"""
Couche d'accès aux données du projet : PostgreSQL pour les métadonnées,
système de fichiers pour le contenu brut et les médias.

Deux tables :
  articles       les contenus collectés par le scraper et leur cycle de vie
  video_reviews  le statut de validation humaine de chaque vidéo produite

Malgré son emplacement sous scraper/, ce module est la porte d'entrée unique de
la base pour tout le projet — le dashboard et la publication passent par lui
plutôt que d'écrire leur propre SQL, ce qui évite les schémas divergents.

Connexion via CONTENU_DB_URL ; par défaut l'instance Postgres démarrée par
`astro dev start`, exposée sur localhost:5432.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

import psycopg
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

load_dotenv()
logger = logging.getLogger(__name__)

RAW_DIR = Path("data/raw")

# Adresse IP littérale et non « localhost » : sous Windows, libpq résout d'abord
# ::1 puis tente une négociation GSSAPI/SSPI qui peut bloquer plusieurs minutes
# avant de retomber sur IPv4. Avec 127.0.0.1, la connexion est immédiate.
DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/contenu"

# Une base injoignable doit faire échouer la tâche, pas la figer indéfiniment.
CONNECT_TIMEOUT = 10

_schema_pret = False


# ── Connexion ─────────────────────────────────────────────────────────────────

def _dsn() -> str:
    """Chaîne de connexion à la base métier."""
    return os.getenv("CONTENU_DB_URL", DEFAULT_DSN)


class BaseIndisponible(RuntimeError):
    """
    Postgres est injoignable : la stack n'est probablement pas démarrée.

    Distinguée d'une erreur SQL ordinaire pour que le message dise quoi faire.
    `ConnectionTimeout` remonté brut ne nomme ni la base concernée, ni la commande
    qui la démarre : l'appelant croit à un défaut du code qu'il vient d'écrire.
    """


def _diagnostiquer_connexion(exc: Exception) -> BaseIndisponible:
    """Traduit un échec de connexion en message actionnable."""
    infos = conninfo_to_dict(_dsn())
    hote = infos.get("host", "?")
    port = infos.get("port", "5432")
    return BaseIndisponible(
        f"Postgres injoignable sur {hote}:{port} après {CONNECT_TIMEOUT}s "
        f"({type(exc).__name__}).\n"
        f"  • En local, la base vit dans la stack Astro : démarre-la avec « make up ».\n"
        f"  • Depuis un conteneur, l'hôte doit être « postgres » et non "
        f"{hote} — vérifie CONTENU_DB_URL.\n"
        f"  • Sous Windows, toujours 127.0.0.1 et jamais localhost : libpq tente "
        f"une négociation GSSAPI sur un nom d'hôte et peut geler."
    )


def connect() -> psycopg.Connection:
    """
    Ouvre une connexion, en s'assurant que le schéma existe.

    Les lignes sont renvoyées sous forme de dictionnaires, comme le faisait
    sqlite3.Row : le code appelant reste inchangé.

    Raises:
        BaseIndisponible: Postgres ne répond pas
    """
    init_db()
    try:
        return psycopg.connect(
            _dsn(), row_factory=dict_row, connect_timeout=CONNECT_TIMEOUT
        )
    except psycopg.OperationalError as e:
        raise _diagnostiquer_connexion(e) from e


def _creer_base_si_absente() -> None:
    """
    Crée la base métier si elle n'existe pas encore.

    On se connecte à la base d'administration `postgres` : CREATE DATABASE ne
    peut pas s'exécuter depuis la base qu'on veut créer, ni dans une transaction.
    """
    infos = conninfo_to_dict(_dsn())
    cible = infos.get("dbname", "contenu")
    admin = make_conninfo(**{**infos, "dbname": "postgres"})

    try:
        conn_admin = psycopg.connect(
            admin, autocommit=True, connect_timeout=CONNECT_TIMEOUT
        )
    except psycopg.OperationalError as e:
        # Premier point de contact avec Postgres : c'est ici que l'absence de
        # stack se manifeste, et donc ici que le message doit être utile.
        raise _diagnostiquer_connexion(e) from e

    with conn_admin as conn:
        existe = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (cible,)
        ).fetchone()
        if not existe:
            # Le nom vient de notre configuration, pas d'une entrée utilisateur,
            # mais CREATE DATABASE n'accepte pas de paramètre lié.
            conn.execute(f'CREATE DATABASE "{cible}"')
            logger.info("Base créée : %s", cible)


def init_db() -> None:
    """
    Crée la base et les tables si nécessaire. Idempotent, exécuté une seule
    fois par processus : appelé à chaque requête, il coûterait un aller-retour
    réseau inutile.
    """
    global _schema_pret
    if _schema_pret:
        return

    _creer_base_si_absente()

    with psycopg.connect(_dsn(), connect_timeout=CONNECT_TIMEOUT) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id          SERIAL PRIMARY KEY,
                source      TEXT NOT NULL,
                source_id   TEXT NOT NULL UNIQUE,
                title       TEXT NOT NULL,
                url         TEXT NOT NULL,
                published   TEXT,
                summary     TEXT,
                tags        JSONB NOT NULL DEFAULT '[]'::jsonb,
                status      TEXT NOT NULL DEFAULT 'raw',
                licence     TEXT NOT NULL DEFAULT '',
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # Ajoutée après coup : les bases existantes ont été créées sans elle.
        # La licence manque toujours au moment où elle compte — à la
        # publication, des semaines après la collecte, quand plus rien ne
        # rappelle d'où venait l'article. Stockée par article et non par
        # source, à dessein : une source peut changer de licence, et un article
        # collecté hier reste couvert par celle qui s'appliquait hier.
        conn.execute(
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS licence "
            "TEXT NOT NULL DEFAULT ''"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS video_reviews (
                stem        TEXT PRIMARY KEY,
                status      TEXT NOT NULL DEFAULT 'en_attente',
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS articles_status_idx ON articles (status)"
        )
        # Vecteur sémantique de l'article, en float32 brut. Pas de pgvector :
        # l'extension est absente de l'image Astro (PostgreSQL 12.6), et à
        # l'échelle du corpus — 97 articles, 0,15 Mo de vecteurs — une recherche
        # exhaustive en numpy est exacte et immédiate. Un index approximatif ne
        # se justifierait qu'à plusieurs dizaines de milliers de documents.
        #
        # Le nom du modèle accompagne le vecteur : deux modèles produisent des
        # espaces incomparables, et les mélanger donnerait un classement absurde
        # sans la moindre erreur visible.
        conn.execute(
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS embedding BYTEA"
        )
        conn.execute(
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS embedding_modele TEXT"
        )
        conn.commit()

    _schema_pret = True
    logger.debug("Schéma vérifié sur %s", _dsn().rsplit("@", 1)[-1])


# ── Articles ──────────────────────────────────────────────────────────────────

def save_article(
    source: str,
    source_id: str,
    title: str,
    url: str,
    summary: str,
    published: Optional[str] = None,
    tags: Optional[list[str]] = None,
    raw_data: Optional[dict] = None,
    licence: str = "",
) -> bool:
    """
    Enregistre un article et sauvegarde ses données brutes en JSON.

    Un article déjà connu **dont le résumé est vide** voit celui-ci complété si
    la collecte en rapporte un. Sans cela, corriger un scraper ne répare que les
    articles à venir : ceux déjà en base gardent leur résumé vide pour toujours,
    puisqu'un simple `DO NOTHING` les ignore. C'est exactement ce qui s'est passé
    quand le scraper HuggingFace ne remontait que des titres.

    Le titre et la date sont complétés au passage, jamais écrasés : une valeur
    déjà présente fait foi.

    Returns:
        True si l'article est nouveau, False s'il existait déjà
    """
    with connect() as conn:
        ligne = conn.execute(
            """
            INSERT INTO articles
                (source, source_id, title, url, published, summary, tags, licence)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id) DO UPDATE SET
                summary   = EXCLUDED.summary,
                published = COALESCE(articles.published, EXCLUDED.published),
                tags      = EXCLUDED.tags,
                licence   = EXCLUDED.licence
            WHERE COALESCE(articles.summary, '') = ''
              AND COALESCE(EXCLUDED.summary, '') <> ''
            RETURNING id, (xmax = 0) AS insere
            """,
            (source, source_id, title, url, published, summary,
             json.dumps(tags or [], ensure_ascii=False), licence),
        ).fetchone()
        conn.commit()

    # `xmax = 0` distingue une insertion d'une mise à jour : sans ce test, un
    # article réparé serait compté comme nouveau dans le bilan de la collecte.
    if ligne is not None and not ligne["insere"]:
        logger.info("Résumé complété pour un article déjà connu : %s", source_id)
        return False

    if ligne is None:
        logger.debug("Article déjà en base : %s", source_id)
        return False

    if raw_data:
        json_path = RAW_DIR / source / f"{source_id.replace('/', '_')}.json"
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, ensure_ascii=False, indent=2)
        logger.debug("Données brutes sauvegardées : %s", json_path)

    logger.info("Nouvel article enregistré : [%s] %s", source, title)
    return True


def get_articles(
    source: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Récupère les articles filtrés par source et/ou statut, du plus récent au plus ancien."""
    requete = "SELECT * FROM articles WHERE TRUE"
    params: list = []

    if source:
        requete += " AND source = %s"
        params.append(source)
    if status:
        requete += " AND status = %s"
        params.append(status)

    requete += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)

    with connect() as conn:
        return conn.execute(requete, params).fetchall()


