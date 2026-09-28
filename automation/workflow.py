"""
Pipeline complet bout-en-bout : topic → vidéo sous-titrée prête à valider.

Étapes :
  1. LLM   : reformulation → script JSON
  2. Images: illustrations Pexels (optionnel, ignoré si indisponible)
  3. TTS   : synthèse vocale → segments MP3 → audio final
  4. Rendu : slides animées + karaoké mot à mot → un clip par slide
  5. Vidéo : concaténation des clips + bande son → MP4 brut
  6. Subs  : incrustation SRT — sautée quand le karaoké est actif, qui la remplace
  7. ⏸   : arrêt pour validation humaine (streamlit run validation/dashboard.py)

La voix est produite avant l'image : c'est elle qui donne la durée de chaque
slide, et donc la synchronisation.

Usage :
    python automation/workflow.py --topic "les transformers"
    python automation/workflow.py --topic "BERT" --voice remy --whisper-model small
    python automation/workflow.py --topic "GANs" --skip-subtitles
"""

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# En deçà, un résumé n'apporte pas de matière : le LLM comblerait le vide en
# inventant, ce qui est précisément ce que la veille doit éviter.
RESUME_MINIMUM = 200

PROCESSED_DIR = Path("data/processed")
AUDIO_DIR     = Path("data/audio")
VIDEOS_DIR    = Path("data/videos")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _step(n: int, label: str) -> None:
    """Affiche un en-tête d'étape visible dans les logs."""
    logger.info("")
    logger.info("═" * 60)
    logger.info("  ÉTAPE %d — %s", n, label)
    logger.info("═" * 60)


@dataclass(frozen=True)
class Sujet:
    """
    Matière envoyée au LLM, quelle que soit son origine.

    Unifie les deux façons de lancer une vidéo — un sujet tapé à la main, ou un
    article remonté par la veille — pour que le reste du pipeline n'ait pas à
    savoir d'où vient le contenu.

    Attributes:
        titre:     Titre de l'article, ou sujet libre
        resume:    Matière réelle ; c'est elle qui détermine la qualité du script
        source:    'manuel', 'veille', 'arxiv', 'huggingface_blog'…
        source_id: Identifiant en base, ou slug du sujet
        articles:  Identifiants des articles consommés, marqués une fois le
                   script produit. Vide pour un sujet libre.
    """

    titre: str
    resume: str
    source: str
    source_id: str
    articles: tuple[str, ...] = ()

    # ── Provenance des figures ────────────────────────────────────────────────
    #
    # Distincte de `source` et `source_id`, qui nomment le sujet et déterminent
    # donc le stem de tous ses artefacts. En multi-sources ils valent « veille »
    # et le slug du thème, alors que les figures viennent d'un article précis.
    # Les confondre obligerait à renommer le sujet pour l'illustrer.
    #
    # Vides pour un article unique : la provenance se confond alors avec la
    # source, et `provenance_figures` retombe dessus.
    source_figures: str = ""
    id_figures: str = ""
    url: str = ""

    def provenance_figures(self) -> tuple[str, str, str]:
        """(source, identifiant, adresse) de l'article dont tirer les figures."""
        return (
            self.source_figures or self.source,
            self.id_figures or self.source_id,
            self.url,
        )

    @property
    def depuis_veille(self) -> bool:
        """Vrai si le sujet s'appuie sur au moins un article collecté."""
        return bool(self.articles)


def sujet_libre(topic: str) -> Sujet:
    """
    Sujet tapé à la main, sans matière documentaire.

    Le LLM puise alors dans sa seule mémoire : c'est le mode le plus exposé à
    l'invention et aux slides de remplissage. Préférer `sujet_depuis_veille`.
    """
    return Sujet(
        titre=topic,
        resume=f"Explique le concept suivant de manière pédagogique : {topic}",
        source="manuel",
        source_id=topic.lower().replace(" ", "_"),
    )


def _filtrer_par_registre(articles: list, registre: str) -> list:
    """
    Restreint une liste d'articles à une famille de sources.

    Le corpus mêle deux registres que rien ne distinguait jusqu'ici : le
    technique (arXiv, HuggingFace) et le sociétal (Montreal AI Ethics, NIST).
    Sans ce filtre le second est noyé — mesuré au moment de l'élargissement,
    10 articles sur 114 — et la fraîcheur, seul critère de tri, ne le fait
    jamais remonter. Choisir son registre est donc la seule façon d'obtenir une
    vidéo qui ne soit pas technique.

    Un registre vide ne filtre rien : c'est le comportement d'avant.
    """
    if not registre:
        return articles

    from scraper.sources import famille

    retenus = [a for a in articles if famille(a.get("source", "")) == registre]
    logger.info(
        "Registre « %s » : %d article(s) retenu(s) sur %d",
        registre, len(retenus), len(articles),
    )
    return retenus


