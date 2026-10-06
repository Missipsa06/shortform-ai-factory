"""
Rendu vidéo des slides animées : HTML piloté image par image, puis FFmpeg.

Pourquoi pas l'enregistrement natif de Playwright (`record_video`) : il capture
en temps réel, perd des images sous charge et la durée dérive — mesuré à +22 %
sur une séquence de 5 s. Inutilisable dès qu'il faut coller à une bande son.

Ici, l'animation ne joue jamais. Pour chaque image, on fixe l'instant de toutes
les animations de la page (`window.seek`), on capture, on avance. Le rendu est
donc déterministe : même sortie quelle que soit la machine ou sa charge.

Les images intermédiaires sont en JPEG et non en PNG : 5× plus rapide à capturer,
pour un écart de qualité mesuré à 48 dB de PSNR — invisible à l'œil.

Usage :
    python -m content.render --script data/processed/manuel_rag.json
"""

import argparse
import asyncio
import json
import logging
import subprocess
import tempfile
from pathlib import Path

from media.ffmpeg_utils import ffmpeg as find_ffmpeg

logger = logging.getLogger(__name__)

LARGEUR, HAUTEUR = 1080, 1920
FPS = 30
QUALITE_JPEG = 92
CRF = 20


def _duree_segment(segment: Path) -> float:
    """Durée d'un segment audio, en secondes."""
    from media.assembly import get_audio_duration

    return get_audio_duration(segment)


def durees_par_slide(segments_dir: Path, plan: list[dict]) -> dict[str, int]:
    """
    Associe à chaque slide la durée de son segment audio, en millisecondes.

    L'appariement se fait **par nom de fichier**, pas par position dans une liste
    triée : un tri suppose que l'ordre alphabétique des segments reproduit l'ordre
    d'affichage, ce qui cesse d'être vrai dès qu'une slide est insérée. Le plan
    porte déjà la correspondance, on s'en sert.

    Args:
        segments_dir: Dossier des segments MP3
        plan:         Sortie de content.slides.plan_slides

    Returns:
        {slide_id: durée en ms}
    """
    durees: dict[str, int] = {}
    for entree in plan:
        segment = segments_dir / f"{entree['segment']}.mp3"
        if not segment.exists():
            logger.warning(
                "Segment absent pour %s : durée par défaut appliquée", entree["id"]
            )
            continue
        durees[entree["id"]] = int(_duree_segment(segment) * 1000)
    return durees


def mots_par_slide(
    script: dict,
    segments_dir: Path,
    taille_modele: str = "base",
) -> dict[str, list[dict]]:
    """
    Date les mots affichés sur chaque slide, pour le surlignage karaoké.

    Deux subtilités :
      - le texte prononcé d'une slide de contenu est « titre. contenu », alors que
        seul le contenu est affiché : on retire les mots du titre en tête ;
      - les horodatages proviennent du texte *nettoyé* envoyé au TTS (sans emoji
        ni hashtag), donc le karaoké affiche exactement ce qui est prononcé.

    Args:
        script:        Script JSON du sujet
        segments_dir:  Dossier des segments MP3
        taille_modele: Taille du modèle de transcription

    Returns:
        {slide_id: [{'mot', 'debut', 'fin'}]}
    """
    from content.slides import plan_slides
    from media.align import caler_mots
    from media.tts import _clean_text, build_script_text

    # Le plan fournit la correspondance segment audio → slide affichée.
    par_segment = {e["segment"]: e for e in plan_slides(script)}

    # Le texte prononcé d'une slide de contenu commence par son titre : on compte
    # ces mots pour les retirer, seul le contenu étant affiché.
    prefixes = {}
    for entree in par_segment.values():
        donnees = entree["donnees"] or {}
        prefixes[entree["segment"]] = len(
            _clean_text(donnees.get("titre", "")).split()
        ) if entree["genre"] == "contenu" else 0

    resultat: dict[str, list[dict]] = {}

    for seg_id, texte, _type in build_script_text(script):
        entree = par_segment.get(seg_id)
        if entree is None:
            logger.warning("Segment sans slide correspondante : %s", seg_id)
            continue

        audio = segments_dir / f"{seg_id}.mp3"
        if not audio.exists():
            logger.warning("Segment absent, karaoké ignoré : %s", audio.name)
            continue

        mots = caler_mots(audio, texte, taille_modele)[prefixes.get(seg_id, 0):]
        resultat[entree["id"]] = [
            {"mot": m.texte, "debut": m.debut, "fin": m.fin} for m in mots
        ]

    logger.info("Karaoké calé sur %d slides", len(resultat))
    return resultat