def enregistrer_vecteur(source_id: str, vecteur: bytes, modele: str) -> None:
    """
    Associe un vecteur sémantique à un article.

    Args:
        source_id: Identifiant de l'article
        vecteur:   Vecteur sérialisé (float32 brut)
        modele:    Modèle l'ayant produit, pour ne jamais mélanger deux espaces
    """
    with connect() as conn:
        conn.execute(
            "UPDATE articles SET embedding = %s, embedding_modele = %s "
            "WHERE source_id = %s",
            (vecteur, modele, source_id),
        )
        conn.commit()


def charger_vecteurs(modele: str) -> dict[str, bytes]:
    """
    Charge tous les vecteurs produits par un modèle donné.

    Le filtre sur le modèle est essentiel : après un changement de modèle, les
    anciens vecteurs deviennent inexploitables et doivent être ignorés plutôt
    que comparés à tort aux nouveaux.

    Returns:
        {source_id: vecteur sérialisé}
    """
    with connect() as conn:
        lignes = conn.execute(
            "SELECT source_id, embedding FROM articles "
            "WHERE embedding IS NOT NULL AND embedding_modele = %s",
            (modele,),
        ).fetchall()
    return {ligne["source_id"]: bytes(ligne["embedding"]) for ligne in lignes}


def update_status(source_id: str, status: str) -> None:
    """Met à jour le statut d'un article (raw, processed, published, rejected)."""
    with connect() as conn:
        conn.execute(
            "UPDATE articles SET status = %s WHERE source_id = %s",
            (status, source_id),
        )
        conn.commit()
    logger.debug("Statut mis à jour : %s → %s", source_id, status)


