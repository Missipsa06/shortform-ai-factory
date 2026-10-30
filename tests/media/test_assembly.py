"""
Tests de media/assembly.py : construction des listes du démuxeur concat FFmpeg.

`_ligne_concat` est un test de non-régression direct : un stem contenant une
élision française ("l'entraînement", "d'un modèle") cassait silencieusement
l'assemblage vidéo (apostrophes supprimées, chemin corrompu) avant que ce
comportement soit corrigé. Voir la docstring de `_ligne_concat`.
"""

from pathlib import Path

import pytest

from media import assembly
from media.assembly import _ligne_concat, filtre_son, instants_de_transition


# ── Habillage sonore ──────────────────────────────────────────────────────────

def test_l_introduction_ne_declenche_pas_de_transition():
    """Elle prolonge l'accroche sur le même écran : pas de bruitage à 4 s."""
    genres = ["titre", "intro", "contenu", "contenu", "conclusion"]
    assert instants_de_transition(genres, [4.0, 3.0, 10.0, 8.0, 6.0]) == [7.0, 17.0, 25.0]


def test_un_bruitage_par_transition_sous_une_musique_comprimee_par_la_voix():
    graphe = filtre_son(30.0, musique=True, transitions=[7.0, 17.0])
    assert "sidechaincompress" in graphe
    assert graphe.count("adelay=") == 2 and "adelay=6880" in graphe   # 0,12 s avant la slide
    assert "amix=inputs=4" in graphe and graphe.count("channel_layouts=stereo") == 3


def test_sans_musique_le_bruitage_prend_la_deuxieme_entree():
    graphe = filtre_son(30.0, musique=False, transitions=[7.0])
    assert "sidechaincompress" not in graphe and "[2:a]" in graphe


@pytest.mark.parametrize("valeur", ["aucune", "AUCUN", "0", "off"])
def test_la_musique_se_coupe(monkeypatch, valeur):
    monkeypatch.setenv(assembly.MUSIQUE_ENV, valeur)
    assert assembly._son(assembly.MUSIQUE_ENV, assembly.MUSIQUE_DEFAUT) is None


def test_les_sons_par_defaut_sont_versionnes(monkeypatch):
    for env in (assembly.MUSIQUE_ENV, assembly.BRUITAGE_ENV):
        monkeypatch.delenv(env, raising=False)
    assert assembly._son(assembly.MUSIQUE_ENV, assembly.MUSIQUE_DEFAUT).exists()
    assert assembly._son(assembly.BRUITAGE_ENV, assembly.BRUITAGE_DEFAUT).exists()


def test_un_son_inconnu_laisse_la_voix_seule(monkeypatch):
    monkeypatch.setenv(assembly.MUSIQUE_ENV, "jazz_inexistant")
    assert assembly._son(assembly.MUSIQUE_ENV, assembly.MUSIQUE_DEFAUT) is None


# ── Démuxeur concat ───────────────────────────────────────────────────────────


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
