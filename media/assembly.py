"""
Assemblage vidéo finale : slides PNG + audio MP3 → MP4 1080x1920 via FFmpeg.
Chaque slide reste affichée pendant la durée de son segment audio.

Usage :
    python media/assembly.py --slides data/processed/manuel_transformers/png/ \
                             --audio data/audio/manuel_transformers/manuel_transformers.mp3 \
                             --output data/videos/manuel_transformers_raw.mp4
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
FPS = 30
AUDIO_BITRATE = "128k"
VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"
PIXEL_FORMAT = "yuv420p"


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


def _segment_order(segment_path: Path) -> tuple[int, str]:
    """
    Clé de tri des segments audio : hook, puis slide_01…slide_NN, puis conclusion.

    Un tri alphabétique placerait 'conclusion' et 'hook' avant les slides, ce qui
    décalerait toutes les durées d'un cran et désynchroniserait la vidéo.
    """
    name = segment_path.stem
    if name == "hook":
        return (0, "")
    if name == "conclusion":
        return (2, "")
    return (1, name)


def get_segment_durations(segments_dir: Path) -> list[float]:
    """
    Récupère la durée de chaque segment MP3 dans l'ordre (hook, slide_01, ..., conclusion).

    Returns:
        Liste des durées en secondes
    """
    segments = sorted(segments_dir.glob("*.mp3"), key=_segment_order)
    durations = []
    for seg in segments:
        try:
            d = get_audio_duration(seg)
            durations.append(d)
            logger.debug("%s : %.2fs", seg.name, d)
        except Exception as e:
            logger.warning("Impossible de lire la durée de %s : %s", seg.name, e)
            durations.append(3.0)  # durée par défaut
    return durations


def assemble_video(
    png_dir: Path,
    audio_path: Path,
    output_path: Path,
    segments_dir: Path | None = None,
    transition_duration: float = 0.3,
) -> Path:
    """
    Assemble les PNGs et l'audio en une vidéo MP4 finale.

    Chaque PNG est affiché pendant la durée du segment audio correspondant.
    Si segments_dir est fourni, la durée de chaque slide correspond exactement
    à la durée de son segment audio.

    Args:
        png_dir:              Dossier contenant les PNG (slide_00.png, slide_01.png, ...)
        audio_path:           Fichier MP3 de la voix
        output_path:          Chemin de sortie MP4
        segments_dir:         Dossier des segments MP3 (pour sync précise)
        transition_duration:  Durée de la transition entre slides (secondes)

    Returns:
        Chemin du fichier MP4 généré
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    png_files = sorted(png_dir.glob("*.png"))
    if not png_files:
        raise FileNotFoundError(f"Aucun PNG trouvé dans {png_dir}")

    logger.info("Assemblage : %d slides + audio → %s", len(png_files), output_path.name)

    # Calcul des durées par slide
    if segments_dir and segments_dir.exists():
        durations = get_segment_durations(segments_dir)
        # Alignement : si nb slides ≠ nb segments, répartir l'audio uniformément
        if len(durations) != len(png_files):
            logger.warning(
                "Désalignement %d segments audio / %d slides : répartition uniforme "
                "(la synchronisation sera approximative)",
                len(durations), len(png_files),
            )
            total_audio = get_audio_duration(audio_path)
            durations = [total_audio / len(png_files)] * len(png_files)
    else:
        total_audio = get_audio_duration(audio_path)
        durations = [total_audio / len(png_files)] * len(png_files)

    logger.info("Durées slides : %s", [f"{d:.1f}s" for d in durations])

    # Construction du filtre vidéo FFmpeg avec concat
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for png, duration in zip(png_files, durations):
            f.write(_ligne_concat(png))
            f.write(f"duration {duration:.3f}\n")
        # Répéter le dernier fichier (requis par FFmpeg concat demuxer)
        f.write(_ligne_concat(png_files[-1]))
        list_path = Path(f.name)

    cmd = [
        find_ffmpeg(), "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
        "-i", str(audio_path),
        "-vf", f"scale={1080}:{1920}:force_original_aspect_ratio=decrease,"
               f"pad={1080}:{1920}:(ow-iw)/2:(oh-ih)/2:color=0x0D0D1A,"
               f"fps={FPS}",
        "-c:v", VIDEO_CODEC,
        "-preset", "fast",
        "-crf", "23",
        "-c:a", AUDIO_CODEC,
        "-b:a", AUDIO_BITRATE,
        "-pix_fmt", PIXEL_FORMAT,
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    list_path.unlink(missing_ok=True)

    if result.returncode != 0:
        logger.error("FFmpeg erreur :\n%s", result.stderr[-2000:])
        raise RuntimeError(f"FFmpeg a échoué (code {result.returncode})")

    size_mb = output_path.stat().st_size / 1024 / 1024
    logger.info("Vidéo assemblée : %s (%.1f Mo)", output_path, size_mb)
    return output_path


def assemble_clips(clips: list[Path], audio_path: Path, output_path: Path) -> Path:
    """
    Concatène des clips vidéo animés et y colle la bande son.

    Contrairement à assemble_video(), qui devait répartir une durée par image
    fixe, chaque clip porte déjà la durée exacte de son segment de narration :
    la synchronisation est acquise par construction, il n'y a plus rien à caler.

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
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--clips", help="Dossier des clips animes (chemin recommande)")
    source.add_argument("--slides", help="Dossier PNG des slides (montage sur images fixes)")
    parser.add_argument("--audio", required=True, help="Fichier MP3 de la voix")
    parser.add_argument("--output", help="Chemin MP4 de sortie")
    parser.add_argument("--segments", help="Dossier des segments MP3 (avec --slides)")
    args = parser.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.exists():
        logger.error("Fichier audio introuvable : %s", audio_path)
        return

    source_dir = Path(args.clips or args.slides)
    if not source_dir.exists():
        logger.error("Dossier introuvable : %s", source_dir)
        return

    output_path = (
        Path(args.output) if args.output
        else VIDEO_DIR / f"{source_dir.parent.name}_raw.mp4"
    )

    if args.clips:
        clips = sorted(source_dir.glob("*.mp4"))
        if not clips:
            logger.error("Aucun clip dans %s", source_dir)
            return
        result = assemble_clips(clips, audio_path, output_path)
    else:
        segments_dir = Path(args.segments) if args.segments else None
        result = assemble_video(source_dir, audio_path, output_path, segments_dir)

    print(f"Vidéo générée : {result}")


if __name__ == "__main__":
    main()
