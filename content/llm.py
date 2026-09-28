"""
Reformulation du contenu brut en script pédagogique via un LLM.

Le fournisseur (mistral, gemini, kimi) est choisi via LLM_PROVIDER dans .env
ou l'option --provider — voir content/providers.py.

Usage :
    python -m content.llm --topic "réseaux de neurones"
    python -m content.llm --topic "les transformers" --provider gemini
    python -m content.llm --source-id 2401.12345 --source arxiv
"""

import argparse
import json
import logging
from pathlib import Path

from content.providers import PROVIDERS, call_llm_json
from content.slides import ICONES
from scraper.storage import get_articles, update_status

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path("data/processed")

# Plafond de matière envoyée au LLM. 3000 caractères suffisaient tant qu'une
# vidéo reposait sur un seul résumé ; en mode multi-sources, cette limite
# tronquait tout après la première source et annulait l'intérêt du croisement.
# 24 000 caractères ≈ 6 000 tokens, soit une fraction du contexte de tous les
# fournisseurs configurés (128 000 pour le plus étroit).
RESUME_MAX = 24_000

SYSTEM_PROMPT = """Tu es un créateur de contenu pédagogique spécialisé en IA et Tech.
Tu vulgarises des concepts techniques pour le grand public, avec un ton accessible et enthousiaste.

Règles absolues :
- Pas d'équations mathématiques complexes
- Maximum 1 concept clé par slide
- Analogies du quotidien obligatoires
- Langage simple, phrases courtes
- Ton : enthousiaste, factuel, bienveillant
- Langue : français uniquement
"""

