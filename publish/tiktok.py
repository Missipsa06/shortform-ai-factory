"""
Publication de vidéos sur TikTok via Content Posting API v2.

Flow :
  1. init    → obtient une upload_url + publish_id
  2. upload  → envoie le fichier MP4 par chunks
  3. status  → vérifie la publication (PROCESSING → PUBLISHED)

Usage :
    python publish/tiktok.py --video data/videos/mon_topic_raw.mp4 --title "Mon titre"
    python publish/tiktok.py --video data/videos/mon_topic_raw.mp4 --approved-only

Prérequis :
    TIKTOK_ACCESS_TOKEN dans .env
    Scopes requis : video.publish, video.upload
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# Permet `python publish/tiktok.py` en plus de `python -m publish.tiktok` :
# lancé comme script, seul publish/ est sur sys.path, pas la racine du projet.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media.naming import find_video, script_path
from scraper.storage import get_approved_stems, mark_published

load_dotenv()
logger = logging.getLogger(__name__)

API_BASE      = "https://open.tiktokapis.com/v2"
CHUNK_SIZE    = 10 * 1024 * 1024   # 10 Mo par chunk
MAX_WAIT_S    = 120                 # timeout vérification statut (secondes)


# ── Auth ──────────────────────────────────────────────────────────────────────

def _token() -> str:
    """Récupère le token TikTok depuis les variables d'environnement."""
    token = os.getenv("TIKTOK_ACCESS_TOKEN", "")
    if not token:
        raise EnvironmentError("TIKTOK_ACCESS_TOKEN manquant dans .env")
    return token


def _headers() -> dict:
    """Retourne les headers d'authentification pour l'API TikTok."""
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json; charset=UTF-8",
    }


# ── API calls ─────────────────────────────────────────────────────────────────

