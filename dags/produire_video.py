"""
Production d'une vidéo, du sujet au MP4 sous-titré, avec arrêt sur validation.

Déclenché à la demande avec des paramètres (sujet, fournisseur LLM, voix), ce DAG
appelle les mêmes fonctions que `automation/workflow.py` : la logique métier reste
dans les modules du projet, le DAG n'orchestre que l'enchaînement.

Deux apports par rapport à l'exécution séquentielle locale :
  - illustrations et synthèse vocale tournent en parallèle ;
  - la validation humaine bloque la publication sans mobiliser de worker.

L'ordre voix → rendu n'est pas négociable : chaque slide animée dure exactement
le temps de sa narration, durée que seule la synthèse vocale peut fournir.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.sdk import Param, dag, task

DOC = __doc__

VOIX = ["vivienne", "remy", "denise", "henri"]
MODELES_WHISPER = ["tiny", "base", "small", "medium"]


@dag(
    dag_id="produire_video",
    description="Sujet -> script -> slides + voix -> montage -> validation -> publication",
    doc_md=DOC,
    schedule=None,  # déclenchement manuel, ou par un futur DAG de sélection
    start_date=pendulum.datetime(2026, 1, 1, tz="Europe/Paris"),
    catchup=False,
    tags=["production", "video"],
    params={
        "depuis_veille": Param(
            True,
            type="boolean",
            title="Prendre un article de la veille",
            description="Consomme l'article le plus récent collecté par "
                        "veille_quotidienne. C'est le mode normal : le script "
                        "s'appuie alors sur une source réelle. Décoche pour "
                        "traiter le sujet libre ci-dessous.",
        ),
        "source_id": Param(
            "",
            type="string",
            title="Article précis (facultatif)",
            description="Identifiant d'un article en base (ex: 2401.12345). "
                        "Vide = le plus récent non traité.",
        ),
        "topic": Param(
            "",
            type="string",
            title="Thème",
            description="Vide + veille cochée = l'article le plus récent. "
                        "Rempli + veille cochée = les articles du corpus portant "
                        "sur ce thème, croisés (mode recommandé). "
                        "Rempli + veille décochée = le LLM écrit de mémoire.",
        ),
        "sources": Param(
            4,
            type="integer",
            minimum=1,
            maximum=8,
            title="Articles croisés",
            description="Nombre d'articles retenus en mode thème + veille.",
        ),
        "provider": Param(
            "gemini",
            enum=["gemini", "mistral", "openrouter", "kimi"],
            title="Fournisseur LLM",
        ),
        "voice": Param(
            "",
            type="string",
            title="Voix de synthèse",
            description="Vide = voix par défaut du moteur (Charon pour Gemini). "
                        f"Moteur edge : {', '.join(VOIX)}.",
        ),
        "whisper_model": Param(
            "base", enum=MODELES_WHISPER, title="Modèle de transcription"
        ),
        "karaoke": Param(
            True,
            type="boolean",
            title="Surlignage karaoké",
            description="Allume chaque mot au moment où il est prononcé. "
                        "Remplace l'incrustation SRT, qui ferait doublon.",
        ),
    },
    default_args={
        # Appels réseau (LLM, TTS) : une erreur transitoire ne doit pas perdre le run.
        "retries": 2,
        "retry_delay": timedelta(minutes=2),
    },
)
def produire_video():

    @task
    def generer_script(**context) -> str:
        """
        Étape 1 : reformulation LLM du sujet en script structuré.

        C'est ici que les deux DAGs se rejoignent : `veille_quotidienne` remplit
        la file d'articles, celui-ci en consomme un. Le sujet libre reste
        disponible, mais produit un script sans source.
        """
        from automation.workflow import (
            step_llm, sujet_depuis_veille, sujet_libre, sujet_multi_sources,
        )

        params = context["params"]
        source_id = (params.get("source_id") or "").strip()
        topic = (params.get("topic") or "").strip()
        veille = params.get("depuis_veille")

        if source_id:
            sujet = sujet_depuis_veille(source_id)
        elif veille and topic:
            sujet = sujet_multi_sources(topic, nb_sources=params.get("sources", 4))
        elif veille:
            sujet = sujet_depuis_veille()
        else:
            sujet = sujet_libre(topic)

        return str(step_llm(sujet, provider=params["provider"]))

    @task
    def valider_script(chemin_script: str) -> str:
        """
        Porte de qualité : un script incomplet échoue ici, en une fraction de
        seconde, plutôt qu'après la synthèse vocale et l'encodage.
        """
        import json

        obligatoires = ("titre_video", "hook", "slides", "conclusion")
        script = json.loads(open(chemin_script, encoding="utf-8").read())

        manquants = [c for c in obligatoires if not script.get(c)]
        if manquants:
            raise ValueError(f"Script incomplet, champs manquants : {manquants}")

        nb = len(script["slides"])
        if not 3 <= nb <= 12:
            raise ValueError(f"Nombre de slides hors bornes : {nb}")

        for slide in script["slides"]:
            if not slide.get("contenu"):
                raise ValueError(f"Slide {slide.get('numero')} sans contenu")

        return chemin_script

    @task
    def telecharger_illustrations(chemin_script: str) -> dict:
        """Étape 2 : illustrations Pexels (parallèle à la voix, sans effet si pas de clé)."""
        from pathlib import Path

        from automation.workflow import step_images

        return {k: str(v) for k, v in step_images(Path(chemin_script)).items()}

    @task
    def generer_voix(chemin_script: str, **context) -> dict:
        """Étape 3 : synthèse vocale segment par segment."""
        from pathlib import Path

        from automation.workflow import step_tts

        audio, segments = step_tts(Path(chemin_script), context["params"]["voice"])
        return {"audio": str(audio), "segments": str(segments)}

    @task
    def rendre(chemin_script: str, voix: dict, illustrations: dict, **context) -> list[str]:
        """
        Étape 4 : rendu des slides animées, karaoké compris.

        Dépend de la voix : chaque clip dure exactement le temps de sa narration,
        et le karaoké a besoin de l'horodatage des mots. C'est la raison pour
        laquelle image et son ne sont plus parallèles.
        """
        from pathlib import Path

        from automation.workflow import step_render

        clips = step_render(
            Path(chemin_script),
            Path(voix["segments"]),
            images={k: Path(v) for k, v in illustrations.items()},
            karaoke=context["params"]["karaoke"],
        )
        return [str(c) for c in clips]

    @task
    def assembler(clips: list[str], voix: dict, chemin_script: str) -> str:
        """Étape 5 : concaténation des clips et ajout de la bande son."""
        from pathlib import Path

        from automation.workflow import step_assembly

        stem = Path(chemin_script).stem
        video = step_assembly([Path(c) for c in clips], Path(voix["audio"]), stem)
        return str(video)

    @task
    def sous_titrer(video: str, **context) -> str:
        """
        Étape 6 : incrustation SRT, uniquement si le karaoké est désactivé.

        Les deux afficheraient le même texte simultanément.
        """
        import logging
        from pathlib import Path

        from automation.workflow import step_subtitles

        params = context["params"]
        if params["karaoke"]:
            logging.getLogger(__name__).info(
                "Karaoké actif : incrustation SRT sautée, elle ferait doublon"
            )
            return video
        return str(step_subtitles(Path(video), params["whisper_model"]))

    @task.sensor(poke_interval=60, timeout=60 * 60 * 72, mode="reschedule")
    def attendre_validation(video: str) -> bool:
        """
        Étape 6 : attend la décision humaine prise dans le dashboard Streamlit.

        En mode `reschedule`, la tâche libère son slot entre deux vérifications :
        une vidéo peut rester trois jours en attente sans bloquer le scheduler.
        """
        import logging
        from pathlib import Path

        from airflow.exceptions import AirflowSkipException

        from media.naming import stem_from_video
        from scraper.storage import get_review_status

        statut = get_review_status(stem_from_video(Path(video)))
        logging.getLogger(__name__).info("Statut de validation : %s", statut)

        if statut == "rejetée":
            # Un rejet est une issue normale, pas une panne : on saute la
            # publication sans marquer le run en échec.
            raise AirflowSkipException("Vidéo rejetée à la validation humaine")
        return statut in ("approuvée", "publiée")

    @task
    def publier(video: str) -> str:
        """Étape 7 : publication TikTok en privé (SELF_ONLY)."""
        from pathlib import Path

        from media.naming import stem_from_video
        from publish.tiktok import publish_video
        from scraper.storage import mark_published

        chemin = Path(video)
        stem = stem_from_video(chemin)
        publish_id = publish_video(chemin, stem.replace("_", " "), privacy="SELF_ONLY")
        mark_published(stem)
        return publish_id

    script = valider_script(generer_script())
    # Illustrations et voix ne dépendent que du script : lancées en parallèle.
    # Le rendu, lui, attend la voix pour connaître la durée de chaque slide.
    illustrations = telecharger_illustrations(script)
    voix = generer_voix(script)
    clips = rendre(script, voix, illustrations)
    video = sous_titrer(assembler(clips, voix, script))
    attendre_validation(video) >> publier(video)


produire_video()