def sujet_depuis_veille(
    source_id: "str | None" = None,
    aleatoire: bool = False,
    registre: str = "",
) -> Sujet:
    """
    Sujet tiré des articles collectés par la veille.

    Args:
        source_id: Article précis ; None = choix automatique
        aleatoire: Tirer au sort au lieu de prendre le plus récent. La fraîcheur
                   fait remonter les mêmes thèmes tant qu'Arxiv publie sur les
                   mêmes sujets ; le tirage puise dans tout le corpus en attente
                   et diversifie la production.
        registre:  'technique', 'societe', ou '' pour ne pas filtrer. Ignoré
                   quand `source_id` désigne déjà un article précis.

    Returns:
        Le sujet correspondant

    Raises:
        RuntimeError: Aucun article exploitable en attente
    """
    import random

    from scraper.storage import get_articles

    if source_id:
        articles = [
            a for a in get_articles(limit=500) if a["source_id"] == source_id
        ]
        if not articles:
            raise RuntimeError(f"Article introuvable en base : {source_id}")
    else:
        # Du plus récent au plus ancien : sans modèle de scoring des sujets, la
        # fraîcheur est le seul critère dont on dispose.
        # Le plafond est relevé quand un registre est demandé : les sources
        # sociétales sont minoritaires, et les 50 plus récents peuvent n'en
        # contenir aucune.
        plafond = 1000 if (aleatoire or registre) else 50
        articles = _filtrer_par_registre(
            get_articles(status="raw", limit=plafond), registre
        )
        if not articles:
            precision = f" pour le registre « {registre} »" if registre else ""
            raise RuntimeError(
                f"Aucun article en attente{precision}. "
                "Lance la veille : make scrape"
            )
        if aleatoire:
            # Mélangé plutôt que tiré une fois : le filtre sur la longueur du
            # résumé peut écarter le premier choix, et il faut alors un suivant
            # lui aussi aléatoire, pas le plus récent des restants.
            articles = list(articles)
            random.shuffle(articles)
            logger.info("Tirage au sort parmi %d articles en attente", len(articles))

    for article in articles:
        resume = (article.get("summary") or "").strip()
        if len(resume) < RESUME_MINIMUM:
            logger.info(
                "Article écarté (résumé de %d caractères) : %s",
                len(resume), article["title"][:60],
            )
            continue
        logger.info("Sujet retenu depuis la veille : %s", article["title"][:70])
        logger.info("  source=%s  id=%s", article["source"], article["source_id"])
        return Sujet(
            titre=article["title"],
            resume=resume,
            source=article["source"],
            source_id=article["source_id"],
            articles=(article["source_id"],),
            url=article.get("url", ""),
        )

    raise RuntimeError(
        f"{len(articles)} article(s) en attente, aucun avec un résumé d'au moins "
        f"{RESUME_MINIMUM} caractères. Un résumé trop court ferait inventer le LLM."
    )


# ── Sélection multi-sources ───────────────────────────────────────────────────
#
# Recherche par mots-clés, et non par embeddings. À l'échelle actuelle du corpus,
# la question que résout un RAG — retrouver le sous-ensemble pertinent quand tout
# ne tient pas dans le contexte — ne se pose pas encore : l'ensemble des résumés
# représente une fraction de la fenêtre des modèles utilisés. Un filtre lisible,
# dont on peut vérifier le résultat à l'œil, vaut mieux ici qu'une similarité
# vectorielle opaque et deux dépendances de plus.
#
# Limite assumée : la correspondance est littérale. « les réseaux de neurones »
# ne trouvera pas « neural networks ». En pratique le vocabulaire technique de ce
# domaine est le même en français et en anglais (transformer, attention, RAG,
# diffusion, embedding), ce qui couvre la majorité des sujets visés.

_MOTS_VIDES = frozenset({
    "les", "des", "une", "aux", "avec", "pour", "dans", "sur", "par", "que",
    "qui", "est", "sont", "the", "and", "for", "with", "from", "this", "that",
    "are", "was", "its", "can", "how", "why", "what", "using", "based",
})