SLIDE_PROMPT = """À partir du résumé suivant, crée un script de vidéo courte (TikTok/Reels) en français.

Titre du papier/article : {title}
Résumé : {summary}

Génère exactement ce JSON (sans markdown, sans backticks) :
{{
  "titre_video": "Titre accrocheur max 60 caractères",
  "introduction": "Annonce du sujet, 2 phrases max, ton chaleureux. Ex: 'Salut à tous ! Aujourd'hui on décortique ensemble ...'",
  "hook": "Deux phrases, la première chose entendue, avant la salutation : une accroche qui capte en 3 secondes, puis une raison de rester jusqu'au bout. Voir la section « Accroche » plus bas, elle est impérative",
  "slides": [
    {{
      "numero": 1,
      "titre": "Titre de la slide (max 8 mots)",
      "contenu": "Texte lu à voix haute (max 30 mots, simple, vulgarisé)",
      "visuel": "Emoji représentatif du concept",
      "graphique": {{
        "type": "stat|etapes|couches|comparaison|liste|neurones|cycle|matrice|frise|embranchement|venn|figure_source",
        "donnees": {{}}
      }},
      "mots_cles_image": "2-3 mots-clés anglais pour chercher une photo illustrative (ex: neural network brain)",
      "illustration": "Visuel abstrait évoquant le concept de la slide, en anglais, TOUJOURS renseigné. Décris une atmosphère et des matières, jamais une scène ni un objet reconnaissable (ex: 'converging warm gradients over deep space, sense of transformation'). Ne mentionne ni texte, ni schéma, ni réseau de points reliés."
    }}
  ],
  "conclusion": "Phrase de conclusion + appel à l'action (like, partage, follow)",
  "hashtags": ["#IA", "#DataScience", "..."],
  "niveau_difficulte": "débutant|intermédiaire",
  "duree_estimee_secondes": 90,
  "mots_cles_image_titre": "2-3 mots-clés anglais pour l'image de la slide titre",
  "illustration_titre": "Composition abstraite à générer pour la slide titre, en anglais. Décris des formes et une atmosphère, jamais du texte ni un visage réaliste (ex: 'converging luminous lines over deep space, sense of emergence')",
  "termes_anglais": ["Transformer", "BERT", "token"]
}}

Le champ "termes_anglais" doit lister tous les mots ou expressions d'origine anglaise présents dans le script
(noms de modèles, termes techniques IA/ML, acronymes) qui doivent être prononcés en anglais par la voix TTS.

Types de graphiques disponibles — choisis le plus adapté au concept :
- "stat"       : un chiffre clé   → donnees: {{"valeur": "95%", "description": "de précision"}}
                 Écris la valeur en pourcentage dès que la donnée en est un :
                 l'anneau du schéma se remplit alors jusqu'à cette part et montre
                 le chiffre au lieu de l'encadrer. Une quantité sans total
                 ("175 Md de paramètres", "x3 plus rapide") s'écrit telle quelle.
- "etapes"     : un processus, une suite dans le temps
                 → donnees: {{"items": [{{"texte": "Données", "icone": "donnees"}}, {{"texte": "Entraînement", "icone": "reseau"}}, {{"texte": "Prédiction", "icone": "cible"}}]}}
- "couches"    : une superposition, une architecture en niveaux (du haut vers le bas)
                 → donnees: {{"items": [{{"texte": "Sortie", "icone": "cible"}}, {{"texte": "Attention", "icone": "reseau"}}, {{"texte": "Encodage", "icone": "donnees"}}, {{"texte": "Entrée", "icone": "donnees"}}]}}
- "comparaison": un écart chiffré → donnees: {{"items": [{{"label": "IA", "valeur": 92}}, {{"label": "Humain", "valeur": 78}}]}}
                 N'utilise ce type que si les valeurs sont réellement différentes
                 et quantifiables. Deux notions non chiffrables (un faussaire et
                 un détective) ne se comparent pas : c'est "etapes" ou "couches".
- "liste"      : des points clés sans ordre
                 → donnees: {{"items": [{{"texte": "Point A", "icone": "ampoule"}}, {{"texte": "Point B"}}, {{"texte": "Point C"}}]}}
- "neurones"   : réseau de neurones → donnees: {{"couches": [3, 4, 2]}}
- "cycle"      : un processus qui boucle sur lui-même (itération, cycle de vie,
                 rétroaction) → donnees: {{"items": [{{"texte": "Collecte", "icone": "donnees"}}, {{"texte": "Entraînement", "icone": "reseau"}}, {{"texte": "Évaluation", "icone": "cible"}}, {{"texte": "Ajustement", "icone": "engrenage"}}]}}
                 N'utilise ce type que si le processus revient réellement à son
                 point de départ. Un processus qui se termine reste "etapes".
                 3 à 5 items.
- "matrice"    : un croisement de deux axes indépendants (grille 2x2)
                 → donnees: {{"axe_x": "Rapidité", "axe_y": "Précision", "quadrants": [
                     {{"texte": "Rapide et précis", "icone": "cible"}},
                     {{"texte": "Rapide mais imprécis"}},
                     {{"texte": "Lent mais précis"}},
                     {{"texte": "Lent et imprécis"}}
                   ]}}
                 Toujours exactement 4 quadrants, dans cet ordre : haut-droite
                 (les deux axes au maximum), haut-gauche, bas-droite, bas-gauche.
                 N'utilise ce type que si les deux axes sont vraiment indépendants
                 l'un de l'autre — un seul axe qui varie, c'est "comparaison".
- "frise"      : une chronologie, des jalons portant chacun une date
                 → donnees: {{"items": [
                     {{"date": "2017", "texte": "Le Transformer"}},
                     {{"date": "2020", "texte": "GPT-3", "icone": "cerveau"}},
                     {{"date": "2024", "texte": "Modeles de raisonnement", "icone": "ampoule"}}
                   ]}}
                 Chaque item DOIT porter une date, sinon utilise "etapes" : une
                 étape est un rang dans une suite, un jalon est situé dans le
                 temps. 3 à 5 items, dans l'ordre chronologique.
- "embranchement" : une question qui ouvre plusieurs suites
                 → donnees: {{"question": "Les donnees sont-elles etiquetees ?",
                     "branches": [
                       {{"texte": "Apprentissage supervise", "icone": "cible"}},
                       {{"texte": "Apprentissage non supervise", "icone": "reseau"}}
                     ]}}
                 2 ou 3 branches, et une vraie question fermée. Une suite qui ne
                 bifurque pas est "etapes".
- "venn"       : deux notions qui se recouvrent, et ce qu'elles partagent
                 → donnees: {{"gauche": "Intelligence artificielle",
                              "droite": "Statistiques",
                              "commun": "Apprendre des donnees"}}
                 N'utilise ce type que si les deux notions se chevauchent
                 réellement. Deux notions qui s'opposent, c'est "comparaison" ;
                 deux notions dont l'une contient l'autre, c'est "couches".
                 Garde "commun" court : il s'inscrit dans une zone étroite.
- "figure_source" : une figure tirée de l'article lui-même
                 → donnees: {{"indice": 0}}
                 L'indice désigne une figure de la liste ci-dessous, et rien
                 d'autre. N'invente ni légende ni crédit : ils viennent de
                 l'article et sont ajoutés automatiquement.
                 **Préfère ce type à un schéma inventé** quand une figure montre
                 vraiment ce dont parle la slide : un schéma reconstitué de
                 mémoire vaut moins que celui des auteurs. Mais écarte les
                 captures d'interface denses et les tableaux chargés, illisibles
                 sur un téléphone. Une même figure ne sert qu'une fois.

{figures}

Pour "etapes", "couches", "liste", "cycle" et "frise", chaque item peut être un
simple texte ou {{"texte": "...", "icone": "..."}} — de même pour chaque quadrant
de "matrice" et chaque branche d'"embranchement". L'icône est facultative : un
pictogramme à choisir dans cette liste, et dans aucune autre — __ICONES__.
N'en mets pas si aucune ne correspond vraiment au concept : une icône hors sujet
nuit plus qu'elle n'aide.

Le champ "illustration" est indépendant du graphique : il décrit le fond, le
graphique occupe le premier plan. Les deux coexistent, donc l'illustration ne
doit jamais comporter de formes qu'on pourrait confondre avec un schéma.

Choisis "liste" uniquement si aucun autre type ne convient : c'est le seul qui
n'illustre rien. Préfère systématiquement une figure qui montre une relation —
une suite ("etapes"), une superposition ("couches"), un écart ("comparaison"),
une boucle ("cycle"), un croisement ("matrice").
Ne dépasse pas 4 éléments par figure (5 pour "cycle", toujours 4 pour
"matrice"), et garde les libellés à 3 mots maximum : au-delà, le texte déborde
du schéma.

Génère entre 4 et 8 slides. Chaque slide = 1 concept clé vulgarisé avec une
analogie concrète.

Repère de durée : la voix de synthèse débite environ 2,5 mots par seconde. Un
script de 200 mots donne donc une vidéo d'environ 80 secondes.

Accroche — c'est la seule phrase que tous les spectateurs entendent :
- N'OUVRE PAS par "Et si". C'est devenu le réflexe, et deux vidéos qui
  commencent pareil se confondent dans un fil. Écarte aussi "Imaginez" et
  "Saviez-vous que" pour la même raison. Ouvre autrement, et pas de la même
  façon d'une vidéo à l'autre : une affirmation qui dérange, un chiffre brut
  posé sans préambule, une idée reçue que tu démontes, une question directe,
  une situation du quotidien, ce que le sujet appelle.
- La seconde phrase donne une raison de rester, et cette raison est précise :
  elle nomme ce que la vidéo révèle plus loin sans le livrer — "le chiffre de
  la fin est celui que personne n'attend", "la troisième limite change tout".
  "Restez jusqu'à la fin" seul ne promet rien et tombe sous la règle des
  formules creuses ci-dessous.
- Ce que tu promets doit exister dans les slides qui suivent et dans le résumé.
  Annoncer une révélation absente est le seul manquement que le spectateur
  repère à coup sûr.

Exactitude — ces règles priment sur tout le reste, y compris le nombre de slides :
- LE RÉSUMÉ CI-DESSUS EST TA SEULE SOURCE. Reformule-le entièrement à ta manière :
  tes analogies, ton ton, ton découpage, tes titres. La forme t'appartient. Le
  fond, non. Chaque fait, chiffre, nom de modèle, date, résultat ou limite que tu
  écris doit se retrouver dans le résumé. Si tu ne peux pas le pointer du doigt
  dans le texte ci-dessus, ne l'écris pas — même si tu le sais par ailleurs, même
  si c'est vrai, même si cela rendrait la slide plus impressionnante.
- Si le résumé ne contient aucune matière factuelle, explique le principe en
  restant général. N'invente alors aucun détail précis pour combler le vide : pas
  de chiffre, pas de nom de modèle, pas de date, pas de jeu de données.
- N'annonce jamais comme futur ce qui existe déjà (traduire, résumer, coder…).
- Bannis les formules creuses : "les possibilités sont infinies", "révolutionner",
  "changer le monde". Une slide sans contenu propre doit être supprimée.
- Mieux vaut 4 slides denses que 8 dont deux répètent les autres. Si le résumé ne
  porte pas huit idées distinctes, produis-en moins.
"""

