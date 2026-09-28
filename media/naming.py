"""
Conventions de nommage des fichiers produits par le pipeline.

Un sujet est identifié par un « stem » (ex: ``manuel_les_transformers``) qui
relie tous ses artefacts :

    script        data/processed/<stem>.json
    slides PNG    data/processed/<stem>/png/
    audio         data/audio/<stem>/<stem>.mp3
    vidéo brute   data/videos/<stem>_raw.mp4
    vidéo finale  data/videos/<stem>_raw_subtitled.mp4

Toutes les étapes passent par ce module plutôt que de reconstruire les noms à
la main : le dashboard et le module de publication dérivaient auparavant des
stems divergents (``manuel_rag_raw`` au lieu de ``manuel_rag``), ce qui cassait
la liaison vidéo → script.
"""

from pathlib import Path

PROCESSED_DIR = Path("data/processed")
VIDEOS_DIR = Path("data/videos")

RAW_SUFFIX = "_raw"
SUBTITLED_SUFFIX = "_subtitled"


def stem_from_video(video_path: Path) -> str:
    """
    Extrait le stem d'un chemin vidéo, quel que soit son stade de production.

    Args:
        video_path: Chemin vers un MP4 brut ou sous-titré

    Returns:
        Le stem du sujet (ex: 'manuel_rag' pour 'manuel_rag_raw_subtitled.mp4')
    """
    stem = video_path.stem
    if stem.endswith(SUBTITLED_SUFFIX):
        stem = stem[: -len(SUBTITLED_SUFFIX)]
    if stem.endswith(RAW_SUFFIX):
        stem = stem[: -len(RAW_SUFFIX)]
    return stem


def raw_video_path(stem: str) -> Path:
    """Retourne le chemin de la vidéo brute (sans sous-titres) d'un sujet."""
    return VIDEOS_DIR / f"{stem}{RAW_SUFFIX}.mp4"


def subtitled_video_path(stem: str) -> Path:
    """Retourne le chemin de la vidéo sous-titrée d'un sujet."""
    return VIDEOS_DIR / f"{stem}{RAW_SUFFIX}{SUBTITLED_SUFFIX}.mp4"


def script_path(stem: str) -> Path:
    """Retourne le chemin du script JSON d'un sujet."""
    return PROCESSED_DIR / f"{stem}.json"


def find_video(stem: str) -> Path | None:
    """
    Retourne la meilleure vidéo disponible pour un sujet.

    La version sous-titrée est préférée ; la version brute sert de repli
    lorsque le pipeline a été lancé avec --skip-subtitles.

    Returns:
        Chemin de la vidéo, ou None si aucune n'existe
    """
    for candidate in (subtitled_video_path(stem), raw_video_path(stem)):
        if candidate.exists():
            return candidate
    return None


def list_video_stems() -> list[str]:
    """Retourne les stems de tous les sujets ayant au moins une vidéo produite."""
    stems = {stem_from_video(p) for p in VIDEOS_DIR.glob("*.mp4")}
    return sorted(stems)