# Un terme présent dans le titre pèse plus que dans le corps : un titre annonce
# le sujet, un corps peut n'y faire qu'une allusion.
_POIDS_TITRE = 3


def _termes(texte: str) -> list[str]:
    """Découpe un texte en termes comparables : minuscules, sans accents ni vides."""
    import unicodedata

    plie = unicodedata.normalize("NFD", texte.lower())
    plie = "".join(c for c in plie if unicodedata.category(c) != "Mn")
    return [
        mot for mot in re.findall(r"[a-z0-9]+", plie)
        if len(mot) > 2 and mot not in _MOTS_VIDES
    ]


def _pertinence(article: dict, termes: list[str]) -> tuple[int, int]:
    """
    Note un article face aux termes recherchés.

    Returns:
        (nombre de termes distincts trouvés, occurrences pondérées)

    Le nombre de termes *distincts* prime sur le total : un article couvrant
    trois notions du sujet est plus pertinent qu'un autre répétant la même
    trente fois — et ce classement ne favorise pas mécaniquement les textes longs.
    """
    titre = " ".join(_termes(article.get("title") or ""))
    corps = " ".join(_termes(article.get("summary") or ""))

    distincts = occurrences = 0
    for terme in termes:
        n = titre.count(terme) * _POIDS_TITRE + corps.count(terme)
        if n:
            distincts += 1
            occurrences += min(n, 12)  # plafonné : une répétition n'est pas une preuve
    return distincts, occurrences


# Poids du lexical et du sémantique dans le score hybride.
#
# Les deux se trompent différemment, d'où l'intérêt de les additionner. Le
# lexical est précis mais littéral : il trouve un nom de modèle ou un acronyme
# exact, et rate « neural networks » quand on cherche « réseaux de neurones ». Le
# sémantique franchit la langue et la reformulation, mais il est bruité —
# vérifié sur le corpus, « transformers et attention » lui fait remonter un
# article de robotique devant celui qui porte « Transformers » dans son titre.
POIDS_LEXICAL = 0.5
POIDS_SEMANTIQUE = 0.5


def _scores_semantiques(topic: str, candidats: list[dict]) -> "dict[str, float]":
    """
    Similarité sémantique entre un thème et chaque candidat.

    Returns:
        {source_id: similarité}, vide si la recherche vectorielle est indisponible

    L'absence de vecteurs n'est pas une erreur : le pipeline retombe sur le seul
    score lexical, comme avant leur introduction. Un corpus non indexé, un modèle
    changé ou fastembed absent ne doivent pas empêcher de produire une vidéo.
    """
    try:
        import numpy as np

        from content.embeddings import decoder, nom_modele, similarites, vectoriser
        from scraper.storage import charger_vecteurs

        modele = nom_modele()
        stockes = charger_vecteurs(modele)
        if not stockes:
            logger.warning(
                "Aucun vecteur en base pour %s — classement lexical seul. "
                "Pour l'activer : uv run python -m content.embeddings --indexer",
                modele,
            )
            return {}

        connus = [a for a in candidats if a["source_id"] in stockes]
        if not connus:
            return {}

        corpus = np.vstack([decoder(stockes[a["source_id"]]) for a in connus])
        requete = vectoriser([topic])[0]
        scores = similarites(requete, corpus)
        logger.info(
            "Similarité sémantique calculée sur %d/%d candidats (%s)",
            len(connus), len(candidats), modele,
        )
        return {a["source_id"]: float(s) for a, s in zip(connus, scores)}

    except Exception as e:
        logger.warning(
            "Recherche sémantique indisponible (%s) — classement lexical seul", e
        )
        return {}


