"""
Tests de content/lottie.py : recoloration, refus et lecture de page.

Aucun accès réseau : la page LottieFiles est imitée par un HTML minimal qui
reprend ce que la vraie page porte — un JSON-LD et des liens `lottieUrl`.
"""

import json

import pytest

import content.lottie as lottie
from content.lottie import (
    ACCENT,
    APLAT,
    TEXTE,
    ImportRefuse,
    controler,
    recolorer,
    sans_aplats_de_fond,
    vers_charte,
)


# ── Recoloration ──────────────────────────────────────────────────────────────

def test_une_couleur_vive_devient_le_seul_accent():
    """Rouge, vert, orange : la charte n'admet qu'un accent par figure."""
    assert vers_charte(1.0, 0.2, 0.2) == ACCENT
    assert vers_charte(0.2, 0.8, 0.3) == ACCENT


def test_les_gris_s_inversent_pour_le_fond_nuit():
    """Dessinée pour un fond clair : traits sombres → clairs, aplats blancs → sombres."""
    assert vers_charte(0.1, 0.1, 0.1) == TEXTE
    assert vers_charte(1.0, 1.0, 1.0) == APLAT


def test_recolorer_couvre_couleurs_fixes_animees_et_alpha():
    animation = {"layers": [{"shapes": [
        {"ty": "fl", "c": {"a": 0, "k": [1, 0, 0, 1]}},
        {"ty": "st", "c": {"a": 1, "k": [{"t": 0, "s": [0, 0, 0, 0.5]}]}},
        {"ty": "fl", "c": {"a": 0, "k": [255, 255, 255]}},          # ancien export 0-255
    ]}]}
    recolorer(animation)
    formes = animation["layers"][0]["shapes"]
    assert formes[0]["c"]["k"] == [*ACCENT, 1]
    assert formes[1]["c"]["k"][0]["s"] == [*TEXTE, 0.5]               # alpha gardé
    assert formes[2]["c"]["k"] == list(APLAT)


def test_les_fonds_solides_sont_retires():
    animation = {"layers": [{"ty": 1, "sc": "#ffffff"}, {"ty": 4}]}
    sans_aplats_de_fond(animation)
    assert animation["layers"] == [{"ty": 4}]


# ── Refus ─────────────────────────────────────────────────────────────────────

def test_une_animation_a_expressions_est_refusee():
    """La version allégée de lottie-web les ignore : rendu faussé sans message."""
    with pytest.raises(ImportRefuse, match="expressions"):
        controler({"layers": [{"ks": {"r": {"a": 0, "k": 0, "x": "time * 90"}}}]})


def test_une_animation_a_images_est_refusee():
    with pytest.raises(ImportRefuse, match="bitmap"):
        controler({"assets": [{"id": "i0", "p": "img.png", "u": "images/"}]})


def test_les_poignees_de_courbe_ne_passent_pas_pour_des_expressions():
    """`"x": [0.2]` est une poignée de courbe, pas du code."""
    controler({"layers": [{"ks": {"o": {"k": [{"i": {"x": [0.2], "y": [1]}}]}}}]})


# ── Lecture de la page ────────────────────────────────────────────────────────

def _page(licence: str = "Free to use under the Lottie Simple License") -> str:
    ld = {"@graph": [{"@type": "WebPage", "mainEntity": {
        "name": "Free Loupe Animation", "creator": {"name": "Auteur"},
        "contentUrl": "https://assets-v2.lottiefiles.com/a/"
                      "11111111-2222-3333-4444-555555555555/apercu.gif"}}]}
    # Le fichier d'une animation mise en avant précède celui de la bonne.
    return (
        f"<p>{licence}</p>"
        '<a href="?lottieUrl=https%3A%2F%2Fassets-v2.lottiefiles.com%2Fa%2F'
        'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee%2Fautre.lottie&x=1">'
        '<a href="?lottieUrl=https%3A%2F%2Fassets-v2.lottiefiles.com%2Fa%2F'
        '11111111-2222-3333-4444-555555555555%2Fbonne.lottie&x=1">'
        f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    )


class _Reponse:
    def __init__(self, texte: str):
        self.text = texte

    def raise_for_status(self) -> None:
        pass


def test_le_fichier_retenu_est_celui_de_l_animation_principale(monkeypatch):
    """Le premier lien d'une page peut appartenir à une animation mise en avant.
    Le bon est rangé dans le dossier de l'aperçu principal."""
    monkeypatch.setattr(lottie.requests, "get", lambda *a, **k: _Reponse(_page()))
    infos = lottie._lire_page("https://lottiefiles.com/free-animation/loupe")
    assert infos["fichier"].endswith("bonne.lottie")
    assert infos["auteur"] == "Auteur"


def test_une_autre_licence_refuse_l_import(monkeypatch):
    """Creative Commons ou conditions d'auteur : une lecture humaine d'abord."""
    monkeypatch.setattr(lottie.requests, "get",
                        lambda *a, **k: _Reponse(_page("Licensed under CC BY 4.0")))
    with pytest.raises(ImportRefuse, match="Lottie Simple License"):
        lottie._lire_page("https://lottiefiles.com/free-animation/loupe")


# ── Catalogue proposé au LLM ──────────────────────────────────────────────────

def test_chaque_animation_du_prompt_existe_et_porte_son_sens():
    from content.llm import SLIDE_PROMPT
    from content.slides import animations_disponibles, catalogue_animations

    catalogue = catalogue_animations()
    assert catalogue and set(catalogue) <= set(animations_disponibles())
    for nom, sens in catalogue.items():
        assert sens, nom
        assert f"- {nom} : {sens}" in SLIDE_PROMPT