# Tentatives de capture d'une même image avant d'abandonner la vidéo.
# Chromium répond « Unable to capture screenshot » quand il ne peut plus allouer
# la surface de rendu — typiquement sous pression mémoire, la stack Docker et
# x264 tournant à côté. L'échec est transitoire : la surface se reconstruit dès
# qu'un peu de mémoire se libère. Sans reprise, une image manquée sur les 3 000
# d'une vidéo perdait la synthèse vocale et les minutes de rendu déjà passées.
CAPTURES_ESSAIS = 3
CAPTURE_PAUSE = 1.5


async def _capturer_image(page, chemin: Path, instant_ms: float, uri: str) -> None:
    """
    Capture une image, en reconstruisant la page si sa surface est perdue.

    Args:
        page:       Page Playwright
        chemin:     Fichier JPEG à écrire
        instant_ms: Instant à figer dans les animations
        uri:        URI de la slide, pour la recharger en cas d'échec

    Raises:
        Exception: Si la capture échoue encore après CAPTURES_ESSAIS tentatives
    """
    for essai in range(1, CAPTURES_ESSAIS + 1):
        try:
            await page.evaluate("t => window.seek(t)", instant_ms)
            await page.screenshot(
                path=str(chemin), type="jpeg", quality=QUALITE_JPEG
            )
            return
        except Exception as erreur:
            if essai == CAPTURES_ESSAIS:
                raise
            logger.warning(
                "Capture de %s échouée (%d/%d) : %s — nouvelle tentative",
                chemin.name, essai, CAPTURES_ESSAIS, str(erreur).splitlines()[0],
            )
            # Laisser retomber la pression mémoire, puis repartir d'une page
            # fraîchement chargée : le contexte de rendu perdu ne revient pas
            # tout seul, un simple réessai sur la même page rééchouerait.
            await asyncio.sleep(CAPTURE_PAUSE)
            await page.goto(uri)


async def _capturer_slide(page, html: Path, duree_ms: int, dossier: Path) -> int:
    """
    Capture une slide image par image dans `dossier`.

    Returns:
        Nombre d'images produites
    """
    nb_images = max(1, round(duree_ms / 1000 * FPS))
    uri = html.resolve().as_uri()
    await page.goto(uri)

    for i in range(nb_images):
        await _capturer_image(
            page, dossier / f"f_{i:05d}.jpg", i * 1000 / FPS, uri
        )
    return nb_images


# Plafond de threads pour libx264. Sans limite, l'encodeur en ouvre autant que
# la machine a de cœurs logiques (12 ici) et alloue les tampons de chacun pour du
# 1080×1920 — en concurrence avec Chromium et le modèle de transcription, tous
# deux vivants dans le même processus. Observé à 79 % de RAM occupée : x264
# échoue alors sur « Generic error in an external library » sans avoir encodé une
# seule image. Quatre threads suffisent largement pour du 30 ips vertical.
THREADS_X264 = 4


