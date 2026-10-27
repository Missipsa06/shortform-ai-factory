"""
Tests de automation/verifier_docker.py, sans Docker : chaque commande est imitée.
"""

import subprocess

import automation.verifier_docker as verif


def _imiter(monkeypatch, *, info=0, image_presente=True, essai=(0, "")):
    """Remplace `docker` : info, image inspect, puis le conteneur d'essai."""
    def faux(cmd, **_):
        if cmd[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(cmd, info)
        if cmd[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(cmd, 0 if image_presente else 1)
        return subprocess.CompletedProcess(cmd, essai[0], stdout="", stderr=essai[1])

    monkeypatch.setattr(verif.subprocess, "run", faux)


def test_le_daemon_absent_est_signale(monkeypatch):
    _imiter(monkeypatch, info=1)
    assert "Docker ne repond pas" in verif.verifier()


def test_le_montage_mort_apres_veille_est_reconnu(monkeypatch):
    """Régression du 25/10/2026 : `docker info` répondait, `make up` échouait
    sur « mkdir /run/desktop/mnt/host/d: file exists »."""
    _imiter(monkeypatch, essai=(125, "error while creating mount source path "
                                     "'/run/desktop/mnt/host/d/Projects/x/data': "
                                     "mkdir /run/desktop/mnt/host/d: file exists"))
    assert "wsl --shutdown" in verif.verifier()


def test_une_autre_erreur_ne_bloque_pas(monkeypatch):
    """Le test n'arrête que sur ce qu'il sait reconnaître."""
    _imiter(monkeypatch, essai=(1, "exec: true: not found"))
    assert verif.verifier() == ""


def test_au_premier_build_le_test_est_saute(monkeypatch):
    _imiter(monkeypatch, image_presente=False)
    assert verif.verifier() == ""
