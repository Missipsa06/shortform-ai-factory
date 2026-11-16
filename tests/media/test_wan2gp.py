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


def test_un_terme_de_plusieurs_mots_ne_vaut_qu_entier():
    """Découpé, « Failure-Transparent Agents » faisait « des agent » (31/10/2026)."""
    termes = ["Failure-Transparent Agents", "pull request"]
    assert prononcer("Des agents et des pull requests.", termes) == \
        "Des agents et des pull request."


def test_un_nom_propre_garde_son_s():
    """« Janas » devenait « Jana » : déclaré avec son s, c'est un singulier."""
    assert prononcer("Janas charge les experts.", ["Janas", "experts"]) == \
        "Janas charge les expert."


@pytest.mark.parametrize("ecrit,dit", [
    ("30.1K heures de vidéo", "trente mille cent heures de vidéo"),
    ("663 articles, 7 500 visiteurs et 45000 vues", "663 articles, sept mille cinq cents visiteurs et quarante-cinq mille vues"),
    ("depuis 1997, 98,8 %", "depuis 1997, 98,8 %"),                         # années et décimales lues telles quelles
    ("1.5M de vues et 7B de paramètres", "1,5 million de vues et 7 milliards de paramètres"),
    ("un FID de 3.17 sur CIFAR", "un FID de 3,17 sur CIFAR"),
    ("le prix chute de -80 %", "le prix chute de moins 80 %"),
    ("Qwen gagne +13,9 points, x3 plus vite", "Qwen gagne plus 13,9 points, fois 3 plus vite"),
    ("GPT-5.6 et Qwen3.5 de 2024-2026", "GPT-5.6 et Qwen3.5 de 2024-2026"),   # des noms, pas des nombres
    ("88,6 % sur RoboTwin 2.0", "88,6 % sur RoboTwin 2.0"),                 # une version, pas une décimale
    ("Scores JevBench v1.3.0.", "Scores JevBench version 1 point 3 point 0."),
    ("52 000 tokens/s", "cinquante-deux mille tokens par seconde"),
    ("Une conversation = un tableau JSON", "Une conversation égale un tableau JSON"),
])
def test_les_nombres_s_ecrivent_comme_ils_se_disent(ecrit, dit):
    """Qwen3 lit ce qu'on lui donne : « 30.1K » était entendu « 30 points cas »."""
    assert prononcer(ecrit) == dit


@pytest.mark.parametrize("n,lettres", [
    (21, "vingt et un"), (71, "soixante et onze"), (77, "soixante-dix-sept"), (80, "quatre-vingts"),
    (81, "quatre-vingt-un"), (91, "quatre-vingt-onze"), (100, "cent"), (200, "deux cents"),
    (201, "deux cent un"), (1000, "mille"), (80000, "quatre-vingt mille"), (200000, "deux cent mille"),
    (30100, "trente mille cent"), (2_000_000, "deux millions"), (1_200_000_000, "un milliard deux cents millions"),
])
def test_les_entiers_s_ecrivent_en_toutes_lettres(n, lettres):
    assert wan2gp.en_lettres(n) == lettres


def _filtres_ffmpeg(monkeypatch, tmp_path) -> str:
    """Chaîne de filtres -af que `_en_mp3` passe à FFmpeg."""
    vus: list[list[str]] = []
    monkeypatch.setattr(wan2gp.subprocess, "run",
                        lambda cmd, **_: vus.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    wan2gp._en_mp3(tmp_path / "a.wav", tmp_path / "a.mp3")
    return vus[0][vus[0].index("-af") + 1]


def test_chaque_segment_commence_proprement(monkeypatch, tmp_path):
    """Attaque au premier échantillon, mesurée le 28/10/2026 : début entendu coupé."""
    filtres = _filtres_ffmpeg(monkeypatch, tmp_path)
    assert "afade=t=in" in filtres
    assert f"adelay={int(wan2gp.ATTAQUE_S * 1000)}" in filtres
    # Le rognage passe avant le délai, sinon il retirerait le silence ajouté.
    assert filtres.index("silenceremove") < filtres.index("adelay")


def test_chaque_segment_finit_par_une_pause(monkeypatch, tmp_path):
    """Qwen3 s'arrête sur son dernier mot : sans silence ajouté, la slide
    suivante enchaînait sans respiration."""
    assert f"apad=pad_dur={wan2gp.PAUSE_FIN_S}" in _filtres_ffmpeg(monkeypatch, tmp_path)


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
                        lambda source, sortie, timbre="": sortie.write_text(source.read_text()))
    return recues, oublis