def sujet_multi_sources(topic: str, nb_sources: int = 4, registre: str = "") -> Sujet:
    """
    Compose un sujet à partir des articles les plus proches d'un thème choisi.

    Répond au manque laissé par les deux autres modes : `sujet_libre` laisse
    choisir le sujet mais fait écrire le LLM de mémoire, `sujet_depuis_veille`
    est sourcé mais impose le thème du jour. Ici le thème vient de l'appelant et
    la matière du corpus.

    Args:
        topic:      Thème recherché, en français ou en anglais
        nb_sources: Nombre d'articles à retenir
        registre:   'technique', 'societe', ou '' pour croiser tout le corpus.
                    C'est ici qu'il pèse le plus : un même thème traité par les
                    sources techniques ou par les sources sociétales donne deux
                    vidéos entièrement différentes.

    Returns:
        Un sujet dont le résumé concatène les articles retenus

    Raises:
        RuntimeError: Aucun article ne correspond au thème
    """
    from scraper.storage import get_articles

    termes = _termes(topic)
    if not termes:
        raise RuntimeError(f"Thème inexploitable : « {topic} »")

    candidats = [
        a for a in _filtrer_par_registre(
            get_articles(status="raw", limit=1000), registre
        )
        if len((a.get("summary") or "").strip()) >= RESUME_MINIMUM
    ]
    if not candidats:
        precision = f" dans le registre « {registre} »" if registre else ""
        raise RuntimeError(
            f"Aucun article exploitable en attente{precision}. Lance : make scrape"
        )

    semantique = _scores_semantiques(topic, candidats)
    # Ramené à [0, 1] par le maximum observé : les similarités cosinus de ce
    # modèle plafonnent vers 0,5, les comparer brutes à un taux de couverture
    # lexical écraserait leur contribution.
    plafond = max(semantique.values(), default=0.0) or 1.0

    classement = []
    for article in candidats:
        distincts, occurrences = _pertinence(article, termes)
        lexical = distincts / len(termes)
        proximite = semantique.get(article["source_id"], 0.0)
        score = POIDS_LEXICAL * lexical + POIDS_SEMANTIQUE * (proximite / plafond)
        # Un article que ni les mots-clés ni le sens ne rapprochent du thème est
        # écarté : mieux vaut trois sources justes que quatre dont une hors sujet.
        if distincts or proximite > 0:
            classement.append((score, lexical, proximite, occurrences, article))

    classement.sort(key=lambda x: x[0], reverse=True)
    retenus = classement[:nb_sources]

    if not retenus:
        raise RuntimeError(
            f"Aucun des {len(candidats)} articles ne correspond à « {topic} ». "
            f"Termes cherchés : {', '.join(termes)}. Essaie un terme technique "
            f"anglais (attention, diffusion, embedding), ou produis sans source "
            f"avec --topic seul."
        )

    mode = "hybride" if semantique else "lexical"
    logger.info(
        "Thème « %s » — %d article(s) retenu(s), classement %s :",
        topic, len(retenus), mode,
    )
    for score, lexical, proximite, _occ, a in retenus:
        logger.info(
            "  [score %.3f | lexical %.2f | sens %.3f] %s",
            score, lexical, proximite, a["title"][:58],
        )
    retenus = [(None, a) for *_, a in retenus]

    # Chaque source est annoncée par son titre : le modèle voit des documents
    # distincts et non un bloc de texte indifférencié.
    blocs = [
        f"Source {i} — {a['title']}\n{a['summary'].strip()}"
        for i, (_, a) in enumerate(retenus, start=1)
    ]
    resume = (
        f"Voici {len(blocs)} sources documentaires sur le thème « {topic} ». "
        f"Appuie-toi sur elles, en croisant ce qu'elles apportent.\n\n"
        + "\n\n".join(blocs)
    )

    # Les figures viennent du mieux classé des articles croisés. Le sujet, lui,
    # reste nommé d'après le thème : c'est lui qui donne le stem, et un thème
    # croisant quatre articles ne peut pas s'appeler du nom de l'un d'eux.
    principal = retenus[0][1]

    return Sujet(
        titre=topic,
        resume=resume,
        source="veille",
        source_id=topic.lower().replace(" ", "_"),
        articles=tuple(a["source_id"] for _, a in retenus),
        source_figures=principal.get("source", ""),
        id_figures=principal.get("source_id", ""),
        url=principal.get("url", ""),
    )


def _whisper_available() -> bool:
    """
    Indique si faster-whisper est installé, sans l'importer.

    Vérifié avant de lancer le pipeline : sans ce contrôle, son absence ne se
    manifeste qu'à la dernière étape, après plusieurs minutes de travail et un
    appel LLM facturé.

    Le nom du module est `faster_whisper`, pas `whisper` : ce dernier est le
    paquet d'OpenAI, que le projet n'utilise plus depuis la bascule sur
    CTranslate2. Chercher le mauvais nom désactivait silencieusement karaoké et
    sous-titres, en affirmant dans les logs que la bibliothèque manquait.
    """
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None


