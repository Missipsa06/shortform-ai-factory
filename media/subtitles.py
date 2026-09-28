"""
Génération automatique de sous-titres via Whisper + burn dans la vidéo via FFmpeg.

Usage :
    python media/subtitles.py --video data/videos/sujet_raw.mp4
    python media/subtitles.py --video data/videos/sujet_raw.mp4 --model small
"""

import argparse
import logging
import subprocess
from pathlib import Path

from media.ffmpeg_utils import ffmpeg as find_ffmpeg

logger = logging.getLogger(__name__)

# Le décodage audio passe par PyAV (embarqué dans faster-whisper) : contrairement
# à openai-whisper, aucun appel à un ffmpeg externe, donc plus besoin de bricoler
# le PATH avant la transcription. Seule l'incrustation utilise encore ffmpeg.

FONT_SIZE = 52
FONT_COLOR = "white"
OUTLINE_COLOR = "black"
OUTLINE_WIDTH = 3
MARGIN_V = 120   # Marge verticale depuis le bas (px)
MAX_CHARS_PER_LINE = 35


def transcribe(video_path: Path, model: str = "base") -> Path:
    """
    Transcrit l'audio de la vidéo en fichier SRT via faster-whisper (local).

    Args:
        video_path: Chemin vers la vidéo MP4
        model:      Taille du modèle ('tiny', 'base', 'small', 'medium', 'large')

    Returns:
        Chemin du fichier SRT généré
    """
    from faster_whisper import WhisperModel

    output_dir = video_path.parent
    srt_path = output_dir / f"{video_path.stem}.srt"

    logger.info("Transcription faster-whisper (modèle: %s) : %s", model, video_path.name)
    # int8 est le compromis vitesse/qualité recommandé sur CPU ; le modèle est
    # téléchargé puis mis en cache au premier appel.
    wmodel = WhisperModel(model, device="cpu", compute_type="int8")
    segments_iter, info = wmodel.transcribe(str(video_path), language="fr")

    # L'itérateur est paresseux : la transcription se déroule pendant le parcours.
    segments = list(segments_iter)
    logger.info(
        "%d segments transcrits (%.1fs d'audio)", len(segments), info.duration
    )

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, start=1):
            start = _seconds_to_srt_time(seg.start)
            end = _seconds_to_srt_time(seg.end)
            # Découpage si la ligne est trop longue
            text = _wrap_text(seg.text.strip(), MAX_CHARS_PER_LINE)
            f.write(f"{i}\n{start} --> {end}\n{text}\n\n")

    logger.info("SRT généré : %s", srt_path)
    return srt_path


def _seconds_to_srt_time(seconds: float) -> str:
    """Convertit des secondes en format SRT (HH:MM:SS,mmm)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _wrap_text(text: str, max_chars: int) -> str:
    """Découpe le texte en lignes de max_chars caractères max."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 <= max_chars:
            current = f"{current} {word}".strip()
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)


def _srt_to_ass(srt_path: Path, ass_path: Path) -> None:
    """
    Convertit un fichier SRT en ASS avec le style défini (police, taille, couleurs).
    Le style est intégré dans l'en-tête ASS, évitant les paramètres inline FFmpeg.
    """
    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{FONT_SIZE},&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{OUTLINE_WIDTH},1,2,10,10,{MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def srt_time_to_ass(t: str) -> str:
        """Convertit HH:MM:SS,mmm → H:MM:SS.cc (format ASS)."""
        t = t.replace(",", ".")
        h, m, rest = t.split(":")
        s, ms = rest.split(".")
        cs = int(ms) // 10
        return f"{int(h)}:{m}:{s}.{cs:02d}"

    srt_text = srt_path.read_text(encoding="utf-8")
    blocks = [b.strip() for b in srt_text.strip().split("\n\n") if b.strip()]
    events = []

    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        times = lines[1].split(" --> ")
        if len(times) != 2:
            continue
        start = srt_time_to_ass(times[0].strip())
        end = srt_time_to_ass(times[1].strip())
        text = "\\N".join(lines[2:]).replace(",", "，")  # virgule pleine → évite conflits ASS
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    ass_path.write_text(ass_header + "\n".join(events) + "\n", encoding="utf-8")
    logger.info("Fichier ASS généré : %s (%d dialogues)", ass_path.name, len(events))


def burn_subtitles(video_path: Path, srt_path: Path, output_path: Path | None = None) -> Path:
    """
    Incruste (burn) les sous-titres dans la vidéo via FFmpeg.

    Args:
        video_path:  Vidéo source MP4
        srt_path:    Fichier SRT des sous-titres
        output_path: Chemin de sortie (défaut: <nom>_subtitled.mp4)

    Returns:
        Chemin de la vidéo finale avec sous-titres
    """
    import shutil
    import tempfile

    if output_path is None:
        output_path = video_path.parent / f"{video_path.stem}_subtitled.mp4"

    # Convertit le SRT en ASS avec style intégré (évite les problèmes de quoting FFmpeg)
    ass_path = srt_path.with_suffix(".ass")
    _srt_to_ass(srt_path, ass_path)

    # Chemin relatif simple → pas de lecteur "C:", pas de quoting FFmpeg
    tmp_ass = Path("_tmp_subs.ass")
    shutil.copy(ass_path, tmp_ass)

    try:
        cmd = [
            find_ffmpeg(), "-y",
            "-i", str(video_path),
            "-vf", "ass=_tmp_subs.ass",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "copy",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ]

        logger.info("Burn sous-titres → %s", output_path.name)
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error("FFmpeg erreur :\n%s", result.stderr[-2000:])
            raise RuntimeError(f"FFmpeg burn sous-titres échoué (code {result.returncode})")

    finally:
        tmp_ass.unlink(missing_ok=True)

    logger.info("Vidéo finale : %s", output_path)
    return output_path


def process_video(video_path: Path, model: str = "base") -> Path:
    """
    Pipeline complet : transcription Whisper + burn sous-titres.

    Args:
        video_path: Chemin vers la vidéo brute
        model:      Modèle Whisper à utiliser

    Returns:
        Chemin de la vidéo finale sous-titrée
    """
    srt_path = transcribe(video_path, model)
    output_path = video_path.parent / f"{video_path.stem}_subtitled.mp4"
    return burn_subtitles(video_path, srt_path, output_path)


def main() -> None:
    """Point d'entrée CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Sous-titres automatiques Whisper + burn FFmpeg")
    parser.add_argument("--video", required=True, help="Chemin vers la vidéo MP4")
    parser.add_argument(
        "--model",
        default="base",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Modèle Whisper (défaut: base)",
    )
    parser.add_argument("--srt-only", action="store_true",
                        help="Générer uniquement le SRT sans burn")
    args = parser.parse_args()

    video_path = Path(args.video)
    if not video_path.exists():
        logger.error("Vidéo introuvable : %s", video_path)
        return

    if args.srt_only:
        srt = transcribe(video_path, args.model)
        print(f"SRT généré : {srt}")
    else:
        final = process_video(video_path, args.model)
        print(f"Vidéo finale : {final}")


if __name__ == "__main__":
    main()