def _init_upload(title: str, video_size: int, privacy: str = "SELF_ONLY") -> dict:
    """
    Initialise l'upload vidéo auprès de TikTok.

    Args:
        title:       Titre de la vidéo (max 150 caractères)
        video_size:  Taille du fichier en octets
        privacy:     SELF_ONLY | MUTUAL_FOLLOW_FRIENDS | FOLLOWER_OF_CREATOR | PUBLIC_TO_EVERYONE

    Returns:
        Dictionnaire contenant upload_url, publish_id, chunk_size
    """
    url = f"{API_BASE}/post/publish/video/init/"
    payload = {
        "post_info": {
            "title":         title[:150],
            "privacy_level": privacy,
            "disable_duet":  False,
            "disable_stitch": False,
            "disable_comment": False,
        },
        "source_info": {
            "source":              "FILE_UPLOAD",
            "video_size":          video_size,
            "chunk_size":          min(CHUNK_SIZE, video_size),
            "total_chunk_count":   -(-video_size // min(CHUNK_SIZE, video_size)),  # ceil division
        },
    }
    resp = requests.post(url, headers=_headers(), json=payload, timeout=30)
    data = resp.json()
    if resp.status_code != 200 or data.get("error", {}).get("code") != "ok":
        raise RuntimeError(f"TikTok init échoué : {data}")

    logger.info("Upload initialisé — publish_id : %s", data["data"]["publish_id"])
    return data["data"]


def _upload_chunks(upload_url: str, video_path: Path, chunk_size: int) -> None:
    """
    Envoie la vidéo par chunks via PUT vers l'URL pré-signée TikTok.

    Args:
        upload_url: URL pré-signée retournée par init
        video_path: Chemin du fichier MP4
        chunk_size: Taille de chaque chunk en octets
    """
    video_size = video_path.stat().st_size
    total_chunks = -(-video_size // chunk_size)  # ceil division

    with open(video_path, "rb") as f:
        for i in range(total_chunks):
            start = i * chunk_size
            end   = min(start + chunk_size, video_size) - 1
            chunk = f.read(chunk_size)

            headers = {
                "Content-Range":  f"bytes {start}-{end}/{video_size}",
                "Content-Length": str(len(chunk)),
                "Content-Type":   "video/mp4",
            }
            resp = requests.put(upload_url, headers=headers, data=chunk, timeout=120)
            if resp.status_code not in (200, 206):
                raise RuntimeError(f"Upload chunk {i+1}/{total_chunks} échoué : {resp.status_code} {resp.text}")

            logger.info("  Chunk %d/%d envoyé (%.1f Mo)", i + 1, total_chunks, len(chunk) / 1024 / 1024)


def _wait_published(publish_id: str) -> str:
    """
    Interroge l'API jusqu'à ce que la vidéo soit PUBLISHED ou en erreur.

    Returns:
        Statut final ('PUBLISHED', 'FAILED', etc.)
    """
    url = f"{API_BASE}/post/publish/status/fetch/"
    deadline = time.time() + MAX_WAIT_S

    while time.time() < deadline:
        resp = requests.post(
            url,
            headers=_headers(),
            json={"publish_id": publish_id},
            timeout=30,
        )
        data = resp.json()
        status = data.get("data", {}).get("status", "UNKNOWN")
        logger.info("Statut publication : %s", status)

        if status == "PUBLISH_COMPLETE":
            return "PUBLISHED"
        if status in ("FAILED", "SPAM_RISK_TOO_MANY_POSTS", "SPAM_RISK_USER_BANNED_FROM_POSTING"):
            raise RuntimeError(f"Publication TikTok échouée : {status} — {data}")

        time.sleep(5)

    raise TimeoutError(f"TikTok n'a pas confirmé la publication dans {MAX_WAIT_S}s")


# ── Helpers DB ────────────────────────────────────────────────────────────────

def _load_script(stem: str) -> dict:
    """Charge le script JSON associé à la vidéo pour récupérer le titre."""
    json_path = script_path(stem)
    if json_path.exists():
        try:
            return json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# ── Pipeline publication ───────────────────────────────────────────────────────

def publish_video(
    video_path: Path,
    title: str,
    privacy: str = "SELF_ONLY",
) -> str:
    """
    Publie une vidéo MP4 sur TikTok via Content Posting API.

    Args:
        video_path: Chemin vers le fichier MP4
        title:      Titre de la vidéo
        privacy:    Niveau de confidentialité TikTok

    Returns:
        publish_id TikTok
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Vidéo introuvable : {video_path}")

    video_size = video_path.stat().st_size
    logger.info("Publication TikTok : %s (%.1f Mo)", video_path.name, video_size / 1024 / 1024)

    # 1. Init
    data = _init_upload(title, video_size, privacy)
    upload_url = data["upload_url"]
    publish_id = data["publish_id"]
    chunk_size = data.get("chunk_size", CHUNK_SIZE)

    # 2. Upload
    _upload_chunks(upload_url, video_path, chunk_size)
    logger.info("Upload terminé — en attente de traitement TikTok...")

    # 3. Vérification statut
    status = _wait_published(publish_id)
    logger.info("Vidéo publiée sur TikTok ! publish_id=%s status=%s", publish_id, status)

    return publish_id


def publish_approved(privacy: str = "SELF_ONLY") -> list[str]:
    """
    Publie toutes les vidéos approuvées dans le dashboard de validation.

    Returns:
        Liste des publish_id TikTok générés
    """
    approved = get_approved_stems()
    if not approved:
        logger.info("Aucune vidéo approuvée à publier")
        return []

    publish_ids: list[str] = []
    for stem in approved:
        video_path = find_video(stem)
        if video_path is None:
            logger.warning("Vidéo introuvable pour stem '%s', ignorée", stem)
            continue

        script = _load_script(stem)
        title = script.get("titre_video") or stem.replace("_", " ")
        hashtags = " ".join(script.get("hashtags", []))
        full_title = f"{title} {hashtags}"[:150]

        try:
            pid = publish_video(video_path, full_title, privacy)
            publish_ids.append(pid)
            mark_published(stem)
        except Exception as e:
            logger.error("Échec publication '%s' : %s", stem, e)

    return publish_ids


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    """Point d'entrée CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Publication TikTok via Content Posting API")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--video", help="Chemin vers un fichier MP4 spécifique")
    group.add_argument("--approved-only", action="store_true",
                       help="Publie toutes les vidéos approuvées dans le dashboard")
    parser.add_argument("--title", default="", help="Titre de la vidéo (avec --video)")
    parser.add_argument(
        "--privacy",
        default="SELF_ONLY",
        choices=["SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE"],
        help="Niveau de confidentialité (défaut: SELF_ONLY pour tester)",
    )
    args = parser.parse_args()

    if args.video:
        video_path = Path(args.video)
        title = args.title or video_path.stem.replace("_", " ")
        pid = publish_video(video_path, title, args.privacy)
        logger.info("Publié — publish_id : %s", pid)
    else:
        pids = publish_approved(args.privacy)
        logger.info("%d vidéo(s) publiée(s)", len(pids))


if __name__ == "__main__":
    main()