# ── Étapes ────────────────────────────────────────────────────────────────────

def step_llm(sujet: "Sujet | str", provider: str | None = None) -> Path:
    """
    Étape 1 : reformulation LLM → JSON script.

    Args:
        sujet:    Sujet à traiter ; une chaîne est acceptée et traitée comme un
                  sujet libre, les DAGs et scripts existants restant valides
        provider: Fournisseur LLM ; None = LLM_PROVIDER dans .env puis défaut

    Returns:
        Chemin du fichier JSON généré
    """
    from content.llm import reformulate_article, save_script

    if isinstance(sujet, str):
        sujet = sujet_libre(sujet)

    # Les figures de l'article sont relevées **avant** la rédaction : le modèle
    # les choisit par indice, sur la seule foi de leurs légendes. Seules les
    # métadonnées circulent ici, le téléchargement a lieu à l'étape des images.
    from scraper.figures import figures_de

    provenance = sujet.provenance_figures()
    figures = figures_de(*provenance)

    script = reformulate_article(
        title=sujet.titre,
        summary=sujet.resume,
        source_id=sujet.source_id,
        source=sujet.source,
        provider=provider,
        figures=figures,
    )
    # Consignée dans le script : l'étape des images relève les figures une
    # seconde fois, pour rester exécutable seule après une reprise par
    # `make render`. Sans elle, un billet de blog serait introuvable — et en
    # multi-sources, `source` nomme le thème, pas l'article qui porte les figures.
    script["provenance_figures"] = dict(
        zip(("source", "source_id", "url"), provenance)
    )

    path = save_script(script, sujet.source_id, sujet.source)
    logger.info("Script LLM : %s", path)

    # L'article sort de la file dès que le script existe : le travail de veille
    # est consommé. Si une étape ultérieure échoue, le script reste sur disque et
    # se reprend avec `make video STEM=<stem>` — inutile de repasser par le LLM.
    if sujet.articles:
        from scraper.storage import update_status

        for source_id in sujet.articles:
            update_status(source_id, "processed")
        logger.info(
            "%d article(s) marqué(s) 'processed' : %s",
            len(sujet.articles), ", ".join(sujet.articles),
        )

    return path


def step_images(script_path: Path) -> "dict[str, Path]":
    """
    Étape 2 : illustrations des slides dépourvues de schéma.

    Le fournisseur est choisi par IMAGE_PROVIDER (pexels ou cloudflare) ; l'étape
    est facultative et n'interrompt jamais le pipeline, une slide sans image
    retombant sur le dégradé.

    Returns:
        Dictionnaire {slide_id: image_path}
    """
    try:
        import json
        from content.images import attacher_figures_source, fetch_slide_images

        with open(script_path, encoding="utf-8") as f:
            script = json.load(f)

        output_dir = Path("data/images") / script_path.stem

        # Les figures de l'article se posent avant les illustrations de fond :
        # elles remplacent le schéma au premier plan, et une slide qui en reçoit
        # une n'a plus besoin qu'on lui cherche une photo. Le script est
        # réécrit sur disque, les indices choisis par le modèle étant résolus en
        # chemins locaux que le rendu saura lire.
        # La provenance est consignée par `step_llm` et non déduite de `source`,
        # qui nomme le thème en multi-sources et non l'article aux figures.
        # Les scripts antérieurs n'ont pas la clé : on retombe sur la source.
        provenance = script.get("provenance_figures") or {
            "source": script.get("source", ""),
            "source_id": script.get("source_id", ""),
            "url": script.get("url", ""),
        }
        posees = attacher_figures_source(
            script, output_dir,
            source=provenance.get("source", ""),
            source_id=provenance.get("source_id", ""),
            url=provenance.get("url", ""),
        )
        if posees:
            with open(script_path, "w", encoding="utf-8") as f:
                json.dump(script, f, ensure_ascii=False, indent=2)

        return fetch_slide_images(script, output_dir)
    except Exception as e:
        logger.warning(
            "Étape images ignorée : %s — les slides garderont leur dégradé", e
        )
        return {}