# Injecté après coup, et non via .format(), pour ne pas interférer avec les
# accolades JSON du prompt (échappées en {{ }}) ni avec le .format(title=...,
# summary=...) appliqué plus bas à chaque appel. La liste vient de ICONES
# (content/slides.py) : c'est le même vocabulaire qui rend le pictogramme, donc
# aucun risque que le LLM propose une icône que le renderer ne connaît pas.
SLIDE_PROMPT = SLIDE_PROMPT.replace("__ICONES__", ", ".join(sorted(ICONES)))


def _bloc_figures(figures: "list | None") -> str:
    """
    Catalogue des figures de l'article, tel que le prompt le présente au modèle.

    Le modèle choisit **par indice**, sur la seule foi des légendes : il ne voit
    aucune image. Sans figure disponible, le type est explicitement fermé —
    laisser la porte ouverte le ferait désigner des indices imaginaires, que
    `content/images.py` devrait ensuite écarter slide par slide.
    """
    if not figures:
        return ('Aucune figure n\'accompagne cet article : le type '
                '"figure_source" est indisponible, ne le choisis pas.')

    lignes = [
        f"Figures disponibles dans l'article, à désigner par leur indice avec "
        f'le type "figure_source" :'
    ]
    for i, figure in enumerate(figures):
        legende = getattr(figure, "legende", "") or "(sans légende)"
        lignes.append(f"  {i} — {legende}")
    return "\n".join(lignes)