def _erreur_ffmpeg(stderr: str) -> str:
    """
    Extrait le message utile d'une sortie FFmpeg.

    La bannière de version et la ligne de configuration occupent à elles seules
    plus de 2000 caractères : conserver la fin de stderr, comme on le faisait,
    gardait les lignes de conclusion et coupait justement la cause, annoncée en
    tête. On retire la bannière et on garde le début du reste.
    """
    lignes = [
        ligne for ligne in stderr.splitlines()
        if ligne.strip()
        and not ligne.startswith(("ffmpeg version", "  built with", "  configuration:", "  lib"))
    ]
    return "\n".join(lignes[:12]) or stderr[-400:]


def _assembler_images(dossier: Path, sortie: Path, reessais: int = 1) -> Path:
    """
    Assemble une séquence d'images en MP4 muet.

    Args:
        dossier:  Dossier des images numérotées
        sortie:   Chemin du MP4 à produire
        reessais: Tentatives restantes. Un échec d'encodage sous pression mémoire
                  est transitoire ; laisser mourir le pipeline gaspillerait
                  l'appel de synthèse vocale et les minutes de rendu déjà passées.
    """
    sortie.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        find_ffmpeg(), "-y",
        "-framerate", str(FPS),
        "-i", str(dossier / "f_%05d.jpg"),
        "-c:v", "libx264", "-preset", "fast", "-crf", str(CRF),
        "-threads", str(THREADS_X264),
        "-pix_fmt", "yuv420p",
        str(sortie),
    ]
    resultat = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")

    if resultat.returncode != 0 and reessais > 0:
        logger.warning(
            "Encodage de %s échoué, nouvelle tentative en mono-thread", sortie.name
        )
        cmd[cmd.index("-threads") + 1] = "1"
        resultat = subprocess.run(
            cmd, capture_output=True, encoding="utf-8", errors="replace"
        )

    if resultat.returncode != 0:
        raise RuntimeError(
            f"FFmpeg a échoué sur {sortie.name} (code {resultat.returncode}) :\n"
            f"{_erreur_ffmpeg(resultat.stderr or '')}\n"
            f"Si le message évoque l'encodeur sans image produite, la machine "
            f"manquait probablement de mémoire : ferme la stack Docker "
            f"(« make down ») ou les conteneurs d'autres projets, puis relance "
            f"« make render STEM=… »."
        )
    return sortie


# Chromium est ici un rastériseur, pas un navigateur : rien de ce qu'il garde en
# réserve pour l'interactivité ne sert. Ces options lui retirent ce dont on n'a
# pas l'usage, sur une machine où la mémoire est la ressource rare (mesuré :
# 1,7 Go libres sur 15,7 au moment d'un échec de capture).
ARGS_CHROMIUM = [
    "--disable-gpu",                 # le rendu est logiciel de toute façon
    "--disable-dev-shm-usage",       # /dev/shm est minuscule dans le conteneur
    "--disable-extensions",
    "--disable-background-networking",
    "--mute-audio",
]


# Slides rendues en même temps, chacune dans sa page d'un même navigateur.
# ponytail: 2 et pas plus — la mémoire est la ressource rare (échecs de capture
# et d'encodage observés à 79 % de RAM). Monter seulement après mesure.
PAGES_PARALLELES = 2


