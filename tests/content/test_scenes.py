"""
Tests des scènes animées : orchestration, cache, repli, calage et prompt.

Ni Wan2GP ni FFmpeg : la génération, l'animation et le découpage sont imités.
"""

import json

import pytest

import content.scenes as scenes
from content.llm import _avec_scenes
from content.slides import _graphic_svg


def _script(tmp_path):
    script = {"hook": "H", "slides": [
        {"numero": 1, "titre": "A", "contenu": "x",
         "graphique": {"type": "scene", "donnees": {"action": "types", "mouvement": "stops"}}},
        {"numero": 2, "titre": "B", "contenu": "y",
         "graphique": {"type": "stat", "donnees": {"valeur": "95%"}}},
        {"numero": 3, "titre": "C", "contenu": "z",
         "graphique": {"type": "scene", "donnees": {"action": "reads", "mouvement": "smiles"}}},
    ], "conclusion": "F"}
    chemin = tmp_path / "sujet.json"
    chemin.write_text(json.dumps(script), encoding="utf-8")
    return chemin


@pytest.fixture
def wan2gp_imite(monkeypatch, tmp_path):
    """Compte les lancements ; chaque clip « découpé » donne trois images."""
    monkeypatch.setattr(scenes, "SCENES_DIR", tmp_path / "scenes")
    lancements: list[str] = []

    def images(prompts, sorties, **_):
        lancements.append("scenes")
        for s in sorties:
            s.parent.mkdir(parents=True, exist_ok=True)
            s.with_suffix(".png").write_text("x")
        return [s.with_suffix(".png") for s in sorties]

    def animer(fixes, mouvements, sorties):
        lancements.append("animation")
        return sorties

    def decouper(clip, dossier):
        dossier.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (dossier / f"f_{i:04d}.jpg").write_text("x")

    monkeypatch.setattr(scenes.wan2gp, "generer_images", images)
    monkeypatch.setattr(scenes.wan2gp, "animer", animer)
    monkeypatch.setattr(scenes, "_decouper", decouper)
    return lancements


def test_deux_lancements_pour_toute_la_video(wan2gp_imite, tmp_path):
    """Un lancement de Wan2GP par slide paierait le chargement du modèle à chaque fois."""
    chemin = _script(tmp_path)
    prets = scenes.preparer_scenes(chemin)
    assert wan2gp_imite == ["scenes", "animation"]
    # Identifiants de plan_slides, sans introduction : titre, 3 contenus, conclusion.
    assert set(prets) == {"slide_01", "slide_03"}
    script = json.loads(chemin.read_text(encoding="utf-8"))
    assert script["slides"][0]["graphique"]["donnees"]["fps"] == scenes.FPS
    assert "images" not in script["slides"][1]["graphique"]["donnees"]   # le stat reste un stat


def test_une_scene_en_cache_ne_relance_rien(wan2gp_imite, tmp_path):
    chemin = _script(tmp_path)
    scenes.preparer_scenes(chemin)
    scenes.preparer_scenes(chemin)
    assert wan2gp_imite == ["scenes", "animation"]


def test_sans_wan2gp_la_video_se_produit(monkeypatch, tmp_path):
    monkeypatch.setattr(scenes, "SCENES_DIR", tmp_path / "scenes")

    def absent(*_a, **_k):
        raise RuntimeError("Wan2GP indisponible : WAN2GP_DIR n'est pas défini")

    monkeypatch.setattr(scenes.wan2gp, "generer_images", absent)
    assert scenes.preparer_scenes(_script(tmp_path)) == {}


def test_le_clip_est_etire_sur_la_narration(tmp_path):
    """Joué à vitesse normale, l'événement montré précédait la phrase qui le dit."""
    for i in range(120):                                    # 5 s à 24 images/s
        (tmp_path / f"f_{i:04d}.jpg").write_text("x")
    donnees = {"images": str(tmp_path), "fps": 24}
    # 5 s de slide, 4,34 s utiles : plus court que le clip, aucun étirement.
    court = _graphic_svg({"type": "scene", "donnees": donnees}, duree_ms=5000)
    long = _graphic_svg({"type": "scene", "donnees": donnees}, duree_ms=30000)
    assert "fps: 24.0000" in court                          # slide courte : vitesse normale
    assert "fps: 12.0000" in long                           # plafond ×2


def test_le_prompt_ne_propose_les_scenes_qu_avec_wan2gp():
    gabarit = '"type": "stat|venn__TYPE_SCENE__"\n__SCENE__'
    assert "|scene" in _avec_scenes(gabarit, True)
    sans = _avec_scenes(gabarit, False)
    assert "scene" not in sans and "__" not in sans
