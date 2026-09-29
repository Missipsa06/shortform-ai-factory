"""
Vectorisation des textes par fastembed, en local.

Modèle exécuté par ONNX Runtime — déjà présent, tiré par faster-whisper. Aucun
quota, aucun appel réseau, donc ré-vectoriser tout le corpus est gratuit : c'est
ce qui compte, puisqu'on le refait à chaque changement de modèle ou de scraper.
Les API d'embeddings de Mistral et Gemini ont été retirées pour cette raison :
elles coûtaient un quota sans rien apporter à l'échelle du corpus.

Le modèle par défaut est **multilingue** à dessein : les sujets sont formulés en
français et le corpus est anglophone. Vérifié sur la base réelle — « les réseaux
de neurones » remonte bien « Neural Solver » et « Tensor Dimensions in
Transformers », ce que la recherche par mots-clés ne pouvait pas faire.

Pourquoi pas sentence-transformers : il ramènerait PyTorch, écarté du projet au
profit de CTranslate2 pour la transcription. fastembed passe par ONNX Runtime,
déjà installé.

Usage :
    python -m content.embeddings --indexer
    python -m content.embeddings --requete "les transformers"
"""

import argparse
import logging
import os
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


# 384 dimensions pour 0,22 Go. Les variantes multilingues plus lourdes existent
# (mpnet 768 dims / 1 Go, e5-large 1024 dims / 2,24 Go) et se substituent par
# EMBEDDING_MODEL ; à 97 documents, leur gain ne justifie pas dix fois le poids
# dans l'image Docker.
MODELE_FASTEMBED = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Le texte vectorisé est tronqué : au-delà, on décrit le corps de l'article
# plutôt que son sujet, et la similarité se dilue.
LONGUEUR_MAX = 1200

# fastembed range ses modèles dans le dossier temporaire du système par défaut
# (mesuré : 241 Mo). Dans un conteneur, ce dossier est éphémère : chaque
# recréation retéléchargerait le modèle, et une tâche Airflow pourrait le perdre
# en cours de route. On impose donc un emplacement stable, monté en volume par
# docker-compose.override.yml comme l'est déjà le cache des modèles Whisper.
CACHE_DEFAUT = Path.home() / ".cache" / "fastembed"

_modele_local = None


def _cache_fastembed() -> str:
    """Dossier des modèles téléchargés, créé au besoin."""
    chemin = Path(os.getenv("FASTEMBED_CACHE") or CACHE_DEFAUT)
    chemin.mkdir(parents=True, exist_ok=True)
    return str(chemin)


def nom_modele() -> str:
    """
    Identifiant du modèle actif.

    Stocké à côté de chaque vecteur : deux modèles produisent des espaces
    incomparables, et mélanger leurs vecteurs donnerait un classement absurde
    sans la moindre erreur visible.
    """
    return os.getenv("EMBEDDING_MODEL") or MODELE_FASTEMBED


def vectoriser(textes: list[str]) -> np.ndarray:
    """
    Vectorise une liste de textes et normalise les vecteurs.

    Le modèle est chargé une seule fois par processus. La normalisation est
    faite ici, une fois pour toutes : le produit scalaire de deux vecteurs
    unitaires vaut leur similarité cosinus, ce qui évite de recalculer des
    normes à chaque comparaison.

    Returns:
        Matrice (n, dimensions) de vecteurs unitaires, en float32
    """
    global _modele_local
    if not textes:
        return np.zeros((0, 0), dtype=np.float32)

    from fastembed import TextEmbedding

    modele = nom_modele()
    if _modele_local is None or _modele_local[0] != modele:
        logger.info("Chargement du modèle de vectorisation (%s)", modele)
        _modele_local = (modele, TextEmbedding(modele, cache_dir=_cache_fastembed()))
    tronques = [t[:LONGUEUR_MAX] for t in textes]
    vecteurs = np.array(list(_modele_local[1].embed(tronques)), dtype=np.float32)

    normes = np.linalg.norm(vecteurs, axis=1, keepdims=True)
    # Un vecteur nul ferait une division par zéro et propagerait des NaN dans
    # tout le classement ; on le laisse nul, sa similarité sera simplement de 0.
    normes[normes == 0] = 1.0
    return vecteurs / normes


def similarites(requete: np.ndarray, corpus: np.ndarray) -> np.ndarray:
    """
    Similarité cosinus entre une requête et un corpus déjà normalisés.

    Recherche exhaustive et non approximative : à 97 documents de 384 dimensions,
    le corpus pèse 0,15 Mo et le produit matriciel est immédiat. Un index
    approximatif — pgvector, Qdrant — ne se justifierait qu'à plusieurs dizaines
    de milliers de documents.
    """
    if corpus.size == 0:
        return np.zeros(0, dtype=np.float32)
    return corpus @ requete


def encoder(vecteur: np.ndarray) -> bytes:
    """Sérialise un vecteur pour le stockage en base."""
    return np.asarray(vecteur, dtype=np.float32).tobytes()


def decoder(donnees: bytes) -> np.ndarray:
    """Reconstruit un vecteur depuis sa forme stockée."""
    return np.frombuffer(donnees, dtype=np.float32)


def texte_indexable(article: dict) -> str:
    """
    Compose le texte représentant un article.

    Le titre est repris en tête : il annonce le sujet de façon dense, là où le
    résumé le dilue dans les détails de méthode.
    """
    return f"{article.get('title', '')}. {(article.get('summary') or '').strip()}"


def main() -> None:
    """Point d'entrée CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Vectorisation du corpus")
    groupe = parser.add_mutually_exclusive_group(required=True)
    groupe.add_argument(
        "--indexer", action="store_true",
        help="Vectorise les articles qui ne le sont pas encore",
    )
    groupe.add_argument("--requete", help="Cherche les articles proches d'un theme")
    parser.add_argument("--limite", type=int, default=5, help="Resultats affiches")
    parser.add_argument(
        "--tout", action="store_true",
        help="Re-vectorise meme les articles deja indexes (apres changement de modele)",
    )
    args = parser.parse_args()

    from scraper.storage import charger_vecteurs, enregistrer_vecteur, get_articles

    modele = nom_modele()

    if args.indexer:
        articles = [
            a for a in get_articles(limit=5000)
            if len((a.get("summary") or "").strip()) >= 200
        ]
        deja = set() if args.tout else set(charger_vecteurs(modele))
        restants = [a for a in articles if a["source_id"] not in deja]

        if not restants:
            print(f"{len(articles)} articles deja indexes avec {modele}")
            return

        logger.info("Vectorisation de %d articles (%s)", len(restants), modele)
        vecteurs = vectoriser([texte_indexable(a) for a in restants])
        for article, vecteur in zip(restants, vecteurs):
            enregistrer_vecteur(article["source_id"], encoder(vecteur), modele)
        print(f"{len(restants)} articles vectorises ({vecteurs.shape[1]} dimensions)")
        return

    stockes = charger_vecteurs(modele)
    if not stockes:
        print(f"Aucun vecteur en base pour {modele}. Lance d'abord --indexer")
        return

    ids = list(stockes)
    corpus = np.vstack([decoder(stockes[i]) for i in ids])
    q = vectoriser([args.requete])[0]
    scores = similarites(q, corpus)

    titres = {a["source_id"]: a["title"] for a in get_articles(limit=5000)}
    for rang in np.argsort(-scores)[: args.limite]:
        print(f"  {scores[rang]:.3f}  {titres.get(ids[rang], ids[rang])[:70]}")


if __name__ == "__main__":
    main()