async def _rendre(html_files: list[Path], durees: dict[str, int],
                  sortie_dir: Path) -> list[Path]:
    """
    Rend les slides en parallèle sur PAGES_PARALLELES pages d'un seul navigateur.

    L'encodage FFmpeg part dans un thread : appelé directement, ce sous-processus
    bloquerait la boucle d'événements et les captures de l'autre page avec lui.
    L'ordre des clips rendus est celui des slides, quel que soit l'ordre de fin.
    """
    from playwright.async_api import async_playwright

    sortie_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        navigateur = await p.chromium.launch(args=ARGS_CHROMIUM)
        pages: asyncio.Queue = asyncio.Queue()
        for _ in range(min(PAGES_PARALLELES, len(html_files))):
            await pages.put(await navigateur.new_page(
                viewport={"width": LARGEUR, "height": HAUTEUR}
            ))

        async def une_slide(html: Path) -> Path:
            slide_id = html.stem
            duree_ms = durees.get(slide_id, 4000)
            clip = sortie_dir / f"{slide_id}.mp4"
            page = await pages.get()
            try:
                # Les images vivent dans un dossier temporaire du système : des
                # milliers de petits fichiers sur un volume monté seraient lents,
                # et elles ne servent à rien une fois le clip encodé.
                with tempfile.TemporaryDirectory(prefix=f"rendu_{slide_id}_") as tmp:
                    nb = await _capturer_slide(page, html, duree_ms, Path(tmp))
                    await asyncio.to_thread(_assembler_images, Path(tmp), clip)
            finally:
                await pages.put(page)
            logger.info("  %s : %d images (%.1fs)", slide_id, nb, duree_ms / 1000)
            return clip

        try:
            return list(await asyncio.gather(*(une_slide(h) for h in html_files)))
        finally:
            await navigateur.close()


# Mesure, dans l'état final de chaque slide, ce que l'œil ne verrait qu'après le
# rendu : un bloc qui déborde dans une bande recouverte par l'interface de
# TikTok, ou un mot plus large que sa colonne. Seuls les enfants directs de
# `.slide` sont mesurés : la colonne est un flex vertical, un contenu trop long
# allonge donc son bloc, qui sort alors de la zone utile.
_JS_MESURE = """z => {
  const fautes = [], tol = 2;
  for (const el of document.querySelectorAll('.slide > *')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const nom = String(el.className).split(' ')[0] || el.tagName.toLowerCase();
    if (r.bottom > z.h - z.bas + tol)
      fautes.push(`${nom} descend à ${Math.round(r.bottom)} px (limite ${z.h - z.bas})`);
    if (r.right > z.w - z.droite + tol)
      fautes.push(`${nom} déborde à droite jusqu'à ${Math.round(r.right)} px`);
    if (el.scrollWidth > el.clientWidth + tol)
      fautes.push(`${nom} : un mot dépasse la largeur de la colonne`);
  }
  return fautes;
}"""


async def _mesurer(html_files: list[Path]) -> dict[str, list[str]]:
    """Défauts de mise en page par slide ; les slides saines n'y figurent pas."""
    from playwright.async_api import async_playwright

    from content.slides import ZONE_SURE_BAS, ZONE_SURE_DROITE

    zones = {"w": LARGEUR, "h": HAUTEUR, "bas": ZONE_SURE_BAS, "droite": ZONE_SURE_DROITE}
    defauts: dict[str, list[str]] = {}
    async with async_playwright() as p:
        navigateur = await p.chromium.launch(args=ARGS_CHROMIUM)
        try:
            page = await navigateur.new_page(viewport={"width": LARGEUR, "height": HAUTEUR})
            for html in html_files:
                # La page se fige d'elle-même sur son dernier instant au chargement :
                # c'est l'état le plus chargé, tous les éléments sont apparus.
                await page.goto(html.resolve().as_uri())
                await page.evaluate("document.fonts.ready")
                fautes = await page.evaluate(_JS_MESURE, zones)
                if fautes:
                    defauts[html.stem] = fautes
        finally:
            await navigateur.close()
    return defauts


def verifier_mise_en_page(script_path: Path) -> None:
    """
    Refuse un script dont une slide sortirait de la zone utile.

    À appeler avant la synthèse vocale : le défaut se voit alors en quelques
    secondes, au lieu de minutes de rendu et d'un appel de quota consommé.
    La mise en page ne dépend pas des durées : les slides sont générées sans elles.

    Raises:
        ValueError: Liste des slides fautives et de leurs défauts
    """
    from content.slides import generate_html_slides

    defauts = asyncio.run(_mesurer(generate_html_slides(script_path)))
    if defauts:
        detail = "\n".join(
            f"  {slide} : {' ; '.join(fautes)}" for slide, fautes in defauts.items()
        )
        raise ValueError(
            f"Mise en page hors zone utile dans {script_path.name} :\n{detail}\n"
            f"Raccourcir le texte concerné dans le script JSON, ou le régénérer."
        )
    logger.info("Mise en page vérifiée : toutes les slides tiennent dans la zone utile")