def reformulate_article(
    title: str,
    summary: str,
    source_id: str,
    source: str,
    provider: str | None = None,
    figures: "list | None" = None,
) -> dict:
    """
    Envoie un article au LLM pour reformulation en script de vidéo pédagogique.

    Args:
        title:     Titre de l'article original
        summary:   Résumé de l'article
        source_id: Identifiant unique de la source
        source:    Nom de la source (arxiv, huggingface_blog, ...)
        provider:  Fournisseur LLM ; None = LLM_PROVIDER puis défaut

    Returns:
        Dictionnaire contenant le script structuré pour la vidéo
    """
    prompt = SLIDE_PROMPT.format(
        title=title, summary=summary[:RESUME_MAX], figures=_bloc_figures(figures)
    )
    logger.info("Reformulation de : %s", title[:60])

    # La trace dit quel fournisseur a *réellement* répondu : après un repli
    # automatique, ce n'est plus celui demandé.
    trace: dict = {}
    # Le contrat minimal d'un script : sans l'un de ces champs, la vidéo ne peut
    # pas se monter. Le déclarer ici fait échouer le fournisseur fautif et laisse
    # la chaîne de repli tenter le suivant, au lieu d'écrire un script vide.
    script = call_llm_json(
        SYSTEM_PROMPT, prompt, provider=provider, trace=trace,
        champs_requis=("titre_video", "hook", "slides", "conclusion"),
    )

    script["source_id"] = source_id
    script["source"] = source
    script["titre_original"] = title
    # Traçabilité : indispensable pour comparer la qualité entre fournisseurs
    script["llm_provider"] = trace["provider"]
    script["llm_model"] = trace["model"]
    if trace.get("replis"):
        script["llm_replis"] = trace["replis"]

    return script


