"""
Export des vidéos approuvées vers un dossier synchronisé (Google Drive, OneDrive).

Destiné à la publication depuis le téléphone : le client Drive miroite un dossier
local, l'application mobile le rend disponible, et la vidéo se partage vers TikTok
sans passer par l'ordinateur.

Pourquoi un dossier local plutôt que l'API Drive : aucun OAuth à gérer, aucun
jeton à rafraîchir, et surtout aucun système de fichiers virtuel. Le mode
« streaming » de Google Drive expose un lecteur que Docker Desktop ne sait pas
monter de façon fiable sous Windows — or `data/` est monté dans le scheduler
Airflow et dans le dashboard. Un dossier miroité reste un dossier ordinaire.

Seules les vidéos **approuvées** partent : le dossier du téléphone ne doit
contenir que ce qui est prêt à publier, pas soixante essais.

Prérequis dans .env :
    DRIVE_EXPORT_DIR=C:/Users/…/Google Drive/IA.Maline

Usage :
    python -m publish.drive              # exporte les approuvées
    python -m publish.drive --tout       # inclut les déjà publiées
    python -m publish.drive --lister     # montre ce qui serait exporté
"""

import argparse
import json
import logging
import os
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from media.naming import find_video, script_path
from scraper.storage import get_all_review_statuses

load_dotenv()
logger = logging.getLogger(__name__)

STATUTS_EXPORTABLES = ("approuvée",)


def dossier_export() -> Path:
    """
    Dossier de destination, créé au besoin.

    Raises:
        EnvironmentError: Variable absente du .env
    """
    brut = os.getenv("DRIVE_EXPORT_DIR", "").strip()
    if not brut:
        raise EnvironmentError(
            "DRIVE_EXPORT_DIR manquant dans .env — indique le dossier synchronisé "
            "par ton client Google Drive, par exemple "
            "C:/Users/toi/Google Drive/IA.Maline"
        )
    chemin = Path(brut).expanduser()
    chemin.mkdir(parents=True, exist_ok=True)
    return chemin


def _lisible(texte: str, longueur: int = 60) -> str:
    """
    Transforme un titre en nom de fichier lisible sur un téléphone.

    Le stem est parlant pour le pipeline, pas pour un humain : `arxiv_2608.28511v1`
    ne dit rien dans une galerie. Le titre de la vidéo, lui, se reconnaît d'un
    coup d'œil au moment de publier.
    """
    plie = unicodedata.normalize("NFD", texte)
    plie = "".join(c for c in plie if unicodedata.category(c) != "Mn")
    propre = re.sub(r"[^\w\s-]", "", plie).strip()
    return re.sub(r"[\s_]+", "-", propre)[:longueur].strip("-") or "video"


def _nom_export(stem: str, video: Path) -> str:
    """
    Nom du fichier exporté : date, titre lisible, extension d'origine.

    La date en tête garde l'ordre chronologique dans la galerie du téléphone,
    où le tri par nom est souvent le seul disponible.
    """
    date = datetime.fromtimestamp(video.stat().st_mtime).strftime("%Y-%m-%d")
    titre = stem
    chemin_json = script_path(stem)
    if chemin_json.exists():
        try:
            script = json.loads(chemin_json.read_text(encoding="utf-8"))
            titre = script.get("titre_video") or stem
        except (ValueError, OSError):
            pass
    return f"{date}_{_lisible(titre)}{video.suffix}"


def videos_a_exporter(tout: bool = False) -> list[tuple[str, Path]]:
    """
    Vidéos éligibles à l'export, de la plus récente à la plus ancienne.

    Args:
        tout: Inclure aussi les vidéos déjà publiées

    Returns:
        [(stem, chemin de la vidéo)]
    """
    statuts = get_all_review_statuses()
    acceptes = set(STATUTS_EXPORTABLES) | ({"publiée"} if tout else set())

    resultat = []
    for stem, statut in statuts.items():
        if statut not in acceptes:
            continue
        video = find_video(stem)
        if video is None:
            logger.warning("Vidéo introuvable pour un sujet approuvé : %s", stem)
            continue
        resultat.append((stem, video))

    resultat.sort(key=lambda x: x[1].stat().st_mtime, reverse=True)
    return resultat


def exporter(tout: bool = False) -> list[Path]:
    """
    Copie les vidéos éligibles vers le dossier synchronisé.

    Une vidéo déjà présente et de même taille est ignorée : recopier 14 Mo
    relancerait une synchronisation pour rien, et le client Drive téléverserait
    à nouveau le fichier.

    Returns:
        Chemins des fichiers effectivement copiés
    """
    destination = dossier_export()
    copies: list[Path] = []

    for stem, video in videos_a_exporter(tout):
        cible = destination / _nom_export(stem, video)
        if cible.exists() and cible.stat().st_size == video.stat().st_size:
            logger.debug("Déjà exportée : %s", cible.name)
            continue
        shutil.copy2(video, cible)
        copies.append(cible)
        logger.info("Exportée : %s (%d Mo)", cible.name, video.stat().st_size // 1024 // 1024)

    logger.info("%d vidéo(s) copiée(s) vers %s", len(copies), destination)
    return copies


def main() -> None:
    """Point d'entrée CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(
        description="Exporte les videos approuvees vers un dossier synchronise"
    )
    parser.add_argument(
        "--tout", action="store_true",
        help="Inclure les videos deja publiees, pas seulement les approuvees",
    )
    parser.add_argument(
        "--lister", action="store_true",
        help="Afficher ce qui serait exporte, sans rien copier",
    )
    args = parser.parse_args()

    if args.lister:
        eligibles = videos_a_exporter(args.tout)
        if not eligibles:
            print("Aucune vidéo approuvée. Valide-en depuis le dashboard.")
            return
        print(f"{len(eligibles)} vidéo(s) éligible(s) :")
        for stem, video in eligibles:
            taille = video.stat().st_size // 1024 // 1024
            print(f"  {_nom_export(stem, video):<70} {taille:>4} Mo")
        return

    copies = exporter(args.tout)
    print(f"{len(copies)} vidéo(s) exportée(s) vers {dossier_export()}")


if __name__ == "__main__":
    main()