def test_les_reglages_d_une_voix_suivent_son_extrait(monkeypatch, tmp_path):
    """Sans fichier, les défauts ; avec, l'échantillonnage et le timbre de l'extrait."""
    monkeypatch.setattr(wan2gp, "VOIX_DIR", tmp_path)
    assert wan2gp.reglages_voix("nue") == {"temperature": wan2gp.TEMPERATURE,
                                           "top_k": wan2gp.TOP_K, "timbre": ""}
    (tmp_path / "vive.json").write_text('{"temperature": 0.95, "top_k": 20, '
                                        '"timbre": "highshelf=f=6500:g=6"}', encoding="utf-8")
    assert wan2gp.reglages_voix("vive")["top_k"] == 20


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
    monkeypatch.setattr(tts, "_part_lue", lambda f, t, p: 1.0)

    script = {"hook": "Tmux, vraiment ?", "slides": [{"numero": 1, "titre": "Un",
              "contenu": "Deux."}], "conclusion": "Fin."}
    final, fichiers = tts.generer_audio_qwen3(script, tmp_path / "seg",
                                              tmp_path / "final.mp3")

    assert [f.name for f in fichiers] == ["hook.mp3", "slide_01.mp3", "conclusion.mp3"]
    assert recu["textes"][0] == "Ti-max, vraiment ?"
    assert final == tmp_path / "final.mp3"


def _relecture_imitee(monkeypatch, tmp_path, notes: dict[str, list[float]]):
    """Chaque prise écrit sa graine ; `notes[texte]` donne la lecture par prise."""
    import media.tts as tts

    lancements = []

    def faux_synthetiser(textes, sorties, voix, graine=wan2gp.GRAINE):
        lancements.append((list(textes), graine))
        for texte, s in zip(textes, sorties):
            s.write_text(f"{texte}|{graine}")

    def fausse_lecture(fichier, texte, prononce):
        _, graine = fichier.read_text().split("|")
        return notes[texte][int(graine) - wan2gp.GRAINE]

    monkeypatch.setattr(tts.wan2gp, "synthetiser", faux_synthetiser)
    monkeypatch.setattr(tts, "_part_lue", fausse_lecture)
    textes = list(notes)
    fichiers = [tmp_path / f"{i}.mp3" for i in range(len(textes))]
    faux_synthetiser(textes, fichiers, "animateur")
    lancements.clear()
    tts._reprendre_les_rates(fichiers, textes, textes, "animateur")
    return lancements, [f.read_text() for f in fichiers]


def test_seul_le_segment_mal_lu_est_refait_avec_une_autre_graine(monkeypatch, tmp_path):
    lancements, prises = _relecture_imitee(
        monkeypatch, tmp_path, {"bon": [0.95], "rate": [0.40, 0.92]})
    assert lancements == [(["rate"], wan2gp.GRAINE + 1)]
    assert prises == [f"bon|{wan2gp.GRAINE}", f"rate|{wan2gp.GRAINE + 1}"]


def test_la_meilleure_prise_reste_quand_aucune_ne_passe(monkeypatch, tmp_path):
    """Deux reprises au plus ; une reprise pire que l'original ne le remplace pas."""
    lancements, prises = _relecture_imitee(
        monkeypatch, tmp_path, {"rate": [0.50, 0.70, 0.30]})
    assert len(lancements) == 2
    assert prises == [f"rate|{wan2gp.GRAINE + 1}"]
