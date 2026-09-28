"""
Interface de pilotage : génération, validation et journaux.

Trois onglets, qui suivent l'ordre de travail réel — on génère, on relit, et on
va voir les journaux quand quelque chose cloche.

La génération ne bloque jamais l'interface : les exécutions sont détachées et
suivies par `validation/runs.py`, l'onglet Journaux se contentant de lire les
fichiers produits.

Usage :
    streamlit run validation/dashboard.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

# `streamlit run validation/dashboard.py` place validation/ sur sys.path, pas la
# racine du projet : on l'ajoute pour pouvoir importer les modules du projet.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media.naming import find_video, list_video_stems, script_path
from scraper.sources import LIBELLES_FAMILLE, famille, profil, sources_de
from scraper.storage import get_all_review_statuses, set_review_status
from validation import runs

st.set_page_config(page_title="IA.Maline — pilotage", page_icon="🎬", layout="wide")

STATUS_COLORS = {
    "en_attente": "🟡",
    "approuvée":  "🟢",
    "rejetée":    "🔴",
    "publiée":    "🔵",
}
ETAT_COLORS = {runs.EN_COURS: "🔄", runs.TERMINE: "✅", runs.ECHEC: "❌"}

MODELES_WHISPER = ["tiny", "base", "small", "medium", "large"]


# ── Données ───────────────────────────────────────────────────────────────────

def _load_script(json_path: Path) -> "dict | None":
    """Charge le script JSON si disponible."""
    if json_path.exists():
        try:
            return json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _get_videos() -> list[dict]:
    """
    Retourne toutes les vidéos produites avec leur script et leur statut DB.

    La version sous-titrée est préférée ; la version brute sert de repli pour les
    sujets traités avec --skip-subtitles.
    """
    # Un seul aller-retour en base, quel que soit le nombre de vidéos.
    statuts = get_all_review_statuses()

    videos = []
    for stem in list_video_stems():
        video_path = find_video(stem)
        if video_path is None:
            continue
        json_path = script_path(stem)
        videos.append({
            "stem": stem,
            "video_path": video_path,
            "script_path": json_path,
            "script": _load_script(json_path),
            "status": statuts.get(stem, "en_attente"),
            "subtitled": video_path.stem.endswith("_subtitled"),
            "produite": video_path.stat().st_mtime,
        })

    # De la plus récente à la plus ancienne. `list_video_stems` trie par nom,
    # ce qui mélangeait les productions du jour et celles d'il y a trois
    # semaines : on relit d'abord ce qu'on vient de fabriquer.
    videos.sort(key=lambda v: v["produite"], reverse=True)
    return videos


@st.cache_data(ttl=30)
def _articles_en_attente() -> list[tuple[str, str, str]]:
    """
    Articles exploitables de la file, pour la liste déroulante.

    Le nom de la source accompagne chaque entrée : le corpus mêle désormais du
    technique (arXiv, HuggingFace) à de la critique et de l'institutionnel, et
    un titre seul ne dit plus de quel registre relève l'article.

    Mis en cache : la liste est relue à chaque interaction de l'interface, et
    l'aller-retour en base à chaque frappe rendrait le formulaire poussif.
    """
    from automation.workflow import RESUME_MINIMUM
    from scraper.storage import get_articles

    return [
        (a["source_id"], a["title"], a.get("source") or "")
        for a in get_articles(status="raw", limit=500)
        if len((a.get("summary") or "").strip()) >= RESUME_MINIMUM
    ]


def _afficher_licence(source: str) -> None:
    """
    Rappelle ce que la licence de la source autorise.

    Affiché aux deux bouts de la chaîne — au lancement et à la validation —
    parce que c'est à la publication que l'obligation se matérialise, des
    semaines après la collecte, quand plus personne ne se souvient d'où venait
    l'article. Une source sans licence déclarée n'est pas libre : elle est
    « tous droits réservés » par défaut, et le dire explicitement vaut mieux que
    de laisser un blanc qu'on lira comme une absence de contrainte.
    """
    fiche = profil(source)
    if not fiche.licence:
        st.caption(
            f"{fiche.icone} {fiche.libelle} · registre {fiche.nature} — "
            "aucune licence déclarée, donc tous droits réservés par défaut. "
            "Reformuler les faits, ne pas reprendre la formulation."
        )
        return
    st.caption(
        f"{fiche.icone} {fiche.libelle} · registre {fiche.nature} — "
        f"**{fiche.licence}**. Réutilisation et adaptation permises, "
        "attribution à afficher."
    )


def _resumer_file(registre: str) -> None:
    """
    Ce que le registre choisi laisse réellement disponible.

    Sans ce compte, un registre vide ne se découvre qu'à l'échec de la
    génération, plusieurs minutes plus tard : la file penche lourdement du côté
    technique, et les sources sociétales viennent d'arriver.
    """
    articles = _articles_en_attente()
    disponibles = [a for a in articles if famille(a[2]) == registre]
    noms = " · ".join(profil(s).libelle for s in sources_de(registre))
    if not disponibles:
        st.warning(f"Aucun article en file pour {noms}. Lance une collecte.")
        return
    st.caption(
        f"{len(disponibles)} article(s) sur {len(articles)} — {noms}"
    )


# ── Onglet Génération ─────────────────────────────────────────────────────────

def onglet_generation() -> None:
    """Formulaire de lancement du pipeline."""
    st.subheader("Lancer une génération")

    mode = st.radio(
        "Origine du sujet",
        [
            "Thème + sources croisées",
            "Article le plus récent",
            "Article au hasard",
            "Article précis",
            "Sujet libre, sans source",
        ],
        captions=[
            "Tu choisis le thème, les articles du corpus qui en parlent sont croisés — recommandé",
            "Le dernier article collecté par la veille",
            "Tiré au sort dans la file, pour diversifier les thèmes",
            "Un article désigné dans la liste",
            "Aucune source : le LLM écrit de mémoire et invente plus facilement",
        ],
        horizontal=False,
    )

    commande = ["automation.workflow"]
    libelle = mode

    # Le registre se choisit avant le mode et vaut pour tous ceux qui puisent
    # dans la file. C'est la décision éditoriale du jour — une vidéo technique
    # ou une vidéo de société — et elle précède le choix de l'article, qu'il
    # soit désigné, tiré au sort ou croisé sur un thème.
    registre = ""
    if mode != "Sujet libre, sans source":
        etiquettes = {
            "Tout le corpus": "",
            f"📄🤗 {LIBELLES_FAMILLE['technique']}": "technique",
            f"⚖️🏛️ {LIBELLES_FAMILLE['societe']}": "societe",
        }
        choix_registre = st.radio(
            "Registre", list(etiquettes), horizontal=True,
            help="Technique : papiers de recherche et billets d'ingénierie. "
                 "Société : gouvernance, normes, critique. Un même thème donne "
                 "deux vidéos très différentes selon le registre.",
        )
        registre = etiquettes[choix_registre]
        if registre:
            _resumer_file(registre)

    def avec_registre(base: list[str]) -> list[str]:
        """`--registre` n'est ajouté que s'il restreint quelque chose."""
        return base + (["--registre", registre] if registre else [])

    if mode == "Thème + sources croisées":
        theme = st.text_input("Thème", placeholder="attention, diffusion, embeddings…")
        nb = st.slider("Articles croisés", 1, 8, 4)
        commande = avec_registre(
            commande + ["--topic", theme, "--depuis-veille", "--sources", str(nb)]
        )
        libelle = f"Thème « {theme} » ({nb} sources)"
        pret = bool(theme.strip())

    elif mode == "Article le plus récent":
        commande = avec_registre(commande + ["--depuis-veille"])
        pret = True

    elif mode == "Article au hasard":
        commande = avec_registre(commande + ["--aleatoire"])
        pret = True

    elif mode == "Article précis":
        articles = _articles_en_attente()
        if not articles:
            st.warning("Aucun article exploitable en file. Lance une collecte ci-dessous.")
            pret = False
        else:
            # La file est dominée en nombre par les papiers arXiv — 80 sur 90
            # au moment de l'élargissement — et une source sociétale y serait
            # introuvable à l'œil sans ce filtre.
            visibles = [
                a for a in articles
                if not registre or famille(a[2]) == registre
            ]
            if not visibles:
                st.warning(
                    f"Aucun article en file pour « {LIBELLES_FAMILLE[registre]} ». "
                    "Lance une collecte ci-dessous."
                )
                pret = False
            else:
                libelles = {
                    sid: f"{profil(src).icone} {profil(src).libelle} · {titre[:58]}"
                    for sid, titre, src in visibles
                }
                choix = st.selectbox(
                    f"Article ({len(visibles)} sur {len(articles)} en attente)",
                    options=list(libelles),
                    format_func=libelles.get,
                )
                source_choisie = next(s for i, _, s in visibles if i == choix)
                _afficher_licence(source_choisie)
                commande += ["--source-id", choix]
                libelle = f"Article {choix}"
                pret = True

    else:
        theme = st.text_input("Sujet", placeholder="les réseaux de neurones convolutifs")
        commande += ["--topic", theme]
        libelle = f"Sujet libre « {theme} »"
        pret = bool(theme.strip())
        if theme.strip():
            st.warning(
                "Sans source documentaire, le LLM comble les trous : slides de "
                "remplissage, faits approximatifs. Préfère un thème croisé."
            )

    st.divider()

    # media.tts importe edge_tts et le client gRPC nvidia : chargé ici plutôt
    # qu'en tête de fichier, pour ne pas alourdir chaque page du dashboard.
    from media.tts import voix_disponibles

    col1, col2, col3 = st.columns(3)
    with col1:
        fournisseur = st.selectbox(
            "Fournisseur LLM",
            ["(défaut .env)", "gemini", "mistral", "openrouter", "nvidia", "kimi"],
            help="gemini, mistral, openrouter et nvidia ont un palier gratuit ; kimi est payant",
        )
    with col2:
        # Préfixées par moteur : une voix seule ("Charon") ne dit pas si elle
        # vient de gemini ou d'ailleurs, et les trois moteurs ne partagent aucun
        # nom en commun.
        choix_voix = ["(défaut .env)"] + [
            f"{moteur}:{v}" for moteur, voix in voix_disponibles().items() for v in voix
        ]
        voix_choisie = st.selectbox(
            "Voix", choix_voix,
            help="Moteur:voix — vide = TTS_PROVIDER et sa voix par défaut dans .env",
        )
    with col3:
        whisper = st.selectbox("Modèle de transcription", MODELES_WHISPER, index=1)

    if fournisseur != "(défaut .env)":
        commande += ["--provider", fournisseur]
    if voix_choisie != "(défaut .env)":
        tts_provider, _, voix = voix_choisie.partition(":")
        commande += ["--tts-provider", tts_provider, "--voice", voix]
    commande += ["--whisper-model", whisper]

    with st.expander("Commande correspondante"):
        st.code("uv run python -m " + " ".join(commande), language="bash")

    if st.button("▶ Lancer la génération", type="primary", disabled=not pret,
                 use_container_width=True):
        run_id = runs.lancer(commande, libelle)
        st.success(f"Génération lancée ({run_id}). Suis-la dans l'onglet Journaux.")
        st.session_state["dernier_run"] = run_id

    st.divider()
    st.subheader("Collecte")
    col_a, col_b = st.columns([1, 3])
    with col_a:
        limite = st.number_input("Articles par source", 5, 100, 20, step=5)
    with col_b:
        st.write("")
        if st.button("🔄 Lancer une collecte", use_container_width=True):
            run_id = runs.lancer(
                ["scraper.pipeline", "--source", "all", "--limit", str(limite)],
                f"Collecte ({limite} par source)",
            )
            st.success(f"Collecte lancée ({run_id}).")
            _articles_en_attente.clear()

    # Ce que la file contient réellement, par registre. Le corpus penche
    # lourdement du côté technique — 70 articles arXiv contre 5 critiques au
    # moment de l'élargissement — et ce déséquilibre ne se voit pas autrement
    # qu'ici, alors qu'il décide de ce que les vidéos racontent.
    articles = _articles_en_attente()
    if articles:
        comptes: dict[str, int] = {}
        for _, _, source in articles:
            comptes[source] = comptes.get(source, 0) + 1
        st.caption("File d'attente par source")
        colonnes = st.columns(len(comptes))
        for colonne, (source, nombre) in zip(
            colonnes, sorted(comptes.items(), key=lambda kv: -kv[1])
        ):
            fiche = profil(source)
            colonne.metric(f"{fiche.icone} {fiche.libelle}", nombre,
                           help=f"Registre {fiche.nature}"
                                + (f" · {fiche.licence}" if fiche.licence else ""))


# ── Onglet Validation ─────────────────────────────────────────────────────────

def onglet_validation() -> None:
    """Relecture et approbation des vidéos produites."""
    videos = _get_videos()
    if not videos:
        st.info("Aucune vidéo dans `data/videos/`. Lance une génération.")
        return

    # La date accompagne chaque entrée : sans elle, l'ordre chronologique se
    # devine mal sur des noms de sujets qui ne portent aucune indication de temps.
    par_stem = {v["stem"]: v for v in videos}
    selected_stem = st.selectbox(
        f"Vidéo ({len(videos)}, de la plus récente à la plus ancienne)",
        options=[v["stem"] for v in videos],
        format_func=lambda s: (
            f"{STATUS_COLORS.get(par_stem[s]['status'], '⚪')} "
            f"{datetime.fromtimestamp(par_stem[s]['produite']):%d/%m %H:%M}  —  {s}"
        ),
    )
    video = par_stem[selected_stem]

    col_video, col_info = st.columns([3, 2])

    with col_video:
        st.video(str(video["video_path"]))
        if not video["subtitled"]:
            st.caption("Version brute — le karaoké remplace les sous-titres incrustés.")

        statut = video["status"]
        st.markdown(f"**Statut :** {STATUS_COLORS.get(statut, '⚪')} `{statut}`")

        col_ok, col_ko = st.columns(2)
        with col_ok:
            if st.button("✅ Approuver", use_container_width=True, type="primary"):
                set_review_status(selected_stem, "approuvée")
                st.rerun()
        with col_ko:
            if st.button("❌ Rejeter", use_container_width=True):
                set_review_status(selected_stem, "rejetée")
                st.rerun()

    with col_info:
        script = video["script"]
        if not script:
            st.info("Pas de script JSON associé.")
            return

        st.markdown(f"**{script.get('titre_video', '—')}**")
        provenance = script.get("llm_provider", "?")
        if script.get("llm_replis"):
            provenance += f" (repli depuis {', '.join(script['llm_replis'])})"
        st.caption(f"Écrit par {provenance}")
        # La licence se rappelle ici surtout : c'est l'écran depuis lequel on
        # approuve une vidéo pour publication.
        _afficher_licence(script.get("source", ""))

        if script.get("hook"):
            st.info(script["hook"])
        for slide in script.get("slides", []):
            with st.expander(f"{slide.get('numero', '?')} — {slide.get('titre', '')}"):
                st.write(slide.get("contenu", ""))
                graphique = (slide.get("graphique") or {}).get("type")
                if graphique:
                    st.caption(f"Figure : {graphique}")
        if script.get("conclusion"):
            st.success(script["conclusion"])
        if script.get("hashtags"):
            st.code(" ".join(script["hashtags"]))

    st.divider()
    compte = {"en_attente": 0, "approuvée": 0, "rejetée": 0, "publiée": 0}
    for v in videos:
        compte[v["status"]] = compte.get(v["status"], 0) + 1
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🟡 En attente", compte["en_attente"])
    c2.metric("🟢 Approuvées", compte["approuvée"])
    c3.metric("🔴 Rejetées", compte["rejetée"])
    c4.metric("🔵 Publiées", compte["publiée"])


# ── Onglet Journaux ───────────────────────────────────────────────────────────

@st.fragment(run_every=3)
def _journal_direct(run_id: str) -> None:
    """
    Affiche un journal en se rafraîchissant seul.

    `st.fragment` ne réexécute que ce bloc : sans lui, le rafraîchissement
    rejouerait toute la page, y compris les requêtes en base et le formulaire.
    """
    execution = next((e for e in runs.lister() if e["id"] == run_id), None)
    if execution is None:
        st.warning("Exécution introuvable.")
        return

    statut = execution["statut"]
    entete = f"{ETAT_COLORS.get(statut, '·')} {execution['libelle']} — `{statut}`"
    if execution.get("code") not in (None, 0):
        entete += f" (code {execution['code']})"
    st.markdown(entete)

    contenu = runs.journal(run_id)
    st.code(contenu or "(journal vide, démarrage en cours…)", language="log")


def onglet_journaux() -> None:
    """Journaux d'exécution, pour suivre et déboguer."""
    executions = runs.lister()
    if not executions:
        st.info("Aucune exécution. Lance une génération depuis l'onglet Génération.")
        return

    defaut = st.session_state.get("dernier_run")
    options = [e["id"] for e in executions]
    index = options.index(defaut) if defaut in options else 0

    choix = st.selectbox(
        "Exécution",
        options=options,
        index=index,
        format_func=lambda i: (
            f"{ETAT_COLORS.get(next(e['statut'] for e in executions if e['id'] == i), '·')} "
            f"{i} — {next(e['libelle'] for e in executions if e['id'] == i)}"
        ),
    )

    col_a, col_b = st.columns([4, 1])
    with col_b:
        if st.button("🗑 Supprimer", use_container_width=True):
            runs.supprimer(choix)
            st.rerun()

    _journal_direct(choix)

    en_cours = [e for e in executions if e["statut"] == runs.EN_COURS]
    if en_cours:
        st.caption(f"{len(en_cours)} exécution(s) en cours — rafraîchissement toutes les 3 s.")


# ── Assemblage ────────────────────────────────────────────────────────────────

st.title("🎬 IA.Maline")

generer, valider, journaux = st.tabs(["Génération", "Validation", "Journaux"])
with generer:
    onglet_generation()
with valider:
    onglet_validation()
with journaux:
    onglet_journaux()
