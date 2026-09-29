"""
Orchestration du scraping : lance les sources, déduplique et stocke les résultats.

Usage :
    python scraper/pipeline.py --source all --limit 20
    python scraper/pipeline.py --source arxiv --limit 5
"""

import argparse
import logging
import sys
from typing import Optional

from scraper.sources import SOURCES
from scraper.storage import get_articles, save_article

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _indexer_nouveaux() -> None:
    """
    Vectorise les articles qui ne le sont pas encore.

    Rattaché à la collecte plutôt qu'à une commande séparée : un corpus
    partiellement indexé dégrade le classement en silence, puisque les articles
    sans vecteur n'obtiennent qu'un score lexical et se retrouvent
    systématiquement défavorisés face aux autres.

    L'échec n'interrompt pas la veille : les articles sont enregistrés, ils
    seront vectorisés à la passe suivante ou par
    `python -m content.embeddings --indexer`.
    """
    try:
        from content.embeddings import (
            encoder, nom_modele, texte_indexable, vectoriser,
        )
        from scraper.storage import charger_vecteurs, enregistrer_vecteur

        modele = nom_modele()
        deja = set(charger_vecteurs(modele))
        restants = [
            a for a in get_articles(limit=5000)
            if a["source_id"] not in deja
            and len((a.get("summary") or "").strip()) >= 200
        ]
        if not restants:
            return

        logger.info("Vectorisation de %d nouveaux articles (%s)", len(restants), modele)
        vecteurs = vectoriser([texte_indexable(a) for a in restants])
        for article, vecteur in zip(restants, vecteurs):
            enregistrer_vecteur(article["source_id"], encoder(vecteur), modele)
        logger.info("%d articles vectorisés", len(restants))

    except Exception as e:
        logger.warning(
            "Indexation sémantique ignorée (%s) — les articles restent "
            "exploitables, le classement sera lexical", e
        )


def run_pipeline(source: str = "all", limit: int = 20) -> dict[str, int]:
    """
    Lance le pipeline de scraping pour une ou toutes les sources.

    Args:
        source: Nom de la source ('all', 'arxiv', 'huggingface_blog', 'montreal_ai_ethics', 'nist')
        limit:  Nombre maximum d'articles par source

    Returns:
        Dictionnaire {source: nb_nouveaux_articles}
    """
    sources_to_run = list(SOURCES.keys()) if source == "all" else [source]

    if source != "all" and source not in SOURCES:
        logger.error("Source inconnue : '%s'. Disponibles : %s", source, list(SOURCES.keys()))
        sys.exit(1)

    results: dict[str, int] = {}

    for src_name in sources_to_run:
        logger.info("=== Démarrage : %s ===", src_name)
        scraper_fn = SOURCES[src_name]
        new_count = 0

        try:
            for item in scraper_fn(limit=limit):
                saved = save_article(
                    source=item["source"],
                    source_id=item["source_id"],
                    title=item["title"],
                    url=item["url"],
                    summary=item["summary"],
                    published=item.get("published"),
                    tags=item.get("tags"),
                    raw_data=item.get("raw"),
                    # Vide pour les sources historiques, qui n'en déclarent
                    # pas : elles restent explicitement sans licence connue
                    # plutôt que faussement libres.
                    licence=item.get("licence", ""),
                )
                if saved:
                    new_count += 1
        except Exception as e:
            logger.error("Erreur inattendue sur %s : %s", src_name, e, exc_info=True)

        logger.info("=== %s terminé : %d nouveaux articles ===", src_name, new_count)
        results[src_name] = new_count

    total = sum(results.values())
    logger.info("Pipeline terminé. Total nouveaux articles : %d", total)

    if total:
        _indexer_nouveaux()
    return results


def main() -> None:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(description="Pipeline de scraping de contenu IA/ML")
    parser.add_argument(
        "--source",
        default="all",
        choices=["all"] + list(SOURCES.keys()),
        help="Source à scraper (défaut: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Nombre max d'articles par source (défaut: 20)",
    )
    args = parser.parse_args()
    run_pipeline(source=args.source, limit=args.limit)


if __name__ == "__main__":
    main()
