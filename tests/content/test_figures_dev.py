"""Tests des figures « dev » : terminal qui se tape et récapitulatif."""

import re

from content.figures_dev import (
    COLONNES,
    DEBUT_MS,
    MARGE_FIN_MS,
    POLICE_MONO,
    figure_recap,
    figure_terminal,
)
from content.slides import SORTIE_MS

TERMINAL = {
    "chemin": "~/projet-ia",
    "lignes": [
        {"cmd": "curl -fsSL https://ollama.com/install.sh | sh"},
        {"ok": "Ollama installé"},
        {"cmd": "ollama pull llama3.2"},
        {"out": "pulling manifest"},
        {"ok": "success"},
    ],
    "depuis": 2,
}
RECAP = {"items": [
    {"code": "install.sh", "texte": "installer"},
    {"code": "ollama pull", "texte": "télécharger"},
    {"code": "ollama run", "texte": "discuter"},
]}


def _mots(texte: str, pas: float = 0.4) -> list[dict]:
    return [{"mot": m, "debut": i * pas, "fin": (i + 1) * pas}
            for i, m in enumerate(texte.split())]


def _delais(html: str) -> list[int]:
    """Tous les délais, y compris ceux des listes `a ms,b ms` (curseur)."""
    return [int(d) for liste in re.findall(r"animation-delay:([\d,ms]+)", html)
            for d in re.findall(r"\d+", liste)]


def _corps(html: str) -> str:
    """Le fragment sans sa feuille de style, qui nomme aussi les classes."""
    return html.split("</style>", 1)[1]


def _delais_lignes(html: str) -> list[int]:
    """Instant d'apparition de chaque ligne animée (pas de sa frappe ni de son curseur)."""
    return [int(d) for d in re.findall(
        r'<div class="dev-ligne[^"]*anim" style="[^"]*?animation-delay:(\d+)ms', html)]


def test_police_mono_embarquee_avec_licence():
    assert POLICE_MONO.is_file()
    assert (POLICE_MONO.parent / "LICENSE_JetBrainsMono.txt").is_file()


def test_terminal_apercu_sans_animation():
    html = _corps(figure_terminal(TERMINAL, 900, 900))
    assert "anim" not in html
    assert "dev-curseur" not in html
    assert html.count('class="dev-ligne') == 5
    assert "✓ success" in html and "~/projet-ia" in html and "Simulation" in html


def test_terminal_lignes_deja_presentes_non_animees():
    html = _corps(figure_terminal(TERMINAL, 900, 900, duree_ms=8000))
    corps = html.split('class="dev-corps">', 1)[1]
    lignes = re.findall(r'<div class="dev-ligne[^>]*>', corps)
    assert len(lignes) == 5
    assert all("anim" not in l for l in lignes[:2])
    assert all("anim" in l for l in lignes[2:])
    # Une seule commande nouvelle : une seule frappe, un seul curseur.
    assert html.count("dev-frappe anim") == 1 and html.count("dev-curseur") == 1


def test_terminal_sequence_dans_la_fenetre():
    duree = 8000
    html = figure_terminal(TERMINAL | {"depuis": 0}, 900, 900, duree_ms=duree)
    lignes = _delais_lignes(html)
    assert len(lignes) == 5 and lignes == sorted(lignes)
    delais = _delais(html)
    assert min(delais) >= DEBUT_MS
    # Tout est posé avant la sortie, curseur éteint compris.
    assert max(delais) <= duree - SORTIE_MS


def test_terminal_slide_courte_accelere_sans_deborder():
    duree = 2500
    html = figure_terminal(TERMINAL | {"depuis": 0}, 900, 900, duree_ms=duree)
    assert max(_delais(html)) <= duree - SORTIE_MS


