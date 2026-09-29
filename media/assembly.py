"""
Assemblage vidéo finale : clips animés + bande son → MP4 1080x1920 via FFmpeg.

Chaque clip, rendu par `content/render.py`, porte déjà la durée exacte de son
segment de narration : l'assemblage se contente de les mettre bout à bout et
d'y coller l'audio.

Usage :
    python -m media.assembly --clips data/processed/<stem>/clips/ \
                             --audio data/audio/<stem>/<stem>.mp3
"""

import argparse
import json
import logging
import subprocess
import tempfile
from pathlib import Path

from media.ffmpeg_utils import ffmpeg as find_ffmpeg, ffprobe as find_ffprobe

logger = logging.getLogger(__name__)

VIDEO_DIR = Path("data/videos")
AUDIO_BITRATE = "128k"
AUDIO_CODEC = "aac"


def _ligne_concat(chemin: Path) -> str:
    """
    Ligne "file '...'" pour une liste du démuxeur concat de FFmpeg.

    Une apostrophe non échappée dans le chemin ferme la citation en cours : le
    démuxeur bascule alors en mode non cité pour le reste de la ligne, ce qui
    concatène silencieusement les segments au lieu de signaler une erreur — un
    sujet en texte libre contenant une élision ("l'entraînement", "d'un modèle")
    donne un stem qui casse ainsi l'assemblage. Échapper "'" en "'\\''" (ferme,
    apostrophe littérale, rouvre) est la syntaxe documentée par FFmpeg pour ce cas.
    """
    echappe = chemin.resolve().as_posix().replace("'", "'\\''")
    return f"file '{echappe}'\n"


def get_audio_duration(audio_path: Path) -> float:
    """
    Récupère la durée d'un fichier audio via FFprobe.

    Returns:
        Durée en secondes
    """
    cmd = [
        find_ffprobe(), "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFprobe erreur : {result.stderr}")

    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def assemble_clips(clips: list[Path], audio_path: Path, output_path: Path) -> Path:
    """
    Concatène des clips vidéo animés et y colle la bande son.

    Chaque clip porte déjà la durée exacte de son segment de narration : la
    synchronisation est acquise par construction, il n'y a plus rien à caler.

    Args:
        clips:       Clips MP4 muets, dans l'ordre d'affichage
        audio_path:  Bande son complète
        output_path: MP4 de sortie

    Returns:
        Chemin de la vidéo assemblée
    """
    if not clips:
        raise ValueError("Aucun clip à assembler")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for clip in clips:
            f.write(_ligne_concat(clip))
        liste = Path(f.name)

    cmd = [
        find_ffmpeg(), "-y",
        "-f", "concat", "-safe", "0", "-i", str(liste),
        "-i", str(audio_path),
        # Les clips sortent déjà du même encodeur, aux mêmes réglages : une simple
        # copie du flux vidéo évite un ré-encodage et la perte qui va avec.
        "-c:v", "copy",
        "-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE,
        "-shortest", "-movflags", "+faststart",
        str(output_path),
    ]

    logger.info("Assemblage de %d clips animés → %s", len(clips), output_path.name)
    resultat = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    liste.unlink(missing_ok=True)

    if resultat.returncode != 0:
        logger.error("FFmpeg erreur :\n%s", (resultat.stderr or "")[-2000:])
        raise RuntimeError(f"FFmpeg a échoué (code {resultat.returncode})")

    taille = output_path.stat().st_size / 1024 / 1024
    logger.info("Vidéo assemblée : %s (%.1f Mo)", output_path, taille)
    return output_path


def main() -> None:
    """Point d'entrée CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Assemblage video + audio via FFmpeg")
    parser.add_argument("--clips", required=True, help="Dossier des clips animes")
    parser.add_argument("--audio", required=True, help="Fichier MP3 de la voix")
    parser.add_argument("--output", help="Chemin MP4 de sortie")
    args = parser.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.exists():
        logger.error("Fichier audio introuvable : %s", audio_path)
        return

    source_dir = Path(args.clips)
    if not source_dir.exists():
        logger.error("Dossier introuvable : %s", source_dir)
        return

    output_path = (
        Path(args.output) if args.output
        else VIDEO_DIR / f"{source_dir.parent.name}_raw.mp4"
    )

    clips = sorted(source_dir.glob("*.mp4"))
    if not clips:
        logger.error("Aucun clip dans %s", source_dir)
        return
    result = assemble_clips(clips, audio_path, output_path)
    print(f"Vidéo générée : {result}")


if __name__ == "__main__":
    main()
