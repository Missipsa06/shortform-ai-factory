"""
Scènes animées : la mascotte mise en situation, puis animée, pour chaque slide `scene`.

**Le principe.** Sur une slide narrative, le LLM décrit ce que fait la mascotte
(`action`) et ce qui se passe en cinq secondes (`mouvement`). Flux 2 Klein place
le robot dans la situation à partir de son image de référence — c'est elle qui
tient la cohérence du personnage d'une vidéo à l'autre — puis Wan 2.2 l'anime.
Le clip est découpé en images, que `seek()` affiche une à une (content/slides.py).

**Deux lancements de Wan2GP par vidéo, jamais un par slide** : toutes les scènes,
puis toutes les animations. Charger un modèle coûte près d'une minute.

**Le cache est la reproductibilité.** Deux générations d'un même clip ne sont pas
identiques — les calculs GPU ne le sont pas —, mesuré sur prototype. Une scène
générée reste donc sur disque, et le rendu la rejoue à l'image près.

**Tout échoue en repli** : sans Wan2GP (Airflow, Docker), la slide reste sans
scène avec un WARNING, et la vidéo se produit. Le prompt ne propose d'ailleurs
le type `scene` que si Wan2GP est là (content/llm.py).

Usage :
    python -m content.scenes --script data/processed/<stem>.json
"""

import argparse
import json
import logging
import subprocess
from pathlib import Path

from media import wan2gp
from media.ffmpeg_utils import ffmpeg as find_ffmpeg

logger = logging.getLogger(__name__)

SCENES_DIR = Path("data/scenes")
MASCOTTE = Path(__file__).resolve().parent.parent / "media" / "assets" / "mascotte" / "robot.jpg"
FPS = 24

# Le registre des scènes, ajouté à chaque description : c'est lui, avec l'image
# de référence, qui fait que toutes les scènes de la chaîne se ressemblent.
STYLE_SCENE = (
    "flat vector explainer illustration, same character design and style as the "
    "reference image, bold simple geometric shapes, soft gradients, plain dark navy "
    "background, single cyan accent color, no text, no letters, no words"
)
STYLE_MOUVEMENT = ("Flat 2D cartoon animation, static camera, smooth simple motion, "
                   "same flat vector style, no text.")
SUJET = "The small white robot from the reference image"


def _scenes_du_script(script: dict) -> "list[tuple[str, dict]]":
    """(identifiant de slide, données de la scène) — identifiants de `plan_slides`."""
    from content.slides import plan_slides

    return [
        (entree["id"], graphique["donnees"])
        for entree in plan_slides(script)
        if (graphique := (entree["donnees"] or {}).get("graphique") or {}).get("type") == "scene"
        and isinstance(graphique.get("donnees"), dict)
    ]


def _images(dossier: Path) -> list[Path]:
    return sorted(dossier.glob("*.jpg")) if dossier.is_dir() else []


def _decouper(clip: Path, dossier: Path) -> None:
    """Le clip en images JPEG, une fois pour toutes."""
    dossier.mkdir(parents=True, exist_ok=True)
    subprocess.run([find_ffmpeg(), "-y", "-loglevel", "error", "-i", str(clip),
                    "-q:v", "3", str(dossier / "f_%04d.jpg")], check=True)


def preparer_scenes(script_path: Path) -> "dict[str, Path]":
    """
    Génère les scènes manquantes du script, et inscrit leurs images dans le JSON.

    Returns:
        {slide_id: dossier d'images} pour chaque scène disponible
    """
    script = json.loads(script_path.read_text(encoding="utf-8"))
    scenes = _scenes_du_script(script)
    if not scenes:
        return {}
    dossier = SCENES_DIR / script_path.stem
    manquantes = [(sid, d) for sid, d in scenes if not _images(dossier / sid / "images")]

    if manquantes:
        logger.info("%d scène(s) à générer (%d en cache)", len(manquantes),
                    len(scenes) - len(manquantes))
        try:
            fixes = wan2gp.generer_images(
                [f"{SUJET} {d.get('action', '')}. {STYLE_SCENE}" for _, d in manquantes],
                [dossier / sid / "scene" for sid, _ in manquantes],
                modele=wan2gp.MODELE_SCENE, resolution=wan2gp.RESOLUTION_SCENE,
                reference=MASCOTTE,
            )
            clips = wan2gp.animer(
                fixes,
                [f"{STYLE_MOUVEMENT} {d.get('mouvement', '')}" for _, d in manquantes],
                [dossier / sid / "clip.mp4" for sid, _ in manquantes],
            )
            for (sid, _), clip in zip(manquantes, clips):
                _decouper(clip, dossier / sid / "images")
        except RuntimeError as e:
            logger.warning("Scènes indisponibles (%s) — slides sans scène", e)

    disponibles: dict[str, Path] = {}
    for sid, donnees in scenes:
        images = dossier / sid / "images"
        if _images(images):
            donnees |= {"images": images.as_posix(), "fps": FPS}
            disponibles[sid] = images
    script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("%d/%d scène(s) prête(s)", len(disponibles), len(scenes))
    return disponibles


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parseur = argparse.ArgumentParser(description="Génère les scènes animées d'un script")
    parseur.add_argument("--script", required=True, help="Script JSON du sujet")
    preparer_scenes(Path(parseur.parse_args().script))


if __name__ == "__main__":
    main()
