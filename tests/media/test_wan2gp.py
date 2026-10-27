"""
Tests du moteur Qwen3 via Wan2GP : prononciation, installation, file de tâches.

Ni Wan2GP ni FFmpeg ne tournent ici : `subprocess.run` est remplacé par une
imitation qui écrit les fichiers que Wan2GP aurait écrits, et la conversion en
MP3 par une copie. Ce qu'on éprouve est la correspondance texte ↔ fichier, dont
dépend toute la synchronisation de la vidéo.
"""

import json
import subprocess

import pytest

import media.wan2gp as wan2gp
from media.wan2gp import absence, prononcer


# ── Prononciation ─────────────────────────────────────────────────────────────

def test_tmux_est_lu_a_l_anglaise():
    """Qwen3 lisait « TMUKISS », « TEMX » ; « Ti-max » est entendu « T-Max »."""
    assert prononcer("Tmux détache l'agent") == "Ti-max détache l'agent"


def test_le_terme_le_plus_long_passe_en_premier():
    """« Claude » remplacé d'abord ferait de « Claude Code » un « Clôde Code »
    par accident, et casserait l'entrée dédiée."""
    assert prononcer("Claude Code et Claude") == "Clôde Code et Clôde"


def test_un_drapeau_de_commande_est_remplace():
    """Régression de l'essai du 24/10/2026 : un tiret en frontière de mot
    protégeait « --tmux » du remplacement."""
    assert prononcer("claude --worktree --tmux") == "Clôde --worktree --Ti-max"


def test_un_mot_qui_contient_un_terme_reste_intact():
    assert prononcer("Tmuxinator") == "Tmuxinator"


def test_les_entrees_de_commentaire_sont_ignorees():
    assert prononcer("_lisez-moi") == "_lisez-moi"


def test_le_s_du_pluriel_anglais_est_muet():
    """« des workflows » se dit « des workflow » : Qwen3 prononçait le s."""
    assert prononcer("Des botnets et des workflows.", ["botnets", "workflow"]) == \
        "Des botnet et des workflow."


def test_les_invariables_et_les_finales_en_us_gardent_leur_s():
    """« virus » ne devient pas « viru », ni « Kubernetes » « Kubernete »."""
    texte = "Un virus attaque Kubernetes via pandas."
    assert prononcer(texte, ["virus", "Kubernetes", "pandas"]) == texte


def test_sans_termes_anglais_aucun_pluriel_n_est_touche():
    assert prononcer("Des agents et des modèles.") == "Des agents et des modèles."


