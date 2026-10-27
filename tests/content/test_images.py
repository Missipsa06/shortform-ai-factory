"""
Tests de content/images.py : choix de la requête envoyée au fournisseur.

Aucun appel réseau : seule la construction de la requête est vérifiée.
"""

from content.images import _requete

SCRIPT = {
    "titre_video": "L'IA ressuscite des secrets d'archives oubliés",
    "illustration_titre": "warm golden particulate aura in midnight blue mist",
    "mots_cles_image_titre": "golden particles mist",
}


def _entree(genre: str) -> dict:
    return {"genre": genre, "donnees": None}


def test_le_titre_nest_jamais_envoye_au_generateur():
    """
    Régression : l'accueil recevait le titre français de la vidéo, et le modèle
    l'a écrit en lettres déformées au milieu de l'image. « no text » ne retient
    pas un modèle de diffusion devant une phrase littérale.
    """
    for genre in ("intro", "conclusion"):
        requete = _requete(_entree(genre), SCRIPT, "cloudflare")
        assert "secrets" not in requete and "L'IA" not in requete
        assert requete.startswith(SCRIPT["illustration_titre"])


def test_titre_accueil_et_conclusion_restent_distincts():
    """Trois requêtes identiques donneraient trois fois le même visuel, et le
    filtre anti-doublon priverait deux slides de leur image."""
    requetes = {
        _requete(_entree("titre"), SCRIPT, "cloudflare"),
        _requete(_entree("intro"), SCRIPT, "cloudflare"),
        _requete(_entree("conclusion"), SCRIPT, "cloudflare"),
    }
    assert len(requetes) == 3


def test_sans_description_le_degrade_plutot_quun_faux_texte():
    script = {"titre_video": SCRIPT["titre_video"]}
    assert _requete(_entree("intro"), script, "cloudflare") == ""


def test_pexels_garde_le_titre_comme_recherche():
    """Une recherche de photographies ne dessine rien : le titre y reste utile."""
    assert "secrets" in _requete(_entree("intro"), SCRIPT, "pexels")


def test_la_generation_locale_ne_recoit_jamais_le_titre():
    """Même règle que Cloudflare : Z-Image dessinerait le titre en faux texte."""
    requete = _requete(_entree("intro"), SCRIPT, "wan2gp")
    assert "secrets" not in requete
    assert requete.startswith(SCRIPT["illustration_titre"])


# ── Génération locale par Wan2GP ──────────────────────────────────────────────

import pytest

import content.images as images
import media.wan2gp as wan2gp

SCRIPT_COMPLET = {
    "titre_video": "Titre", "illustration_titre": "aube froide",
    "slides": [{"numero": 1, "titre": "Un", "contenu": "x", "illustration": "anneaux"},
               {"numero": 2, "titre": "Deux", "contenu": "y", "illustration": "lignes"}],
    "conclusion": "Fin.",
}


@pytest.fixture
def generateur(monkeypatch, tmp_path):
    """Remplace Wan2GP : écrit un fichier par description, compte les lancements."""
    monkeypatch.setattr(images, "CACHE_FILE", tmp_path / "cache.json")
    lancements: list[list[str]] = []

    def faux(prompts, sorties):
        lancements.append(prompts)
        rendues = []
        for sortie in sorties:
            cible = sortie.with_suffix(".png")
            cible.parent.mkdir(parents=True, exist_ok=True)
            cible.write_text("image")
            rendues.append(cible)
        return rendues

    monkeypatch.setattr(wan2gp, "generer_images", faux)
    return lancements


def test_toute_la_video_part_en_un_seul_lancement(generateur, tmp_path):
    """Le chargement du modèle coûte plus qu'une image : un lancement par slide
    le paierait autant de fois qu'il y a de slides."""
    obtenues = images.fetch_slide_images(SCRIPT_COMPLET, tmp_path / "img", "wan2gp")
    assert len(generateur) == 1
    assert set(obtenues) == {"slide_00", "slide_01", "slide_02", "slide_03"}
    assert all(images.CF_STYLE in p for p in generateur[0])   # même registre partout


def test_une_video_deja_generee_ne_relance_pas_wan2gp(generateur, tmp_path):
    images.fetch_slide_images(SCRIPT_COMPLET, tmp_path / "img", "wan2gp")
    images.fetch_slide_images(SCRIPT_COMPLET, tmp_path / "img", "wan2gp")
    assert len(generateur) == 1


def test_sans_wan2gp_le_repli_passe_sur_pexels(monkeypatch, tmp_path):
    """Sous Docker, Wan2GP n'existe pas : une photo plutôt qu'un fond nu."""
    monkeypatch.setattr(images, "CACHE_FILE", tmp_path / "cache.json")

    def absent(prompts, sorties):
        raise RuntimeError("Wan2GP indisponible : WAN2GP_DIR n'est pas défini")

    monkeypatch.setattr(wan2gp, "generer_images", absent)
    vus: list[str] = []
    monkeypatch.setattr(images, "fetch_image",
                        lambda requete, d, nom, fournisseur, repli: vus.append(fournisseur))
    images.fetch_slide_images(SCRIPT_COMPLET, tmp_path / "img", "wan2gp", fallback=True)
    assert vus and set(vus) == {"pexels"}
