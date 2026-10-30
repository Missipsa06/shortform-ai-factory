"""
Assemblage vidéo finale : clips animés + bande son → MP4 1080x1920 via FFmpeg.

Chaque clip, rendu par `content/render.py`, porte déjà la durée exacte de son
segment de narration : l'assemblage se contente de les mettre bout à bout et
d'y coller l'audio — la voix, une musique de fond qui s'efface sous elle, et un
bruitage à chaque changement de slide (`media/assets/sons/`).

Usage :
    python -m media.assembly --clips data/processed/<stem>/clips/ \
                             --audio data/audio/<stem>/<stem>.mp3
"""

import argparse
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from media.ffmpeg_utils import ffmpeg as find_ffmpeg, ffprobe as find_ffprobe

load_dotenv()
logger = logging.getLogger(__name__)

VIDEO_DIR = Path("data/videos")
AUDIO_BITRATE = "128k"
AUDIO_CODEC = "aac"

# ── Habillage sonore (30/10/2026) ─────────────────────────────────────────────
# Générés une fois par Stable Audio 3 Small et versionnés, comme les polices :
# aucun GPU par vidéo, même son en local et sous Airflow. Origine, prompts et
# licence dans SOURCES.json. « aucune » / « aucun » coupe l'un ou l'autre.
SONS_DIR = Path(__file__).parent / "assets" / "sons"
MUSIQUE_ENV, MUSIQUE_DEFAUT = "MUSIQUE", "tech_minimal"
BRUITAGE_ENV, BRUITAGE_DEFAUT = "BRUITAGE_TRANSITION", "whoosh"
_AUCUN = frozenset({"0", "aucun", "aucune", "non", "off", "none"})
# Mesuré sur la vidéo « Amazon » : à 0,22 puis comprimée par la voix, la
# musique reste ~10 dB sous elle (-31 LUFS contre -21) et le niveau global ne
# bouge pas. Le bruitage précède l'entrée de la slide, pendant les 240 ms de
# sortie de la précédente.
VOLUME_MUSIQUE = 0.22
VOLUME_BRUITAGE = 0.45
AVANCE_BRUITAGE_S = 0.12
# La voix de Qwen3 est en mono 24 kHz, et un mix suit le format de sa première
# entrée : la musique y perdait tout au-dessus de 12 kHz.
FORMAT_MIX = "aformat=sample_rates=48000:channel_layouts=stereo"


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


def _son(env: str, defaut: str) -> "Path | None":
    """Fichier de `SONS_DIR` désigné par la variable ; None s'il est coupé ou absent."""
    nom = (os.getenv(env) or defaut).strip().lower()
    if nom in _AUCUN:
        return None
    chemin = SONS_DIR / f"{nom}.mp3"
    if not chemin.exists():
        # Un nom mal tapé ne doit pas coûter la vidéo : elle sort avec la voix seule.
        logger.warning("%s=%s : %s introuvable, ignoré", env, nom, chemin.name)
        return None
    return chemin


def instants_de_transition(genres: list[str], durees: list[float]) -> list[float]:
    """
    Début de chaque slide, en secondes, sauf la première et l'introduction.

    L'introduction prolonge l'accroche sur le même écran, sans entrée : un
    bruitage y marquerait une transition que l'image ne fait pas.
    """
    instants, t = [], 0.0
    for genre, duree in zip(genres, durees):
        if t > 0 and genre != "intro":
            instants.append(round(t, 3))
        t += duree
    return instants