def step_render(script_path: Path, segments_dir: Path,
                images: "dict[str, Path] | None" = None,
                karaoke: bool = True) -> list[Path]:
    """
    Étape 4 : rendu des slides animées en clips vidéo.

    Passe après la synthèse vocale : chaque clip dure exactement le temps de sa
    narration, ce qui rend la synchronisation acquise par construction.

    Args:
        script_path:  Script JSON du sujet
        segments_dir: Segments MP3, qui donnent la durée de chaque slide
        images:       Illustrations Pexels éventuelles
        karaoke:      Surligner chaque mot au moment où il est prononcé

    Returns:
        Liste ordonnée des clips MP4 muets
    """
    from content.render import rendre_slides

    clips = rendre_slides(script_path, segments_dir, images=images, karaoke=karaoke)
    logger.info("%d clips animés rendus", len(clips))
    return clips


def step_slides(script_path: Path, images: "dict[str, Path] | None" = None) -> list[Path]:
    """
    Génération des slides en PNG statiques.

    Conservée pour l'ancien montage sur images fixes et pour un aperçu rapide
    sans passer par le rendu vidéo, plus long.

    Returns:
        Liste des fichiers PNG générés
    """
    import asyncio as _asyncio
    from content.slides import generate_html_slides
    from content.export import _screenshot_slides

    html_files = generate_html_slides(script_path, images=images)
    logger.info("%d slides HTML générées", len(html_files))

    png_dir = script_path.parent / script_path.stem / "png"
    png_dir.mkdir(parents=True, exist_ok=True)

    png_files = _asyncio.run(_screenshot_slides(html_files, png_dir))
    logger.info("%d PNG exportés dans %s", len(png_files), png_dir)
    return png_files


def step_tts(script_path: Path, voice: str, tts_provider: "str | None" = None) -> tuple[Path, Path]:
    """
    Étape 4 : synthèse vocale.

    Args:
        tts_provider: Moteur TTS (edge, gemini, nvidia) ; None = TTS_PROVIDER dans .env

    Returns:
        (audio_final_mp3, segments_dir)
    """
    from media.tts import process_script

    stem = script_path.stem
    segments_dir = AUDIO_DIR / stem / "segments"

    # La voix vide laisse le moteur choisir la sienne, et le regroupement des
    # appels est décidé dans media/tts.py selon le moteur : workflow.py n'a pas
    # à connaître ces détails.
    final = process_script(script_path, voice=voice, provider=tts_provider)
    logger.info("Audio final : %s", final)
    return final, segments_dir


def step_assembly(clips: list[Path], audio_path: Path, stem: str) -> Path:
    """
    Étape 5 : concaténation des clips animés et ajout de la bande son.

    Returns:
        Chemin du MP4 brut
    """
    from media.assembly import assemble_clips

    output_path = VIDEOS_DIR / f"{stem}_raw.mp4"
    raw_video = assemble_clips(clips, audio_path, output_path)
    logger.info("Vidéo brute : %s", raw_video)
    return raw_video


def step_subtitles(raw_video: Path, whisper_model: str) -> Path:
    """
    Étape 6 : transcription Whisper + burn sous-titres.

    Returns:
        Chemin du MP4 final sous-titré
    """
    from media.subtitles import process_video

    final_video = process_video(raw_video, model=whisper_model)
    logger.info("Vidéo finale : %s", final_video)
    return final_video


# ── Pipeline principal ────────────────────────────────────────────────────────