def test_chaque_segment_finit_par_une_pause(monkeypatch, tmp_path):
    """Qwen3 s'arrête sur son dernier mot : sans silence ajouté, la slide
    suivante enchaînait sans respiration."""
    vus: list[list[str]] = []
    monkeypatch.setattr(wan2gp.subprocess, "run",
                        lambda cmd, **_: vus.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    wan2gp._en_mp3(tmp_path / "a.wav", tmp_path / "a.mp3")
    assert f"apad=pad_dur={wan2gp.PAUSE_FIN_S}" in vus[0]


# ── Installation ──────────────────────────────────────────────────────────────

def _fausse_installation(racine, venv=".venv/Scripts/python.exe"):
    (racine / "wgp.py").write_text("")
    python = racine / venv
    python.parent.mkdir(parents=True)
    python.write_text("")
    return python


def test_sans_wan2gp_dir_le_moteur_se_dit_absent(monkeypatch):
    monkeypatch.delenv("WAN2GP_DIR", raising=False)
    assert "WAN2GP_DIR" in absence()


def test_un_dossier_sans_wgp_est_refuse(monkeypatch, tmp_path):
    monkeypatch.setenv("WAN2GP_DIR", str(tmp_path))
    assert "wgp.py" in absence()


@pytest.mark.parametrize("venv", [".venv/Scripts/python.exe", ".venv/bin/python"])
def test_une_installation_complete_est_reconnue(monkeypatch, tmp_path, venv):
    """Windows et Linux rangent l'interpréteur du venv différemment."""
    _fausse_installation(tmp_path, venv)
    monkeypatch.setenv("WAN2GP_DIR", str(tmp_path))
    assert absence() == ""


# ── File de tâches et correspondance des sorties ─────────────────────────────

@pytest.fixture
def wan2gp_imite(monkeypatch, tmp_path):
    """
    Installation factice, et un `subprocess.run` qui joue Wan2GP : il lit la
    file, écrit un fichier par tâche sous son `output_filename`, sauf pour les
    rangs listés dans `oublis`. Rend la liste des files reçues.
    """
    racine = tmp_path / "Wan2GP"
    racine.mkdir()
    _fausse_installation(racine)
    monkeypatch.setenv("WAN2GP_DIR", str(racine))
    recues: list[list[dict]] = []
    oublis: set[int] = set()

    def faux_run(cmd, **kwargs):
        taches = json.loads(open(cmd[cmd.index("--process") + 1], encoding="utf-8").read())
        recues.append(taches)
        sortie = cmd[cmd.index("--output-dir") + 1]
        for rang, tache in enumerate(taches):
            if rang not in oublis:
                open(f"{sortie}/{tache['output_filename']}.wav", "w").write(tache["prompt"])
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(wan2gp.subprocess, "run", faux_run)
    monkeypatch.setattr(wan2gp, "_en_mp3",
                        lambda source, sortie: sortie.write_text(source.read_text()))
    return recues, oublis


def test_chaque_texte_atterrit_dans_son_fichier(wan2gp_imite, tmp_path):
    """Deux segments qui commencent pareil ne doivent jamais s'échanger : c'est
    pour cela que chaque tâche est nommée par son rang."""
    recues, _ = wan2gp_imite
    textes = ["Tmux, premier.", "Tmux, second.", "Troisième."]
    sorties = [tmp_path / f"seg_{i}.mp3" for i in range(3)]

    wan2gp.synthetiser(textes, sorties, "charon")

    assert len(recues) == 1                          # un seul lancement pour tout
    assert [s.read_text() for s in sorties] == textes


def test_la_tache_porte_la_voix_et_sa_transcription(wan2gp_imite, tmp_path):
    recues, _ = wan2gp_imite
    wan2gp.synthetiser(["Bonjour."], [tmp_path / "a.mp3"], "charon")
    tache = recues[0][0]

    assert tache["model_type"] == "qwen3_tts_base"
    assert tache["model_mode"] == "french"
    assert tache["audio_guide"].endswith("charon.mp3")
    assert tache["alt_prompt"].startswith("Réfléchir plus longtemps")
    assert tache["seed"] == wan2gp.GRAINE


def test_un_segment_sans_audio_interrompt_tout(wan2gp_imite, tmp_path):
    """Poursuivre livrerait une bande son amputée, défaut qui ne s'entend
    qu'à la lecture."""
    _, oublis = wan2gp_imite
    oublis.add(1)
    with pytest.raises(RuntimeError, match="segment 2/2"):
        wan2gp.synthetiser(["Un.", "Deux."], [tmp_path / "a.mp3", tmp_path / "b.mp3"])


def test_une_voix_inconnue_est_refusee_avant_wan2gp(wan2gp_imite, tmp_path):
    recues, _ = wan2gp_imite
    with pytest.raises(ValueError, match="charon"):          # nomme les disponibles
        wan2gp.synthetiser(["Bonjour."], [tmp_path / "a.mp3"], "inexistante")
    assert recues == []


def test_chaque_image_prend_son_nom_et_son_format(wan2gp_imite, tmp_path):
    """Wan2GP choisit le format ; le chemin rendu doit porter le vrai suffixe,
    sans quoi la slide pointerait vers un fichier inexistant."""
    recues, _ = wan2gp_imite
    rendues = wan2gp.generer_images(["aube", "crépuscule"],
                                    [tmp_path / "slide_00", tmp_path / "slide_01"])
    assert [r.name for r in rendues] == ["slide_00.wav", "slide_01.wav"]  # suffixe de l'imitation
    assert [r.read_text() for r in rendues] == ["aube", "crépuscule"]
    assert recues[0][0]["model_type"] == wan2gp.MODELE_IMAGE
    assert recues[0][0]["resolution"] == wan2gp.RESOLUTION_IMAGE


def test_la_narration_qwen3_garde_un_fichier_par_segment(monkeypatch, tmp_path):
    """Le chemin de media/tts.py : textes prononcés, un MP3 par segment nommé
    d'après son identifiant, puis la concaténation dans l'ordre du script."""
    import media.tts as tts

    recu: dict = {}

    def faux_synthetiser(textes, sorties, voix):
        recu.update(textes=textes, sorties=sorties)
        for s in sorties:
            s.write_text("audio")

    monkeypatch.setattr(tts.wan2gp, "synthetiser", faux_synthetiser)
    monkeypatch.setattr(tts, "concatenate_segments", lambda fichiers, cible: cible)

    script = {"hook": "Tmux, vraiment ?", "slides": [{"numero": 1, "titre": "Un",
              "contenu": "Deux."}], "conclusion": "Fin."}
    final, fichiers = tts.generer_audio_qwen3(script, tmp_path / "seg",
                                              tmp_path / "final.mp3")

    assert [f.name for f in fichiers] == ["hook.mp3", "slide_01.mp3", "conclusion.mp3"]
    assert recu["textes"][0] == "Ti-max, vraiment ?"
    assert final == tmp_path / "final.mp3"
