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
