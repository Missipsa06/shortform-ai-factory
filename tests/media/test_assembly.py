"""
Tests de media/assembly.py : construction des listes du démuxeur concat FFmpeg.

`_ligne_concat` est un test de non-régression direct : un stem contenant une
élision française ("l'entraînement", "d'un modèle") cassait silencieusement
l'assemblage vidéo (apostrophes supprimées, chemin corrompu) avant que ce
comportement soit corrigé. Voir la docstring de `_ligne_concat`.
"""

from pathlib import Path

from media.assembly import _ligne_concat


def test_chemin_sans_apostrophe():
    ligne = _ligne_concat(Path("data/videos/manuel_rag/clips/slide_00.mp4"))
    assert ligne.startswith("file '")
    assert ligne.endswith("'\n")
    assert "manuel_rag" in ligne


def test_apostrophe_est_echappee():
    """
    Régression : une apostrophe non échappée fermait la citation FFmpeg en
    cours, et le démuxeur concat basculait en mode non cité pour le reste de
    la ligne — l'apostrophe disparaissait purement et simplement du chemin lu.
    """
    chemin = Path("data/processed/manuel_l'entraînement_d'un_modèle/clips/slide_00.mp4")
    ligne = _ligne_concat(chemin)

    # La syntaxe documentée par FFmpeg pour un guillemet simple littéral à
    # l'intérieur d'un champ cité : fermer, l'apostrophe littérale, rouvrir.
    assert "'\\''" in ligne
    # Le nom de fichier doit rester intact ailleurs dans la ligne : aucune
    # portion du chemin ne doit avoir disparu à cause de l'échappement.
    assert "entra" in ligne and "ment" in ligne
    assert "mod" in ligne


def test_plusieurs_apostrophes_dans_le_meme_chemin():
    chemin = Path("data/processed/manuel_l'un_d'eux_qu'on_a_choisi/clips/slide_00.mp4")
    ligne = _ligne_concat(chemin)
    assert ligne.count("'\\''") == 3


def test_ligne_reste_une_syntaxe_concat_valide():
    """La ligne produite doit garder la forme "file '...'" attendue par FFmpeg,
    apostrophes ou non."""
    ligne = _ligne_concat(Path("data/videos/x'y/clip.mp4"))
    assert ligne.startswith("file '")
    assert ligne.endswith("'\n")
    assert ligne.count("\n") == 1