def run_pipeline(
    topic: "str | None" = None,
    voice: str = "",
    whisper_model: str = "base",
    skip_subtitles: bool = False,
    provider: str | None = None,
    tts_provider: "str | None" = None,
    sujet: "Sujet | None" = None,
) -> Path:
    """
    Lance le pipeline complet, depuis un sujet libre ou un article de la veille.

    Args:
        topic:          Sujet libre (ex: "les transformers") ; ignoré si `sujet`
        voice:          Voix du moteur TTS retenu (edge : vivienne, remy, denise,
                        henri ; gemini : nom de voix, ex. Charon ; nvidia : Louise,
                        Pascal), ou nom complet
        whisper_model:  Modèle Whisper (tiny, base, small, medium)
        skip_subtitles: Sauter l'étape transcription/burn
        provider:       Fournisseur LLM ; None = LLM_PROVIDER dans .env puis défaut
        tts_provider:   Moteur TTS (edge, gemini, nvidia) ; None = TTS_PROVIDER
                        dans .env puis défaut
        sujet:          Sujet déjà résolu — chemin normal depuis la veille

    Returns:
        Chemin de la vidéo finale

    Raises:
        ValueError: Ni `topic` ni `sujet` fourni
    """
    t0 = time.time()

    if sujet is None:
        if not topic:
            raise ValueError("Fournis un sujet libre (topic) ou un Sujet résolu")
        sujet = sujet_libre(topic)

    logger.info("Pipeline démarré pour : « %s »", sujet.titre)
    if sujet.depuis_veille:
        logger.info("Matière : article %s/%s", sujet.source, sujet.source_id)
    else:
        logger.warning(
            "Sujet libre, sans matière documentaire : le LLM écrira de mémoire. "
            "Un article de la veille (--depuis-veille) donne un script mieux fondé."
        )

    # Le client du moteur de voix se charge à l'étape 3, soit après le scraping
    # et un appel LLM facturé. Mesuré le 16/09/2026 : une exécution perdue pour
    # un module absent qu'on pouvait constater en une milliseconde. Le contrôle
    # remonte donc ici, au même endroit que celui de whisper. Il lève au lieu de
    # basculer sur un autre moteur : la voix est un choix éditorial, et en
    # changer d'office fabriquerait une vidéo dans une voix non retenue.
    from media.tts import (
        dependance_manquante,
        message_dependance,
        resoudre_fournisseur,
    )

    moteur_voix = resoudre_fournisseur(tts_provider)
    module_absent = dependance_manquante(moteur_voix)
    if module_absent:
        raise RuntimeError(message_dependance(moteur_voix, module_absent))

    # Contrôle préalable : mieux vaut le savoir maintenant qu'après l'encodage.
    karaoke = _whisper_available()
    if not karaoke:
        logger.warning(
            "faster-whisper n'est pas installé : ni karaoké ni sous-titres. "
            "Pour les activer : make install-subs (ou uv sync --group subtitles)"
        )
        skip_subtitles = True
    elif not skip_subtitles:
        # Le karaoké affiche déjà le texte prononcé, mot par mot et dans le
        # rendu : y superposer un SRT incrusté afficherait deux fois le même
        # texte à l'écran.
        logger.info(
            "Sous-titres karaoké intégrés au rendu — incrustation SRT superflue"
        )
        skip_subtitles = True

    # ── 1. LLM ────────────────────────────────────────────────────────────────
    _step(1, "Reformulation LLM")
    script_path = step_llm(sujet, provider=provider)

    # Le stem est lu sur le fichier produit, jamais reconstruit : les scripts
    # issus de la veille s'appellent « arxiv_2401.12345 » et non « manuel_… ».
    stem = script_path.stem
    logger.info("Stem retenu : %s", stem)

    # ── 2. Images Pexels ──────────────────────────────────────────────────────
    _step(2, "Téléchargement images Pexels")
    images = step_images(script_path)

    # ── 3. TTS ────────────────────────────────────────────────────────────────
    # La voix passe avant l'image : chaque slide doit durer exactement le temps
    # de sa narration, et seule la synthèse peut donner cette durée.
    _step(3, "Synthèse vocale")
    audio_path, segments_dir = step_tts(script_path, voice, tts_provider)

    # ── 4. Rendu animé ────────────────────────────────────────────────────────
    _step(4, "Rendu des slides animées + karaoké (HTML → clips vidéo)")
    clips = step_render(script_path, segments_dir, images=images, karaoke=karaoke)
    if not clips:
        raise RuntimeError("Aucun clip généré à l'étape de rendu")

    # ── 5. Assemblage vidéo ────────────────────────────────────────────────────
    _step(5, "Assemblage des clips + bande son (FFmpeg)")
    raw_video = step_assembly(clips, audio_path, stem)

    # ── 6. Sous-titres ────────────────────────────────────────────────────────
    if skip_subtitles:
        _step(6, "Sous-titres (ignorés)")
        final_video = raw_video
        logger.info("Sous-titres ignorés — vidéo livrée sans incrustation")
    else:
        _step(6, "Transcription Whisper + incrustation des sous-titres")
        final_video = step_subtitles(raw_video, whisper_model)

    # ── Résumé ────────────────────────────────────────────────────────────────
    elapsed = time.time() - t0
    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║  PIPELINE TERMINÉ en %.0fs", elapsed)
    logger.info("║  Vidéo : %s", final_video)
    logger.info("║")
    logger.info("║  ⏸  Lance le dashboard pour valider avant publication :")
    logger.info("║     streamlit run validation/dashboard.py")
    logger.info("╚══════════════════════════════════════════════════════════╝")

    return final_video


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    """Point d'entrée CLI."""
    from content.providers import PROVIDERS
    from media.tts import FOURNISSEURS as TTS_FOURNISSEURS

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Pas de caractère hors cp1252 ici : argparse écrit sur stdout sans repli
    # d'encodage, et --help planterait dès que la sortie est redirigée.
    parser = argparse.ArgumentParser(
        description="Pipeline complet : topic -> vidéo sous-titrée"
    )
    # Trois combinaisons utiles, d'ou l'absence de groupe exclusif :
    #   --topic X                   sujet libre, sans source
    #   --depuis-veille             article le plus recent
    #   --topic X --depuis-veille   articles du corpus portant sur X  <- multi-sources
    parser.add_argument(
        "--topic",
        help="Theme a traiter. Seul : le LLM ecrit de memoire. Combine a "
             "--depuis-veille : les articles du corpus portant sur ce theme",
    )
    parser.add_argument(
        "--depuis-veille", action="store_true",
        help="S'appuyer sur les articles collectes. Sans --topic, prend le plus recent",
    )
    parser.add_argument(
        "--source-id",
        help="Utiliser un article precis de la base (ex: 2401.12345)",
    )
    parser.add_argument(
        "--aleatoire", action="store_true",
        help="Tirer un article au sort dans la file, au lieu du plus recent",
    )
    parser.add_argument(
        "--sources", type=int, default=4, metavar="N",
        help="Nombre d'articles croises en mode multi-sources (defaut: 4)",
    )
    parser.add_argument(
        "--registre", default="", choices=["", "technique", "societe"],
        help="Restreindre a une famille de sources. 'technique' : arXiv et "
             "HuggingFace. 'societe' : Montreal AI Ethics et NIST. Par defaut, "
             "tout le corpus. Sans effet avec --source-id, qui designe deja "
             "un article precis",
    )
    parser.add_argument(
        "--voice", default="",
        help="Voix du moteur TTS retenu (edge : vivienne/remy/denise/henri ; "
             "gemini : ex. Charon ; nvidia : Louise/Pascal)",
    )
    parser.add_argument(
        "--tts-provider", choices=sorted(TTS_FOURNISSEURS),
        help="Moteur TTS (defaut : TTS_PROVIDER dans .env, sinon gemini)",
    )
    parser.add_argument(
        "--whisper-model", default="base",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Modèle Whisper pour la transcription (défaut: base)",
    )
    parser.add_argument(
        "--skip-subtitles", action="store_true",
        help="Sauter la transcription Whisper et le burn des sous-titres",
    )
    parser.add_argument(
        "--provider", choices=sorted(PROVIDERS),
        help="Fournisseur LLM (defaut : LLM_PROVIDER dans .env, sinon mistral)",
    )
    args = parser.parse_args()

    if not (args.topic or args.depuis_veille or args.source_id or args.aleatoire):
        parser.error(
            "fournis --topic, --depuis-veille, ou les deux pour croiser "
            "les articles du corpus sur un theme"
        )

    try:
        if args.source_id:
            sujet = sujet_depuis_veille(args.source_id, registre=args.registre)
        elif args.depuis_veille and args.topic:
            sujet = sujet_multi_sources(
                args.topic, nb_sources=args.sources, registre=args.registre
            )
        elif args.depuis_veille or args.aleatoire:
            sujet = sujet_depuis_veille(
                aleatoire=args.aleatoire, registre=args.registre
            )
        else:
            sujet = sujet_libre(args.topic)
        final = run_pipeline(
            voice=args.voice,
            whisper_model=args.whisper_model,
            skip_subtitles=args.skip_subtitles,
            provider=args.provider,
            tts_provider=args.tts_provider,
            sujet=sujet,
        )
        logger.info("Vidéo prête : %s", final)
        logger.info("Lance le dashboard : streamlit run validation/dashboard.py")
    except Exception as e:
        from scraper.storage import BaseIndisponible

        # Une panne prévue et actionnable (base éteinte, file vide) se lit mieux
        # sans pile d'appels : celle-ci désigne le code alors que la correction
        # est dans l'environnement.
        attendue = isinstance(e, BaseIndisponible) or (
            isinstance(e, RuntimeError) and "article" in str(e).lower()
        )
        logger.error("Pipeline échoué : %s", e, exc_info=not attendue)
        sys.exit(1)


if __name__ == "__main__":
    main()