def save_script(script: dict, source_id: str, source: str, suffix: str = "") -> Path:
    """
    Sauvegarde le script JSON dans data/processed/.

    Args:
        script:    Script structuré à sauvegarder
        source_id: Identifiant de la source
        source:    Nom de la source
        suffix:    Suffixe optionnel ajouté au nom de fichier — sert à comparer
                   plusieurs fournisseurs sur un même sujet sans les écraser

    Returns:
        Chemin du fichier JSON sauvegardé
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = source_id.replace("/", "_")
    stem = f"{source}_{safe_id}" + (f"_{suffix}" if suffix else "")
    output_path = PROCESSED_DIR / f"{stem}.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    logger.info("Script sauvegardé : %s", output_path)
    return output_path


def process_pending_articles(limit: int = 5, provider: str | None = None) -> list[Path]:
    """
    Traite les articles en statut 'raw' et les reformule via le LLM configuré.

    Args:
        limit:    Nombre maximum d'articles à traiter en une passe
        provider: Fournisseur LLM ; None = LLM_PROVIDER puis défaut

    Returns:
        Liste des fichiers JSON générés
    """
    articles = get_articles(status="raw", limit=limit)
    if not articles:
        logger.info("Aucun article en attente de traitement")
        return []

    logger.info("%d articles à traiter", len(articles))
    outputs: list[Path] = []

    for article in articles:
        source_id = article["source_id"]
        source = article["source"]
        try:
            script = reformulate_article(
                title=article["title"],
                summary=article["summary"] or "",
                source_id=source_id,
                source=source,
                provider=provider,
            )
            path = save_script(script, source_id, source)
            outputs.append(path)
            update_status(source_id, "processed")
        except Exception as e:
            logger.error("Échec traitement %s : %s", source_id, e)

    return outputs


def main() -> None:
    """Point d'entrée CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Reformulation LLM (Claude, Gemini ou Mistral)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--topic", help="Sujet libre à reformuler directement")
    group.add_argument("--source-id", help="ID d'un article déjà en base")
    parser.add_argument("--source", default="arxiv", help="Source de l'article (avec --source-id)")
    parser.add_argument("--limit", type=int, default=5, help="Nb articles pending à traiter")
    parser.add_argument(
        "--provider",
        choices=sorted(PROVIDERS),
        help="Fournisseur LLM (défaut : LLM_PROVIDER dans .env). Le nom est ajouté "
             "au fichier de sortie pour comparer plusieurs fournisseurs sans écraser",
    )
    args = parser.parse_args()

    # Le suffixe nomme le fournisseur ayant répondu, pas celui demandé : après un
    # repli, un fichier « ..._gemini.json » écrit par Mistral fausserait toute
    # comparaison. Il n'est ajouté que si un fournisseur a été demandé
    # explicitement — le pipeline standard garde ses noms habituels.
    def _suffix(script: dict) -> str:
        return script["llm_provider"] if args.provider else ""

    if args.topic:
        # Mode sujet libre : on crée un faux article avec le topic comme résumé
        topic_id = args.topic.lower().replace(" ", "_")
        script = reformulate_article(
            title=args.topic,
            summary=f"Explique le concept suivant de manière pédagogique : {args.topic}",
            source_id=topic_id,
            source="manuel",
            provider=args.provider,
        )
        path = save_script(script, topic_id, "manuel", suffix=_suffix(script))
        print(f"Script généré : {path}  [{script['llm_provider']}/{script['llm_model']}]")

    elif args.source_id:
        articles = get_articles(source=args.source, limit=100)
        article = next((a for a in articles if a["source_id"] == args.source_id), None)
        if not article:
            logger.error("Article non trouvé : %s", args.source_id)
            return
        script = reformulate_article(
            title=article["title"],
            summary=article["summary"] or "",
            source_id=args.source_id,
            source=args.source,
            provider=args.provider,
        )
        path = save_script(script, args.source_id, args.source, suffix=_suffix(script))
        print(f"Script généré : {path}  [{script['llm_provider']}/{script['llm_model']}]")

    else:
        # Mode batch : traite les articles en attente
        paths = process_pending_articles(limit=args.limit, provider=args.provider)
        print(f"{len(paths)} scripts générés")


if __name__ == "__main__":
    main()
