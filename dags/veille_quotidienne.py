"""
Veille quotidienne : collecte les sources ouvertes et alimente la file de sujets.

Ce DAG ne produit **aucune vidéo** — il remplit seulement la table `articles`.
Produire une vidéo pour chaque article coûterait des appels LLM et générerait du
remplissage ; le choix des sujets reste une décision (humaine aujourd'hui, assistée
par un modèle de scoring plus tard).

Chaque source est une tâche indépendante : une panne d'Arxiv n'empêche pas la
collecte de HuggingFace, et chaque source a ses propres tentatives.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.sdk import dag, task

DOC = __doc__


@dag(
    dag_id="veille_quotidienne",
    description="Collecte quotidienne des sources ouvertes IA/ML",
    doc_md=DOC,
    schedule="0 6 * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz="Europe/Paris"),
    catchup=False,
    max_active_runs=1,
    tags=["veille", "scraping"],
    default_args={
        # Les sources sont des services distants : on retente avant d'échouer.
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
    },
)
def veille_quotidienne():

    @task
    def lister_sources() -> list[str]:
        """Noms des sources déclarées dans le registre du scraper."""
        from scraper.sources import SOURCES

        return sorted(SOURCES)

    @task
    def collecter(source: str, limite: int = 20) -> dict:
        """
        Collecte une source et enregistre les nouveaux articles.

        Returns:
            {'source': nom, 'nouveaux': nombre d'articles inédits}
        """
        import logging

        from scraper.sources import SOURCES
        from scraper.storage import save_article

        logger = logging.getLogger(__name__)
        nouveaux = 0

        for item in SOURCES[source](limit=limite):
            if save_article(
                source=item["source"],
                source_id=item["source_id"],
                title=item["title"],
                url=item["url"],
                summary=item["summary"],
                published=item.get("published"),
                tags=item.get("tags"),
                raw_data=item.get("raw"),
            ):
                nouveaux += 1

        logger.info("%s : %d nouveaux articles", source, nouveaux)
        return {"source": source, "nouveaux": nouveaux}

    @task
    def recapituler(resultats: list[dict]) -> str:
        """Journalise le bilan de la collecte et la taille de la file d'attente."""
        import logging

        from scraper.storage import get_articles

        logger = logging.getLogger(__name__)
        total = sum(r["nouveaux"] for r in resultats)
        for r in sorted(resultats, key=lambda x: -x["nouveaux"]):
            logger.info("  %-20s %d", r["source"], r["nouveaux"])

        en_attente = len(get_articles(status="raw", limit=1000))
        logger.info("Total : %d nouveaux — %d articles en attente de traitement",
                    total, en_attente)
        return f"{total} nouveaux, {en_attente} en attente"

    # Une tâche par source, exécutées en parallèle et isolées les unes des autres.
    recapituler(collecter.expand(source=lister_sources()))


veille_quotidienne()