def test_terminal_cale_la_commande_sur_le_mot_prononce():
    mots = _mots("Ensuite on télécharge un modèle avec la commande pull")
    html = figure_terminal(TERMINAL, 900, 900, mots=mots, duree_ms=9000)
    # « pull », neuvième mot, est prononcé à 3,2 s : la frappe démarre dessus.
    frappe = int(re.search(r'dev-frappe anim"[^>]*animation-delay:(\d+)ms', html).group(1))
    assert abs(frappe - 3200) <= 1


def test_terminal_echappe_les_libelles():
    html = figure_terminal({"lignes": [{"cmd": "echo <script>&"}],
                            "chemin": "<b>"}, 900, 900, duree_ms=5000)
    assert "<script>" not in html and "&lt;script&gt;&amp;" in html
    assert "&lt;b&gt;" in html


def test_terminal_largeur_de_frappe_en_caracteres():
    html = figure_terminal({"lignes": [{"cmd": "ollama run"}]}, 900, 900, duree_ms=5000)
    assert "width:calc(10 * 0.6em)" in html and "steps(10,end)" in html


def test_terminal_replie_les_commandes_longues_sans_retrecir():
    cmd = "curl -fsSL https://ollama.com/install.sh | sh"
    html = _corps(figure_terminal({"lignes": [{"cmd": cmd}]}, 900, 900, duree_ms=6000))
    largeurs = [int(n) for n in re.findall(r"width:calc\((\d+) \* 0\.6em\)", html)]
    # Deux rangées, chacune avec sa frappe et son curseur, texte complet conservé.
    assert len(largeurs) == 2 and sum(largeurs) == len(cmd) - 1  # une espace à la coupure
    assert all(n + 3 <= COLONNES for n in largeurs)
    assert html.count('class="dev-curseur') == 2
    frappes = [int(d) for d in re.findall(r'dev-frappe anim"[^>]*?animation-delay:(\d+)ms', html)]
    assert frappes == sorted(frappes) and frappes[0] < frappes[1]


def test_terminal_dimension_relative():
    html = figure_terminal(TERMINAL, 922, 900)
    assert "922px" not in html and "width:100%" in html


def test_terminal_vide_et_lignes_invalides():
    assert figure_terminal({}, 900, 900) == ""
    html = figure_terminal({"lignes": [{"cmd": "ls"}, {"autre": "x"}, "texte"]}, 900, 900)
    assert html.count('class="dev-ligne') == 2


def test_terminal_depuis_invalide_ramene_a_zero():
    html = figure_terminal(TERMINAL | {"depuis": "abc"}, 900, 900, duree_ms=8000)
    assert _corps(html).count("dev-cmd dev-montre anim") == 2


def test_recap_apercu_et_plafond():
    items = {"items": RECAP["items"] + [{"code": "a", "texte": "b"}, {"code": "c", "texte": "d"}]}
    html = _corps(figure_recap(items, 900, 900))
    assert html.count('class="dev-item') == 4
    assert "anim" not in html
    assert figure_recap({"items": []}, 900, 900) == ""


def test_recap_lignes_au_moment_prononce():
    mots = _mots("Retiens le réflexe : install pour installer, pull pour télécharger, run pour discuter.")
    html = figure_recap(RECAP, 900, 900, mots=mots, duree_ms=6000)
    # install (4), pull (7), run (10) à 0,4 s le mot.
    assert _delais(html) == [1600, 2800, 4000]


def test_recap_sans_datation_croissant_et_avant_sortie():
    duree = 6000
    delais = _delais(figure_recap(RECAP, 900, 900, duree_ms=duree))
    assert len(delais) == 3 and delais == sorted(delais)
    assert all(b - a >= 250 for a, b in zip(delais, delais[1:]))
    assert max(delais) <= duree - SORTIE_MS - MARGE_FIN_MS


def test_recap_echappe():
    html = figure_recap({"items": [{"code": "a&b", "texte": "<i>"}]}, 900, 900)
    assert "a&amp;b" in html and "&lt;i&gt;" in html