# ── Validation des vidéos ─────────────────────────────────────────────────────

STATUTS_VIDEO = ("en_attente", "approuvée", "rejetée", "publiée")


def get_review_status(stem: str) -> str:
    """Statut de validation d'une vidéo ('en_attente' si jamais examinée)."""
    with connect() as conn:
        ligne = conn.execute(
            "SELECT status FROM video_reviews WHERE stem = %s", (stem,)
        ).fetchone()
    return ligne["status"] if ligne else "en_attente"


def set_review_status(stem: str, status: str) -> None:
    """
    Enregistre la décision humaine sur une vidéo.

    Raises:
        ValueError: Si le statut n'est pas reconnu
    """
    if status not in STATUTS_VIDEO:
        raise ValueError(
            f"Statut inconnu : '{status}'. Valeurs possibles : {', '.join(STATUTS_VIDEO)}"
        )
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO video_reviews (stem, status, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (stem) DO UPDATE
                SET status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
            """,
            (stem, status),
        )
        conn.commit()
    logger.info("Vidéo '%s' : statut → %s", stem, status)


def get_approved_stems() -> list[str]:
    """Vidéos approuvées et pas encore publiées, dans l'ordre de validation."""
    with connect() as conn:
        lignes = conn.execute(
            "SELECT stem FROM video_reviews WHERE status = 'approuvée' ORDER BY updated_at"
        ).fetchall()
    return [ligne["stem"] for ligne in lignes]


def mark_published(stem: str) -> None:
    """Marque une vidéo comme publiée."""
    set_review_status(stem, "publiée")


def get_all_review_statuses() -> dict[str, str]:
    """
    Statuts de toutes les vidéos examinées, en une seule requête.

    Le dashboard se rafraîchit à chaque interaction : interroger la base une fois
    par vidéo affichée multiplierait les connexions sans bénéfice.
    """
    with connect() as conn:
        lignes = conn.execute("SELECT stem, status FROM video_reviews").fetchall()
    return {ligne["stem"]: ligne["status"] for ligne in lignes}
