"""
Vérifications préalables à `make up` : le daemon répond, et le disque est montable.

**Pourquoi tester le montage et pas seulement le daemon.** Docker Desktop monte
le disque du projet dans sa machine virtuelle WSL2 (`/run/desktop/mnt/host/d`).
Après une mise en veille de Windows, ce montage devient un point mort : `docker
info` répond normalement, mais tout conteneur qui monte `data/` échoue sur
« mkdir /run/desktop/mnt/host/d: file exists » — message qui ne dit ni la
cause, ni le remède. Constaté le 25/10/2026, après trois réveils de veille
traversés par le même Docker Desktop. Le remède est de détruire la VM : relancer
l'application ne suffit pas toujours, `wsl --shutdown` si.

Usage (via `make up`) :
    python -m automation.verifier_docker
"""

import re
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DONNEES = RACINE / "data"

REMEDE = """Le montage du disque est mort dans la VM de Docker Desktop, sans doute
apres une mise en veille de Windows. Docker repond, mais aucun conteneur ne peut
monter data/. Pour le reparer (les conteneurs en cours seront arretes) :

  Stop-Process -Name "Docker Desktop","com.docker.backend","com.docker.build" -Force
  wsl --shutdown
  Start-Process "C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe"

Attendre que `docker info` reponde (30 a 60 s), puis relancer make up."""


# Image construite par `make up` (Makefile, cible `up`). L'image de base du
# Dockerfile ne suffit pas : le build d'Astro ne la conserve pas en local.
IMAGE_DASHBOARD = "shortform-ai-factory-dashboard:latest"


def _image_de_base() -> str:
    """Image du `FROM` du Dockerfile, en second choix."""
    texte = (RACINE / "Dockerfile").read_text(encoding="utf-8")
    trouve = re.search(r"^FROM\s+(\S+)", texte, re.M)
    return trouve.group(1) if trouve else ""


def _image_locale() -> str:
    """Première image du projet présente en local ; vide au tout premier build."""
    for image in (IMAGE_DASHBOARD, _image_de_base()):
        if image and subprocess.run(["docker", "image", "inspect", image],
                                    capture_output=True).returncode == 0:
            return image
    return ""


def verifier() -> str:
    """
    Problème bloquant pour `make up`, ou chaîne vide si tout va bien.

    Seules deux pannes bloquent : le daemon absent, et le montage mort. Toute
    autre erreur du conteneur de test est ignorée — ce test ne doit arrêter que
    sur ce qu'il sait reconnaître, `make up` dira le reste.
    """
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        return ("Docker ne repond pas. Demarre Docker Desktop, attends 10 a 60 s, "
                "puis relance make up.")

    image = _image_locale()
    if not image:
        return ""  # premier build : aucune image à lancer, rien de périmé non plus

    DONNEES.mkdir(exist_ok=True)
    essai = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "true",
         "-v", f"{DONNEES}:/verification", image],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if essai.returncode != 0 and "/run/desktop/mnt/host" in (essai.stderr or ""):
        return REMEDE
    return ""


def main() -> None:
    probleme = verifier()
    if probleme:
        sys.exit(probleme)


if __name__ == "__main__":
    main()
