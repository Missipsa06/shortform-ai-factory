"""
Utilitaire pour localiser l'exécutable FFmpeg/FFprobe sur le système.
Cherche dans le PATH puis dans les emplacements conda courants.
"""

import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_CONDA_ROOT = Path(sys.executable).parent.parent.parent  # anaconda3/envs/Cc_env → anaconda3

_WINGET_GYAN = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"

def _find_gyan_bin() -> list[Path]:
    """Trouve le dossier bin du FFmpeg Gyan installé via winget."""
    if not _WINGET_GYAN.exists():
        return []
    for pkg in _WINGET_GYAN.glob("Gyan.FFmpeg_*/ffmpeg-*/bin"):
        if (pkg / "ffmpeg.exe").exists():
            return [pkg]
    return []

_CONDA_SEARCH_PATHS = [
    *_find_gyan_bin(),                                        # Gyan FFmpeg (libass inclus)
    Path(sys.executable).parent.parent / "Library" / "bin",  # env courant Windows
    _CONDA_ROOT / "Library" / "bin",                          # conda base
    _CONDA_ROOT / "envs" / "Env1" / "Library" / "bin",       # Env1
    _CONDA_ROOT / "envs" / "Cc_env" / "Library" / "bin",     # Cc_env
]


def find_executable(name: str) -> str:
    """
    Trouve le chemin complet d'un exécutable (ffmpeg, ffprobe).

    Cherche d'abord dans les dossiers prioritaires (Gyan, conda),
    puis dans le PATH système.

    Returns:
        Chemin complet vers l'exécutable

    Raises:
        FileNotFoundError: Si l'exécutable est introuvable
    """
    # 1. Cherche en priorité dans Gyan + conda (évite le conda ffmpeg du PATH)
    for search_dir in _CONDA_SEARCH_PATHS:
        candidate = search_dir / f"{name}.exe"
        if candidate.exists():
            logger.debug("Exécutable trouvé : %s", candidate)
            return str(candidate)

    # 2. Fallback : PATH système
    path = shutil.which(name)
    if path:
        return path

    raise FileNotFoundError(
        f"'{name}' introuvable. Installe-le via : winget install Gyan.FFmpeg"
    )


def ffmpeg() -> str:
    """Retourne le chemin vers ffmpeg."""
    return find_executable("ffmpeg")


def ffprobe() -> str:
    """Retourne le chemin vers ffprobe."""
    return find_executable("ffprobe")