def filtre_son(duree: float, musique: bool, transitions: list[float]) -> str:
    """
    Graphe FFmpeg de la bande son : entrée 1 la voix, 2 la musique, puis le bruitage.

    La musique est comprimée par la voix (`sidechaincompress`) : elle s'efface
    quand la voix parle et remonte dans les silences, sans calage à la main.
    """
    filtres = [f"[1:a]{FORMAT_MIX},asplit=2[voix][cle]" if musique else f"[1:a]{FORMAT_MIX}[voix]"]
    pistes = ["[voix]"]
    if musique:
        filtres += [
            f"[2:a]{FORMAT_MIX},atrim=0:{duree:.3f},volume={VOLUME_MUSIQUE},"
            f"afade=t=in:d=1.5,afade=t=out:st={max(0.0, duree - 2):.3f}:d=2[fond]",
            "[fond][cle]sidechaincompress=threshold=0.03:ratio=6:attack=30:release=500[fond_bas]",
        ]
        pistes.append("[fond_bas]")
    if transitions:
        entree = 3 if musique else 2
        copies = "".join(f"[b{i}]" for i in range(len(transitions)))
        filtres.append(f"[{entree}:a]{FORMAT_MIX},volume={VOLUME_BRUITAGE},asplit={len(transitions)}{copies}")
        for i, instant in enumerate(transitions):
            filtres.append(f"[b{i}]adelay={max(0, int((instant - AVANCE_BRUITAGE_S) * 1000))}:all=1[t{i}]")
            pistes.append(f"[t{i}]")
    filtres.append("".join(pistes) + f"amix=inputs={len(pistes)}:normalize=0:duration=first[son]")
    return ";".join(filtres)


def genres_du_script(script_path: Path) -> "list[str] | None":
    """Genre de chaque slide dans l'ordre d'affichage (accroche, intro, contenu…)."""
    if not script_path.exists():
        return None
    from content.slides import plan_slides

    script = json.loads(script_path.read_text(encoding="utf-8"))
    return [entree["genre"] for entree in plan_slides(script)]


def assemble_clips(clips: list[Path], audio_path: Path, output_path: Path,
                   genres: "list[str] | None" = None) -> Path:
    """
    Concatène des clips vidéo animés et y colle la bande son.

    Chaque clip porte déjà la durée exacte de son segment de narration : la
    synchronisation est acquise par construction, il n'y a plus rien à caler.
    Les instants de transition s'en déduisent de même.

    Args:
        clips:       Clips MP4 muets, dans l'ordre d'affichage
        audio_path:  Bande son complète
        output_path: MP4 de sortie
        genres:      Genre de chaque clip (`genres_du_script`) ; sans lui, ou
                     s'il ne correspond pas aux clips, pas de bruitage

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

    musique, bruitage = _son(MUSIQUE_ENV, MUSIQUE_DEFAUT), _son(BRUITAGE_ENV, BRUITAGE_DEFAUT)
    transitions: list[float] = []
    if bruitage and genres:
        if len(genres) == len(clips):
            transitions = instants_de_transition(genres, [get_audio_duration(c) for c in clips])
        else:
            logger.warning("%d genres pour %d clips : pas de bruitage", len(genres), len(clips))

    entrees = ["-f", "concat", "-safe", "0", "-i", str(liste), "-i", str(audio_path)]
    son = ["-map", "0:v", "-map", "1:a"]
    if musique or transitions:
        duree = get_audio_duration(audio_path)
        if musique:
            entrees += ["-stream_loop", "-1", "-i", str(musique)]   # bouclée si la vidéo est plus longue
        if transitions:
            entrees += ["-i", str(bruitage)]
        son = ["-filter_complex", filtre_son(duree, musique is not None, transitions),
               "-map", "0:v", "-map", "[son]"]
        logger.info("Habillage sonore : musique %s, %d transition(s)",
                    musique.stem if musique else "aucune", len(transitions))

    cmd = [
        find_ffmpeg(), "-y", *entrees, *son,
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
    # data/processed/<stem>/clips → data/processed/<stem>.json
    genres = genres_du_script(source_dir.parent.parent / f"{source_dir.parent.name}.json")
    result = assemble_clips(clips, audio_path, output_path, genres)
    print(f"Vidéo générée : {result}")


if __name__ == "__main__":
    main()
