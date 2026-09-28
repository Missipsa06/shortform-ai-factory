"""
Lancement et suivi des exécutions du pipeline depuis l'interface.

Streamlit réexécute son script à chaque interaction : un objet `Popen` gardé en
mémoire ne survivrait pas, et une exécution de dix minutes figerait l'interface.
Les exécutions sont donc **détachées** et leur état vit sur disque — un journal
et un fichier de métadonnées par exécution. L'interface se contente de lire.

Chaque exécution est enveloppée par ce module lui-même
(`python -m validation.runs --executer <id>`), qui relaie la sortie vers le
journal et inscrit le code de retour à la fin. Sans cette enveloppe, un processus
détaché ne laisserait aucune trace de son issue.

Usage interne :
    python -m validation.runs --executer <id>
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
RUNS_DIR = RACINE / "data" / "runs"

EN_COURS = "en_cours"
TERMINE = "termine"
ECHEC = "echec"

# Au-delà, une exécution dont le journal ne bouge plus est considérée perdue :
# la machine s'est éteinte, ou le processus a été tué sans passer par la fin.
SILENCE_MAX_S = 900


def _chemin_meta(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.json"


def _chemin_journal(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.log"


def lancer(commande: list[str], libelle: str) -> str:
    """
    Démarre une exécution détachée et retourne son identifiant.

    Args:
        commande: Arguments passés à `python -m`, sans l'interpréteur
        libelle:  Description lisible, affichée dans l'interface

    Returns:
        Identifiant de l'exécution
    """
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    _chemin_meta(run_id).write_text(
        json.dumps(
            {
                "id": run_id,
                "libelle": libelle,
                "commande": commande,
                "debut": datetime.now().isoformat(timespec="seconds"),
                "statut": EN_COURS,
                "code": None,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _chemin_journal(run_id).write_text("", encoding="utf-8")

    # Détaché du processus Streamlit : fermer l'interface ne doit pas interrompre
    # une génération en cours.
    creation = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        [sys.executable, "-m", "validation.runs", "--executer", run_id],
        cwd=str(RACINE),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation,
    )
    return run_id


def lister() -> list[dict]:
    """Toutes les exécutions connues, de la plus récente à la plus ancienne."""
    if not RUNS_DIR.exists():
        return []
    executions = []
    for meta in RUNS_DIR.glob("*.json"):
        try:
            executions.append(_avec_etat(json.loads(meta.read_text(encoding="utf-8"))))
        except (ValueError, OSError):
            continue
    return sorted(executions, key=lambda e: e["id"], reverse=True)


def _avec_etat(meta: dict) -> dict:
    """
    Complète les métadonnées par un état déduit du journal.

    Une exécution marquée `en_cours` dont le journal ne bouge plus depuis
    longtemps est déclarée perdue : sans cela, une machine éteinte en cours de
    route laisserait une exécution éternellement « en cours ».
    """
    journal = _chemin_journal(meta["id"])
    meta["taille"] = journal.stat().st_size if journal.exists() else 0
    if meta.get("statut") == EN_COURS and journal.exists():
        silence = (datetime.now().timestamp() - journal.stat().st_mtime)
        if silence > SILENCE_MAX_S:
            meta["statut"] = ECHEC
            meta["code"] = "interrompu"
    return meta


def journal(run_id: str, lignes: int = 400) -> str:
    """Dernières lignes du journal d'une exécution."""
    chemin = _chemin_journal(run_id)
    if not chemin.exists():
        return ""
    contenu = chemin.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(contenu[-lignes:])


def supprimer(run_id: str) -> None:
    """Efface une exécution et son journal."""
    _chemin_meta(run_id).unlink(missing_ok=True)
    _chemin_journal(run_id).unlink(missing_ok=True)


def _executer(run_id: str) -> int:
    """
    Enveloppe : exécute la commande, relaie sa sortie, inscrit l'issue.

    Returns:
        Code de retour de la commande
    """
    meta_path = _chemin_meta(run_id)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    journal_path = _chemin_journal(run_id)

    env = dict(os.environ)
    # Sans cela, les accents des messages français se perdent dans le journal
    # sous Windows, dont la console est en cp1252.
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    with open(journal_path, "a", encoding="utf-8", errors="replace") as sortie:
        sortie.write(f"$ {' '.join(meta['commande'])}\n\n")
        sortie.flush()
        processus = subprocess.run(
            [sys.executable, "-m", *meta["commande"]],
            cwd=str(RACINE),
            stdout=sortie,
            stderr=subprocess.STDOUT,
            env=env,
        )

    meta["statut"] = TERMINE if processus.returncode == 0 else ECHEC
    meta["code"] = processus.returncode
    meta["fin"] = datetime.now().isoformat(timespec="seconds")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return processus.returncode


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--executer":
        sys.exit(_executer(sys.argv[2]))
    print(__doc__)