def rendre_slides(
    script_path: Path,
    segments_dir: Path,
    images: "dict[str, Path] | None" = None,
    karaoke: bool = True,
    taille_modele: str = "base",
) -> list[Path]:
    """
    Produit un clip vidéo animé par slide, calé sur la durée de sa narration.

    Args:
        script_path:   Script JSON du sujet
        segments_dir:  Dossier des segments MP3 (donne la durée de chaque slide)
        images:        Illustrations Pexels éventuelles
        karaoke:       Surligner chaque mot au moment où il est prononcé
        taille_modele: Modèle de transcription utilisé pour dater les mots

    Returns:
        Liste ordonnée des clips MP4 muets
    """
    from content.slides import generate_html_slides, plan_slides

    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)

    # Même plan que la génération HTML : l'ordre n'est plus redérivé ici.
    plan = plan_slides(script)
    slide_ids = [e["id"] for e in plan]

    durees = durees_par_slide(segments_dir, plan)

    mots = None
    if karaoke:
        try:
            mots = mots_par_slide(script, segments_dir, taille_modele)
        except ImportError:
            logger.warning(
                "faster-whisper absent : rendu sans karaoké. "
                "Pour l'activer : make install-subs"
            )
        else:
            # Le calage est fini : le modèle n'a plus rien à faire en mémoire,
            # et Chromium comme x264 vont en réclamer beaucoup juste après.
            from media.align import liberer_modeles

            liberer_modeles()

    logger.info("Rendu de %d slides animées", len(slide_ids))
    html_files = generate_html_slides(
        script_path, images=images, durees_ms=durees, mots_ms=mots
    )
    sortie_dir = script_path.parent / script_path.stem / "clips"
    return asyncio.run(_rendre(html_files, durees, sortie_dir))


def main() -> None:
    """Point d'entrée CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(
        description="Rendu des slides animees en clips MP4"
    )
    parser.add_argument("--script", required=True, help="Chemin du script JSON")
    parser.add_argument(
        "--segments",
        help="Dossier des segments MP3 (defaut : data/audio/<stem>/segments)",
    )
    parser.add_argument(
        "--images",
        help="Dossier des illustrations (defaut : data/images/<stem>/)",
    )
    args = parser.parse_args()

    script_path = Path(args.script)
    if not script_path.exists():
        logger.error("Script introuvable : %s", script_path)
        return

    segments = (
        Path(args.segments)
        if args.segments
        else Path("data/audio") / script_path.stem / "segments"
    )
    if not segments.exists():
        logger.error(
            "Segments audio introuvables : %s — lance la synthese vocale d'abord",
            segments,
        )
        return

    # Les illustrations déjà présentes sont reprises automatiquement. Sans cela,
    # `make video` produisait des dégradés même quand les images existaient sur
    # disque : le CLI appelait `rendre_slides` sans jamais les lui passer, et le
    # défaut ne se voyait qu'au montage.
    dossier_images = (
        Path(args.images) if args.images
        else Path("data/images") / script_path.stem
    )
    images = {
        p.stem: p for p in sorted(dossier_images.glob("slide_*.jpg"))
    } if dossier_images.exists() else {}
    if images:
        logger.info("%d illustration(s) reprises depuis %s", len(images), dossier_images)
    else:
        logger.info("Aucune illustration dans %s — dégradés", dossier_images)

    clips = rendre_slides(script_path, segments, images=images or None)
    print(f"{len(clips)} clips rendus dans {clips[0].parent}")


if __name__ == "__main__":
    main()
