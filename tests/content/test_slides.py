"""
Tests de content/slides.py : pictogrammes, items et rendu SVG des figures.

Le rendu réel (PNG via Playwright) n'est pas testé ici — trop lent pour une
suite qui doit rester rapide, et déjà vérifié à l'œil pendant la session qui a
introduit les icônes, le cycle et la matrice 2x2 (voir CLAUDE.md). Ces tests
couvrent la génération SVG elle-même : structure, rétrocompatibilité,
dégradation propre sur données incomplètes.
"""

import pytest

import math
import re

from content.slides import (
    CADRE_MARGE,
    CANEVAS_H,
    ICONES,
    TEXTE,
    _bg_style,
    _bloc_texte,
    _cadence,
    _echapper,
    _fraction,
    _graphic_svg,
    _icone,
    _item,
    _lignes,
    _repartir,
    _svg_figure_source,
    plan_slides,
)

# ── Motion design ─────────────────────────────────────────────────────────────

def _mots(texte: str, debut: float = 1.0, pas: float = 0.3) -> list[dict]:
    return [{"mot": m, "debut": debut + i * pas, "fin": debut + (i + 1) * pas}
            for i, m in enumerate(texte.split())]


def test_l_anneau_se_remplit_quand_le_chiffre_est_prononce():
    """Fixé à 260 ms, le remplissage précédait la voix : le geste qui montre la
    part se jouait avant que la part soit dite."""
    mots = _mots("Le modèle atteint 95% de réussite")      # « 95% » à 1,9 s
    svg = _graphic_svg({"type": "stat", "donnees": {"valeur": "95%"}},
                       mots=mots, duree_ms=6000)
    assert re.search(r'class="remplit anim"[^>]*animation-delay:1900ms', svg)
    assert 'class="rebond anim" style="animation-delay:1900ms"' in svg


def test_sans_chiffre_prononce_l_anneau_garde_son_instant_par_defaut():
    svg = _graphic_svg({"type": "stat", "donnees": {"valeur": "95%"}},
                       mots=_mots("un texte sans nombre"), duree_ms=6000)
    assert "animation-delay:260ms" in svg
    assert "rebond" not in svg


TROIS_VALEURS = {"items": [{"label": "IA", "valeur": 92}, {"label": "Humain", "valeur": 78},
                           {"label": "Hasard", "valeur": 25}]}


def test_les_barres_poussent_apres_leur_bloc():
    svg = _graphic_svg({"type": "comparaison", "donnees": TROIS_VALEURS},
                       mots=_mots("l'IA obtient 92 contre 78 et 25"), duree_ms=6000)
    assert svg.count('class="pousse anim"') == 3


def test_sans_cadence_les_barres_restent_posees():
    """Sans datation, rien ne doit rester invisible à zéro."""
    assert "pousse" not in _graphic_svg({"type": "comparaison", "donnees": TROIS_VALEURS})


def _duel(a: float, b: float, unite: str = "") -> str:
    donnees = {"items": [{"label": "Avant", "valeur": a}, {"label": "Après", "valeur": b}],
               "unite": unite}
    return _graphic_svg({"type": "comparaison", "donnees": donnees},
                        mots=_mots(f"on passe de {a} à {b}"), duree_ms=6000)


def test_deux_parts_donnent_deux_jauges():
    svg = _duel(18.1, 71, "%")
    assert svg.count('class="remplit anim"') == 2
    assert "+52,9 pts" in svg


def test_un_grand_rapport_donne_le_saut_et_son_facteur():
    svg = _duel(18.1, 71)
    assert "×3,9" in svg and 'class="coule anim"' in svg
    assert "÷4" in _duel(80, 20)


def test_deux_valeurs_proches_donnent_des_colonnes_et_leur_ecart():
    svg = _duel(62.6, 66.5, "pts")
    assert svg.count('class="monte anim"') == 2
    assert "+3,9 pts" in svg


def test_un_duel_non_chiffre_retombe_sur_les_barres():
    donnees = {"items": [{"label": "A", "valeur": -5}, {"label": "B", "valeur": 10}]}
    svg = _graphic_svg({"type": "comparaison", "donnees": donnees},
                       mots=_mots("moins 5 contre 10"), duree_ms=6000)
    assert svg.count('class="pousse anim"') == 2


def test_le_titre_entre_mot_par_mot_et_reste_echappe():
    from content.slides import _titre_cinetique

    html = _titre_cinetique("R&D <et> IA", 100)
    assert html.count('class="mot-titre anim"') == 3
    assert "R&amp;D" in html and "&lt;et&gt;" in html
    assert "animation-delay:240ms" in html                  # 100 + 2 × 70


def test_les_mises_en_valeur_alternent_cyan_puis_violet():
    from content.slides import _mots_valorises

    mots = _mots_valorises("Ça marche **chez toi**, ça marche **chez eux** et **là**")
    classes = [c for mot in mots for _, c in mot if c]
    assert classes == ["val-accent", "val-accent", "val-violet", "val-violet", "val-accent"]
    # « **chez toi**, » : la virgule suit la mise en valeur sans en faire partie.
    assert ("toi", "val-accent") in mots[3] and (",", None) in mots[3]


def test_le_titre_valorise_reste_echappe_et_sans_asterisques():
    from content.slides import _texte_brut, _titre_cinetique

    html = _titre_cinetique("**R&D**, c'est <quoi> ?", 0)
    assert '<span class="val-accent">R&amp;D</span>' in html
    assert "&lt;quoi&gt;" in html and "**" not in html
    assert html.count('class="mot-titre anim"') == 3          # « ? » collé
    assert _texte_brut("**Ollama** en **deux**") == "Ollama en deux"


def test_morceaux_de_trois_mots_coupes_a_la_ponctuation():
    from content.slides import MORCEAU_MAX_CARS, _morceaux

    mots = "Ton ordinateur peut faire tourner une IA, sans internet. 2. Télécharger".split()
    groupes = _morceaux(mots)
    textes = [" ".join(mots[i] for i in g) for g in groupes]
    assert textes == ["Ton ordinateur", "peut faire tourner", "une IA,",
                      "sans internet.", "2. Télécharger"]
    assert all(len(t) <= MORCEAU_MAX_CARS for t in textes)
    assert _morceaux(["anticonstitutionnellement"]) == [[0]]   # un mot long reste seul


def test_sous_titres_un_morceau_a_la_fois_cales_sur_la_voix():
    from content.slides import _datation_sous_titres, _sous_titres_html

    datation = _datation_sous_titres("", _mots("un deux trois quatre", 1.0, 0.5), 6000)
    assert [t for _, t in datation] == [1000, 1500, 2000, 2500]
    html = _sous_titres_html(datation, 6000)
    assert html.count('class="morceau anim"') == 2
    # Premier morceau dès 0, le second à l'instant de « quatre », jusqu'à la fin.
    assert "@keyframes st0{0%{opacity:0}0.000%{opacity:1}41.667%{opacity:0}" in html
    assert "@keyframes st1{0%{opacity:0}41.667%{opacity:1}100.000%{opacity:0}" in html


def test_sans_datation_les_sous_titres_se_repartissent_et_s_echappent():
    from content.slides import SORTIE_MS, _datation_sous_titres, _sous_titres_html

    datation = _datation_sous_titres("a b <c> d", None, 4000 + SORTIE_MS)
    assert [t for _, t in datation] == [0, 1000, 2000, 3000]
    assert "&lt;c&gt;" in _sous_titres_html(datation, 4000)


def test_le_titre_prononce_precede_les_mots_dates():
    from content.slides import _datation_sous_titres

    datation = _datation_sous_titres("", _mots("contenu lu", 1.0), 6000, prefixe="Mon titre")
    assert datation[:2] == [("Mon", 0), ("titre", 500)]


def test_barre_de_progression_globale():
    from content.slides import _progres_html

    assert _progres_html(None, 5000) == ""
    html = _progres_html((5000, 20000), 5000)
    assert "scaleX(0.25000)" in html and "scaleX(0.50000)" in html


def test_la_progression_suit_le_plan_complet(tmp_path):
    import json

    from content.slides import generate_html_slides

    script = tmp_path / "s.json"
    script.write_text(json.dumps({"titre_video": "T", "hook": "h",
                                  "slides": [{"numero": 1, "titre": "A", "contenu": "c"}],
                                  "conclusion": "fin"}), encoding="utf-8")
    pages = generate_html_slides(script, durees_ms={"slide_00": 1000, "slide_01": 2000,
                                                    "slide_02": 1000})
    assert "scaleX(0.25000)" in pages[1].read_text(encoding="utf-8")
    # Une durée manque : pas de barre plutôt qu'une progression fausse.
    pages = generate_html_slides(script, durees_ms={"slide_00": 1000})
    assert 'class="progres' not in pages[1].read_text(encoding="utf-8")


def test_l_habillage_n_affiche_ni_badge_ni_compteur_ni_paragraphe():
    from content.slides import MARQUE, _slide_html

    html = _slide_html({"numero": 2, "titre": "T", "contenu": "Texte lu", "visuel": "🧠"},
                       6, duree_ms=6000)
    assert MARQUE not in html and "2/6" not in html and "🧠" not in html
    assert 'class="content' not in html and "soustitres" in html


def test_surtitre_et_sous_titre_seulement_sans_figure():
    from content.slides import _slide_html

    slide = {"titre": "T", "contenu": "c", "surtitre": "Outil", "sous_titre": "Ligne grise"}
    avec = _slide_html({**slide, "graphique": {"type": "liste",
                                               "donnees": {"items": ["a", "b"]}}}, 1)
    sans = _slide_html(slide, 1)
    assert "Outil" not in avec and "Ligne grise" not in avec
    assert "Outil" in sans and "Ligne grise" in sans


def test_l_introduction_prolonge_l_accroche_sans_coupure():
    """L'accroche ne sort pas, l'introduction n'entre pas : un seul écran."""
    from content.slides import DEJA_ENTRE_MS, _intro_slide_html, _title_slide_html

    script = {"titre_video": "Mon **sujet**", "hook": "h", "introduction": "i"}
    accroche = _title_slide_html(script, duree_ms=4000, enchainement=(0, 7000))
    intro = _intro_slide_html(script, duree_ms=3000, enchainement=(4000, 7000))
    assert 'class="slide anim sortie"' not in accroche
    assert 'class="slide anim sortie"' in intro              # la séquence se termine
    assert f"animation-delay:{250 + DEJA_ENTRE_MS}ms" in intro
    assert "animation-delay:-4000ms" in intro                # mascotte et fond en phase
    assert "animation-duration:7000ms" in intro and "Au programme" not in intro


def test_l_etat_par_defaut_precede_la_sortie():
    """L'aperçu PNG et le contrôle de mise en page lisent l'état au chargement :
    le dernier instant montrerait une slide déjà effacée."""
    from content.slides import SORTIE_MS, _slide_html

    html = _slide_html({"numero": 1, "titre": "T", "contenu": "C"}, 3, duree_ms=6000)
    assert f"animation-delay:{6000 - SORTIE_MS}ms" in html     # la sortie
    assert f"DUREE_MS - {SORTIE_MS}" in html                   # l'état par défaut


# ── Animations Lottie ─────────────────────────────────────────────────────────

def test_une_animation_embarque_ses_donnees_et_son_depart():
    """Les données sont injectées dans la page : un fetch serait refusé en file://."""
    html = _graphic_svg({"type": "animation", "donnees": {"nom": "valide"}})
    assert 'class="lottie"' in html
    assert '"nm":"valide"' in html                    # le JSON est dans la page
    assert "depart: 420" in html


def test_une_animation_inconnue_laisse_la_zone_vide(caplog):
    with caplog.at_level("WARNING"):
        assert _graphic_svg({"type": "animation", "donnees": {"nom": "absente"}}) == ""
    assert "valide" in caplog.text                    # nomme les disponibles


def test_seek_pilote_les_animations_et_la_bibliotheque_n_est_chargee_qu_au_besoin():
    """seek() fige Lottie comme les animations CSS : sans cela, l'animation
    jouerait en temps réel et le rendu cesserait d'être déterministe."""
    from content.slides import _slide_html

    avec = _slide_html({"numero": 1, "titre": "T", "contenu": "C",
                        "graphique": {"type": "animation", "donnees": {"nom": "valide"}}},
                       1, duree_ms=6000)
    sans = _slide_html({"numero": 1, "titre": "T", "contenu": "C"}, 1, duree_ms=6000)
    assert "goToAndStop" in avec and "lottie_light.min.js" in avec
    assert "lottie_light.min.js" not in sans


TYPES_FIGURES = [
    "stat", "etapes", "couches", "comparaison", "liste", "neurones", "cycle",
    "matrice", "frise", "embranchement", "venn",
]


# ── _echapper ─────────────────────────────────────────────────────────────────

def test_echapper_esperluette():
    """Une esperluette non échappée rendait le SVG invalide — vérifié contre le
    navigateur, qui l'affichait alors à moitié, sans erreur visible."""
    assert _echapper("recherche & synthèse") == "recherche &amp; synthèse"


def test_echapper_chevrons():
    assert _echapper("a < b > c") == "a &lt; b &gt; c"


def test_echapper_valeur_non_chaine():
    assert _echapper(95) == "95"


# ── _item ─────────────────────────────────────────────────────────────────────

def test_item_texte_simple_retrocompatible():
    """Les scripts produits avant l'introduction des icônes n'ont que des chaînes."""
    assert _item("Données") == ("Données", None)


def test_item_avec_icone_valide():
    assert _item({"texte": "Données", "icone": "donnees"}) == ("Données", "donnees")


def test_item_avec_icone_inconnue_est_ignoree():
    """Une icône hors vocabulaire est ignorée plutôt que de casser le rendu."""
    texte, icone = _item({"texte": "Données", "icone": "inexistante"})
    assert texte == "Données"
    assert icone is None


def test_item_dict_sans_icone():
    assert _item({"texte": "Point A"}) == ("Point A", None)


# ── _icone ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("slug", sorted(ICONES))
def test_chaque_icone_produit_un_groupe_svg_valide(slug):
    svg = _icone(slug, 50, 50, 40, "#00D4FF")
    assert svg.startswith("<g ")
    assert svg.endswith("</g>")
    assert 'stroke="#00D4FF"' in svg


def test_icone_inconnue_renvoie_chaine_vide():
    assert _icone("ne-existe-pas", 50, 50, 40, "#00D4FF") == ""


def test_icone_none_renvoie_chaine_vide():
    assert _icone(None, 50, 50, 40, "#00D4FF") == ""


# ── _repartir ─────────────────────────────────────────────────────────────────

def test_repartir_un_seul_bloc_centre():
    bloc, ecart, y0 = _repartir(900, 1, hauteur_max=200, ratio_ecart=0.2)
    assert bloc == 200
    assert y0 == (900 - 200) // 2


def test_repartir_plafonne_a_hauteur_max():
    """Beaucoup d'espace disponible ne doit pas produire des blocs géants."""
    bloc, _, _ = _repartir(3000, 2, hauteur_max=200, ratio_ecart=0.2)
    assert bloc == 200


def test_repartir_retrecit_si_peu_de_place():
    bloc, ecart, y0 = _repartir(300, 5, hauteur_max=200, ratio_ecart=0.2)
    assert bloc < 200
    assert y0 >= 0


# ── _cadence ──────────────────────────────────────────────────────────────────

def test_cadence_vide_sans_libelles():
    assert _cadence([], None, 6000) == []


def test_cadence_sans_mots_repartit_sur_la_premiere_moitie():
    instants = _cadence(["a", "b", "c"], None, 6000)
    assert len(instants) == 3
    assert all(i < 3000 for i in instants)
    assert instants == sorted(instants)


def test_cadence_avec_mots_dates():
    mots = [{"mot": f"m{i}", "debut": i * 0.5, "fin": i * 0.5 + 0.3} for i in range(10)]
    instants = _cadence(["a", "b"], mots, 8000)
    assert len(instants) == 2
    assert instants == sorted(instants)
    # Le dernier élément paraît aux 3/4 de la narration utile, pas à la toute
    # fin : la figure doit être complète pendant qu'on en parle encore.
    assert instants[-1] < 8000


# ── _graphic_svg : dégradation propre ────────────────────────────────────────

def test_graphique_absent_renvoie_chaine_vide():
    assert _graphic_svg(None) == ""


def test_type_inconnu_renvoie_chaine_vide():
    assert _graphic_svg({"type": "camembert", "donnees": {}}) == ""


@pytest.mark.parametrize("gtype", TYPES_FIGURES)
def test_donnees_vides_ne_leve_pas(gtype):
    """Chaque figure doit dégrader proprement sur des données incomplètes,
    jamais lever une exception qui interromprait le rendu de toute la slide."""
    _graphic_svg({"type": gtype, "donnees": {}})  # ne doit pas lever


# ── Rendu de chaque figure : structure SVG de base ───────────────────────────

DONNEES_VALIDES = {
    "stat": {"valeur": "95%", "description": "de précision"},
    "etapes": {"items": ["Données", "Entraînement", "Prédiction"]},
    "couches": {"items": ["Sortie", "Attention", "Encodage", "Entrée"]},
    "comparaison": {"items": [{"label": "IA", "valeur": 92}, {"label": "Humain", "valeur": 78}]},
    "liste": {"items": ["Point A", "Point B", "Point C"]},
    "neurones": {"couches": [3, 4, 2]},
    "cycle": {"items": ["Collecte", "Entraînement", "Évaluation", "Ajustement"]},
    "matrice": {
        "axe_x": "Rapidité", "axe_y": "Précision",
        "quadrants": ["Rapide et précis", "Rapide mais imprécis",
                      "Lent mais précis", "Lent et imprécis"],
    },
    "frise": {"items": [{"date": "2017", "texte": "Le Transformer"},
                        {"date": "2020", "texte": "GPT-3"},
                        {"date": "2024", "texte": "Raisonnement"}]},
    "embranchement": {
        "question": "Les données sont-elles étiquetées ?",
        "branches": ["Apprentissage supervisé", "Apprentissage non supervisé"],
    },
    "venn": {"gauche": "Intelligence artificielle", "droite": "Statistiques",
             "commun": "Apprendre des données"},
}


@pytest.mark.parametrize("gtype", TYPES_FIGURES)
def test_figure_valide_produit_un_svg_bien_forme(gtype):
    svg = _graphic_svg({"type": gtype, "donnees": DONNEES_VALIDES[gtype]}, w=970, h=900)
    assert svg.startswith("<svg")
    assert svg.strip().endswith("</svg>")
    assert svg.count("<svg") == 1


def test_matrice_avec_moins_de_quatre_quadrants_est_vide():
    """Une matrice 2x2 mal formée (moins de 4 quadrants) ne doit rien dessiner
    plutôt que produire une grille incomplète et trompeuse."""
    svg = _graphic_svg({"type": "matrice", "donnees": {"quadrants": ["A", "B", "C"]}})
    assert svg == ""


def test_cycle_avec_un_seul_item_est_vide():
    """Un cycle à un seul nœud ne représente pas une boucle."""
    svg = _graphic_svg({"type": "cycle", "donnees": {"items": ["Solo"]}})
    assert svg == ""


def test_comparaison_valeurs_identiques_nemet_pas_daccent(caplog):
    """Toutes les valeurs égales ne comparent rien : un WARNING signale le choix
    de figure inadapté plutôt que d'inventer une hiérarchie absente."""
    donnees = {"items": [{"label": "A", "valeur": 50}, {"label": "B", "valeur": 50}]}
    with caplog.at_level("WARNING"):
        _graphic_svg({"type": "comparaison", "donnees": donnees})
    assert any("écart" in r.message or "identiques" in r.message for r in caplog.records)


# ── _fraction : la part que l'anneau de `stat` doit remplir ──────────────────

@pytest.mark.parametrize("valeur,attendu", [
    ("95%", 0.95),
    ("95 %", 0.95),
    ("12%", 0.12),
    ("95,5%", 0.955),        # virgule décimale française
    ("99.9%", 0.999),
    ("0%", 0.0),
    ("3/4", 0.75),
    ("1/3", 1 / 3),
])
def test_fraction_valeurs_rapportees_a_un_total(valeur, attendu):
    assert _fraction(valeur) == pytest.approx(attendu)


@pytest.mark.parametrize("valeur", [
    "175 Md",                # une quantité, sans total
    "x3",
    "3x plus rapide",
    "95",                    # nombre nu : rien ne dit que le total est 100
    "?",
    "",
    "quelques millions",
])
def test_fraction_sans_denominateur_est_none(valeur):
    """Remplir l'anneau au prorata du premier chiffre lu inventerait une échelle
    absente des données : l'anneau reste entier et ne prétend rien mesurer."""
    assert _fraction(valeur) is None


def test_fraction_plafonnee_a_un_tour():
    """Au-delà de 100 %, une part n'a plus de traduction sur un cercle."""
    assert _fraction("150%") == 1.0


def test_fraction_denominateur_nul_est_none():
    assert _fraction("3/0") is None


def test_fraction_accepte_un_nombre_python():
    """Le LLM renvoie parfois un nombre là où le schéma attend une chaîne — sans
    signe de pourcentage, il reste sans total connu."""
    assert _fraction(95) is None


# ── `stat` : l'anneau mesure la valeur au lieu de l'encadrer ─────────────────

def _arc_et_tour(svg: str) -> tuple[float, float]:
    """Longueurs déclarées dans le style de l'arc de progression."""
    arc = re.search(r"--arc:([\d.]+)", svg)
    tour = re.search(r"--tour:([\d.]+)", svg)
    assert arc and tour, "l'arc de progression est absent du SVG"
    return float(arc.group(1)), float(tour.group(1))


def test_stat_pourcentage_remplit_larc_au_prorata():
    """
    Régression : l'anneau était un cercle entier quelle que soit la valeur, si
    bien que « 12 % » et « 95 % » donnaient exactement le même dessin. Le chiffre
    portait seul toute l'information et le cercle n'était que décoratif.
    """
    svg = _graphic_svg({"type": "stat", "donnees": {"valeur": "25%"}})
    arc, tour = _arc_et_tour(svg)
    assert arc == pytest.approx(tour * 0.25, rel=1e-3)


def test_stat_arc_et_rail_sont_tous_deux_traces():
    """Un arc court sans cercle de référence ne se compare à rien et cesse de se
    lire comme une part — même raison que le rail des barres de `comparaison`."""
    svg = _graphic_svg({"type": "stat", "donnees": {"valeur": "20%"}})
    assert 'class="remplit anim"' in svg    # l'arc, en dégradé
    assert '<circle class="rail"' in svg    # le tour entier derrière


def test_stat_cent_pour_cent_reste_un_cercle_tronque():
    """Un `path A` de 100 % rejoindrait son point de départ et ne dessinerait
    rien : le tracé doit rester un cercle tronqué par stroke-dasharray."""
    svg = _graphic_svg({"type": "stat", "donnees": {"valeur": "100%"}})
    arc, tour = _arc_et_tour(svg)
    assert arc == pytest.approx(tour, rel=1e-3)
    assert '<circle class="remplit anim"' in svg


def test_stat_zero_pour_cent_ne_trace_aucun_arc():
    """Les extrémités arrondies d'un arc nul le rendraient visible comme un point
    posé sur le rail, en contradiction avec la valeur affichée."""
    svg = _graphic_svg({"type": "stat", "donnees": {"valeur": "0%"}})
    assert 'class="remplit anim"' not in svg
    assert 'class="tete' not in svg         # ni tête lumineuse posée sur un arc nul
    assert '<circle class="rail"' in svg


def test_stat_sans_pourcentage_garde_lanneau_entier():
    donnees = {"valeur": "175 Md", "description": "de paramètres"}
    svg = _graphic_svg({"type": "stat", "donnees": donnees})
    assert 'class="remplit anim"' not in svg
    assert "--arc" not in svg
    assert 'class="allume' not in svg       # aucune graduation ne prétend mesurer


def test_stat_affiche_toujours_valeur_et_description():
    donnees = {"valeur": "95%", "description": "de précision"}
    svg = _graphic_svg({"type": "stat", "donnees": donnees})
    # Le signe % est un tspan réduit, collé au nombre.
    assert re.search(r">95<tspan[^>]*>%</tspan>", svg)
    assert "de précision" in svg


def test_stat_tour_vaut_la_circonference_du_rayon():
    """`--tour` doit valoir la circonférence réelle : sinon la part dessinée ne
    correspond plus à la valeur annoncée."""
    svg = _graphic_svg({"type": "stat", "donnees": {"valeur": "50%"}}, w=970, h=900)
    rayon = float(re.search(r'\sr="(\d+)"', svg).group(1))
    _, tour = _arc_et_tour(svg)
    assert tour == pytest.approx(2 * math.pi * rayon, rel=1e-3)


# ── Repère des schémas ───────────────────────────────────────────────────────

@pytest.mark.parametrize("gtype", TYPES_FIGURES)
def test_un_schema_dessine_dans_la_zone_de_reference(gtype):
    """
    Le viewBox couvre toujours la zone de `design/schemas/reference.html`,
    plus la bande des halos, quelle que soit la taille demandée : la figure
    dessine dans un repère fixe et le navigateur la met à l'échelle.
    """
    from content.schemas import ZONE_H, ZONE_W

    svg = _graphic_svg({"type": gtype, "donnees": DONNEES_VALIDES[gtype]})
    vue = re.search(r'viewBox="(-?\d+) (-?\d+) (\d+) (\d+)"', svg)
    x, y, largeur, hauteur = (int(g) for g in vue.groups())
    assert (x, y) == (-CADRE_MARGE, -CADRE_MARGE)
    assert (largeur, hauteur) == (ZONE_W + 2 * CADRE_MARGE, ZONE_H + 2 * CADRE_MARGE)
    # Le document, lui, prend la taille demandée.
    assert 'width="970"' in svg and f'height="{CANEVAS_H}"' in svg


def test_la_slide_donne_la_zone_haute_aux_schemas():
    """Dans la bande large, un schéma tenait en 950 × 550 et laissait 300 px
    vides au-dessus des sous-titres."""
    from content.slides import _slide_html

    schema = _slide_html({"titre": "T", "contenu": "c",
                          "graphique": {"type": "etapes", "donnees": DONNEES_VALIDES["etapes"]}}, 1)
    terminal = _slide_html({"titre": "T", "contenu": "c", "graphique": {
        "type": "terminal", "donnees": {"lignes": [{"cmd": "ls"}]}}}, 1)
    assert 'class="graphic haute anim"' in schema
    assert 'class="graphic anim"' in terminal


def test_les_schemas_nintroduisent_pas_de_groupe_transforme():
    """Un `<g transform>` d'enveloppe fausserait le comptage d'icônes, que ce
    motif sert justement à repérer dans plusieurs tests."""
    svg = _graphic_svg({"type": "etapes", "donnees": {"items": ["Sans icône"]}})
    assert "<g transform" not in svg


# ── _lignes / _bloc_texte : le repli que SVG ne fait pas ─────────────────────

def test_lignes_ne_coupe_pas_les_mots():
    assert _lignes("apprendre des données", 12) == ["apprendre", "des données"]


def test_lignes_texte_court_tient_sur_une_ligne():
    assert _lignes("IA", 12) == ["IA"]


def test_lignes_texte_vide():
    assert _lignes("", 12) == []


def test_lignes_mot_plus_long_que_la_largeur_reste_entier():
    """Couper un mot le rendrait illisible : il déborde, ce qui se voit et se
    corrige, au lieu d'être charcuté."""
    assert _lignes("Apprentissage", 6) == ["Apprentissage"]


def test_lignes_plafond_replie_le_reste_sur_la_derniere():
    """Un libellé tronqué en silence passerait en production ; trop long, il se
    voit au premier rendu."""
    lignes = _lignes("un deux trois quatre cinq six sept", 5, max_lignes=2)
    assert len(lignes) == 2
    assert " ".join(lignes).split() == "un deux trois quatre cinq six sept".split()


def test_bloc_texte_reduit_le_corps_pour_le_mot_le_plus_long():
    """Le corps se déduit du mot le plus long, pas de la longueur totale : SVG ne
    coupe pas les mots, un seul terme large déborde quelle que soit la découpe."""
    etroit = _bloc_texte("Apprentissage automatique", 100, 100, 200, TEXTE)
    large = _bloc_texte("Apprentissage automatique", 100, 100, 800, TEXTE)
    taille = lambda svg: int(re.search(r'font-size="(\d+)"', svg).group(1))
    assert taille(etroit) < taille(large)


def test_bloc_texte_vide_ne_produit_rien():
    assert _bloc_texte("", 100, 100, 200, TEXTE) == ""


# ── frise : une chronologie, pas une suite d'étapes ──────────────────────────

def test_frise_affiche_les_dates_et_les_libelles():
    svg = _graphic_svg({"type": "frise", "donnees": DONNEES_VALIDES["frise"]})
    for attendu in ("2017", "2020", "2024", "Le Transformer", "Raisonnement"):
        assert attendu in svg


def test_frise_sans_items_est_vide():
    assert _graphic_svg({"type": "frise", "donnees": {"items": []}}) == ""


def test_frise_accepte_un_jalon_sans_date():
    """Le LLM omet parfois la date : le jalon doit rester rendu, sans trou."""
    donnees = {"items": [{"texte": "Sans date"}, {"date": "2024", "texte": "Avec"}]}
    svg = _graphic_svg({"type": "frise", "donnees": donnees})
    assert "Sans date" in svg and "2024" in svg


def test_frise_accepte_des_items_en_texte_simple():
    """Rétrocompatibilité avec la forme d'item partagée par les autres figures."""
    svg = _graphic_svg({"type": "frise", "donnees": {"items": ["Un", "Deux"]}})
    assert "Un" in svg and "Deux" in svg


def test_frise_relie_les_jalons_par_une_colonne():
    """Sans colonne, ce sont des lignes empilées et non une chronologie."""
    svg = _graphic_svg({"type": "frise", "donnees": DONNEES_VALIDES["frise"]})
    assert 'class="coule anim"' in svg    # la colonne, où le temps s'écoule
    # Un jalon par item, et l'onde du plus récent.
    assert svg.count("<circle") == 3 + svg.count('class="onde anim"')
    assert svg.count('class="onde anim"') == 1


def test_frise_jalon_unique_na_pas_de_colonne():
    """Une colonne reliant un seul jalon pendrait dans le vide."""
    svg = _graphic_svg({"type": "frise", "donnees": {"items": [{"date": "2024", "texte": "Seul"}]}})
    assert "coule" not in svg


# ── embranchement : une question, plusieurs issues ───────────────────────────

def test_embranchement_rend_question_et_branches():
    svg = _graphic_svg({"type": "embranchement", "donnees": DONNEES_VALIDES["embranchement"]})
    assert "étiquetées" in svg
    assert "supervisé" in svg
    assert svg.count('class="carte"') == 3   # la question et ses deux issues


def test_embranchement_sans_question_est_vide():
    """Sans point de décision, la figure n'a plus de sujet."""
    donnees = {"branches": ["A", "B"]}
    assert _graphic_svg({"type": "embranchement", "donnees": donnees}) == ""


def test_embranchement_avec_une_seule_branche_est_vide():
    """Une issue unique ne bifurque pas : c'est « etapes »."""
    donnees = {"question": "Alors ?", "branches": ["Une seule"]}
    assert _graphic_svg({"type": "embranchement", "donnees": donnees}) == ""


def test_embranchement_a_trois_branches():
    donnees = {"question": "Quel type ?", "branches": ["Un", "Deux", "Trois"]}
    svg = _graphic_svg({"type": "embranchement", "donnees": donnees})
    assert svg.count('class="carte"') == 4


def test_embranchement_plafonne_a_trois_branches():
    """Au-delà, les cadres deviennent trop étroits pour porter un libellé."""
    donnees = {"question": "Quel type ?", "branches": ["A", "B", "C", "D", "E"]}
    svg = _graphic_svg({"type": "embranchement", "donnees": donnees})
    assert svg.count('class="carte"') == 4
    assert ">E<" not in svg


# ── venn : deux ensembles et leur recouvrement ───────────────────────────────

def test_venn_rend_les_deux_ensembles_et_le_commun():
    svg = _graphic_svg({"type": "venn", "donnees": DONNEES_VALIDES["venn"]})
    assert svg.count("<circle") == 2
    assert "Statistiques" in svg
    assert "Apprendre" in svg



def test_venn_zone_commune_porte_seule_le_degrade():
    """Les deux cercles sont les prémisses, le recouvrement la conclusion."""
    svg = _graphic_svg({"type": "venn", "donnees": DONNEES_VALIDES["venn"]})
    cercles = re.findall(r"<circle [^>]*>", svg)
    assert all("url(#gA)" not in c for c in cercles)    # aucun cercle en dégradé
    assert re.search(r'<path class="commun"[^>]*fill="url\(#gA\)"', svg)


def test_venn_sans_commun_reste_lisible():
    """Deux ensembles disjoints restent une figure valide, sans zone commune."""
    svg = _graphic_svg({"type": "venn", "donnees": {"gauche": "A", "droite": "B"}})
    assert svg.count("<circle") == 2
    assert 'class="commun"' not in svg


def test_venn_sans_second_ensemble_est_vide():
    """Un seul ensemble ne recouvre rien."""
    assert _graphic_svg({"type": "venn", "donnees": {"gauche": "A"}}) == ""


def test_venn_cercles_tiennent_dans_le_canevas():
    """Mesuré : à rayon maximal les cercles débordaient du canevas par la gauche,
    le libellé de l'ensemble se retrouvant hors cadre."""
    svg = _graphic_svg({"type": "venn", "donnees": DONNEES_VALIDES["venn"]}, w=970, h=900)
    cercles = re.findall(r'<circle cx="(\d+)" cy="(\d+)" r="(\d+)"', svg)
    assert len(cercles) == 2
    for cx, _, r in cercles:
        assert int(cx) - int(r) >= 0
        assert int(cx) + int(r) <= 970


# ── figure_source : la seule figure qui ne soit pas inventée ─────────────────

@pytest.fixture
def image_factice(tmp_path):
    fichier = tmp_path / "figure_02.png"
    fichier.write_bytes(b"\x89PNG\r\n\x1a\n")
    return fichier


def test_figure_source_affiche_image_legende_et_credit(image_factice):
    svg = _svg_figure_source({
        "image": str(image_factice),
        "legende": "Figure 1: Explainability Assistant system architecture.",
        "attribution": "arXiv:2609.11860v1",
    }, 922, 852)
    assert "<image href=" in svg
    assert "architecture" in svg
    assert "arXiv:2609.11860v1" in svg


def test_sans_attribution_rien_nest_rendu(image_factice):
    """
    Le crédit est la contrepartie assumée de l'usage : la licence par défaut
    d'arXiv accorde à arXiv un droit de diffusion, pas à un tiers. Une figure
    sans crédit ne doit donc pas atteindre l'écran.
    """
    svg = _svg_figure_source({"image": str(image_factice), "legende": "x"}, 922, 852)
    assert svg == ""


def test_image_absente_degrade_sans_lever(tmp_path):
    """Le téléchargement a pu échouer : le pipeline retombe alors sur un schéma."""
    donnees = {"image": str(tmp_path / "jamais.png"), "attribution": "arXiv:1"}
    assert _svg_figure_source(donnees, 922, 852) == ""


def test_sans_image_rien_nest_rendu():
    assert _svg_figure_source({"attribution": "arXiv:1"}, 922, 852) == ""


def test_le_chemin_est_encode_en_uri(tmp_path):
    """Même piège que le fond de slide : le chemin porte le stem du sujet, donc
    une apostrophe dès que le titre en contient une."""
    dossier = tmp_path / "veille_l'apprentissage"
    dossier.mkdir()
    fichier = dossier / "figure_00.png"
    fichier.write_bytes(b"\x89PNG")
    svg = _svg_figure_source({"image": str(fichier), "attribution": "arXiv:1"}, 922, 852)
    assert "%27" in svg
    assert "file://" in svg


def test_la_figure_nest_jamais_recadree(image_factice):
    """`meet` et non `slice` : une figure recadrée perd ses axes et ses libellés
    internes, c'est-à-dire ce qui la rend lisible."""
    svg = _svg_figure_source({"image": str(image_factice), "attribution": "a"}, 922, 852)
    assert 'preserveAspectRatio="xMidYMid meet"' in svg


def test_sans_legende_limage_gagne_la_place(image_factice):
    """La bande de légende n'est pas réservée quand l'article n'en fournit pas."""
    base = {"image": str(image_factice), "attribution": "arXiv:1"}
    avec = _svg_figure_source({**base, "legende": "Une légende"}, 922, 852)
    sans = _svg_figure_source(base, 922, 852)
    hauteur = lambda s: int(re.search(r'<image [^>]*height="(\d+)"', s).group(1))
    assert hauteur(sans) > hauteur(avec)


def test_figure_source_passe_par_le_dispatch(image_factice):
    graphique = {"type": "figure_source",
                 "donnees": {"image": str(image_factice), "attribution": "arXiv:1"}}
    assert "<image href=" in _graphic_svg(graphique)


def test_figure_source_nest_jamais_cadencee(image_factice):
    """Une image n'a pas d'éléments qui se posent l'un après l'autre."""
    mots = [{"mot": "x", "debut": 0.0, "fin": 0.3}]
    graphique = {"type": "figure_source",
                 "donnees": {"image": str(image_factice), "attribution": "arXiv:1"}}
    svg = _graphic_svg(graphique, mots=mots, duree_ms=8000)
    assert 'class="parait anim"' not in svg


# ── Fond de slide : le chemin d'illustration entre dans du CSS ───────────────

def test_chemin_avec_apostrophe_est_encode(tmp_path):
    """
    Régression : le chemin porte le stem du sujet, donc une apostrophe dès que le
    titre en contient une. Posée telle quelle dans `url('…')`, elle refermait la
    chaîne CSS et le navigateur jetait **toute la feuille de style** — plus de
    flex, plus de mise en page, `.slide` réduite à 88 px. Mesuré sur une vidéo
    entière rendue sans aucune mise en forme.
    """
    dossier = tmp_path / "veille_l'apprentissage"
    dossier.mkdir()
    image = dossier / "slide_00.jpg"
    image.write_bytes(b"\xff\xd8\xff")

    style = _bg_style(image)
    assert "%27" in style              # l'apostrophe est encodée
    # Une seule paire d'apostrophes, celle qui délimite l'url() du CSS.
    assert style.count("'") == 2


def test_chemin_avec_espace_est_encode(tmp_path):
    """Le dossier du projet lui-même contient une espace."""
    dossier = tmp_path / "un dossier"
    dossier.mkdir()
    image = dossier / "slide_00.jpg"
    image.write_bytes(b"\xff\xd8\xff")
    assert "%20" in _bg_style(image)


def test_le_schema_file_nest_pas_double(tmp_path):
    """`as_uri()` porte déjà « file:// » : le redoubler donnerait une url morte."""
    image = tmp_path / "slide_00.jpg"
    image.write_bytes(b"\xff\xd8\xff")
    assert _bg_style(image).count("file://") == 1


def test_sans_image_le_fond_est_un_degrade():
    """Un aplat n'offre aucune prise au mouvement de caméra."""
    style = _bg_style(None)
    assert "gradient" in style
    assert "url(" not in style


def test_image_absente_retombe_sur_le_degrade(tmp_path):
    assert "url(" not in _bg_style(tmp_path / "jamais_produite.jpg")


# ── Icônes intégrées dans les figures composites ─────────────────────────────

def test_etapes_avec_icones_les_inclut_dans_le_svg():
    donnees = {"items": [{"texte": "Données", "icone": "donnees"},
                          {"texte": "Résultat", "icone": "cible"}]}
    svg = _graphic_svg({"type": "etapes", "donnees": donnees})
    assert svg.count("<g transform") == 2  # une icône par item


def test_etapes_sans_icones_nen_contient_aucune():
    """Rétrocompatibilité : les scripts en simple texte ne doivent rien changer
    au rendu par rapport à avant l'introduction des icônes."""
    donnees = {"items": ["Données", "Entraînement", "Prédiction"]}
    svg = _graphic_svg({"type": "etapes", "donnees": donnees})
    assert "<g transform" not in svg


def test_liste_colonne_icone_tout_ou_rien():
    """Un mélange d'items avec et sans icône ne doit pas décider au hasard du
    poids visuel : soit tous les items réservent la colonne, soit aucun."""
    donnees = {"items": [{"texte": "Point A", "icone": "ampoule"}, "Point B"]}
    svg = _graphic_svg({"type": "liste", "donnees": donnees})
    assert svg.count("<g transform") == 1  # seul l'item avec icône en affiche une


# ── Construction cadencée (narration) ────────────────────────────────────────

@pytest.mark.parametrize("gtype", [
    "etapes", "couches", "comparaison", "liste", "neurones", "cycle", "matrice",
    "frise", "embranchement", "venn",
])
def test_figures_cadencees_animent_avec_datation(gtype):
    mots = [{"mot": f"m{i}", "debut": i * 0.4, "fin": i * 0.4 + 0.3} for i in range(20)]
    svg = _graphic_svg(
        {"type": gtype, "donnees": DONNEES_VALIDES[gtype]}, mots=mots, duree_ms=8000
    )
    # Le geste d'entrée varie (surgir, glisser, tomber) : seule la classe compte.
    assert re.search(r'class="parait( \w+)? anim"', svg)


def test_stat_nest_jamais_cadencee():
    """Un chiffre seul n'est pas une séquence : rien à cadencer."""
    mots = [{"mot": f"m{i}", "debut": i * 0.4, "fin": i * 0.4 + 0.3} for i in range(20)]
    svg = _graphic_svg(
        {"type": "stat", "donnees": DONNEES_VALIDES["stat"]}, mots=mots, duree_ms=8000
    )
    assert 'class="parait anim"' not in svg


@pytest.mark.parametrize("gtype", TYPES_FIGURES)
def test_sans_duree_ms_reste_statique(gtype):
    """duree_ms=0 (valeur par défaut) doit se comporter comme avant la cadence,
    même si des mots datés sont fournis par erreur."""
    mots = [{"mot": "x", "debut": 0.0, "fin": 0.3}]
    svg = _graphic_svg({"type": gtype, "donnees": DONNEES_VALIDES[gtype]}, mots=mots, duree_ms=0)
    assert 'class="parait' not in svg


# ── plan_slides (nécessaire aux figures cadencées en contexte réel) ──────────

def test_plan_slides_ordre_hook_puis_contenu():
    """`id` est un identifiant visuel séquentiel (slide_00, 01...) — c'est
    `segment` qui porte le nom sémantique reliant au fichier audio."""
    script = {
        "hook": "Accroche",
        "titre_video": "Titre",
        "introduction": "Intro",
        "slides": [{"numero": 1, "titre": "T1", "contenu": "C1", "graphique": None}],
        "conclusion": "Fin",
    }
    plan = plan_slides(script)
    genres = [p["genre"] for p in plan]
    segments = [p["segment"] for p in plan]
    ids = [p["id"] for p in plan]

    assert genres == ["titre", "intro", "contenu", "conclusion"]
    assert segments[0] == "hook"
    assert segments[-1] == "conclusion"
    # Les identifiants visuels sont séquentiels et uniques, indépendamment du
    # nom des segments audio.
    assert ids == [f"slide_{i:02d}" for i in range(len(plan))]


def test_plan_slides_sans_introduction():
    """Les scripts antérieurs à l'introduction n'en ont pas — le pipeline doit
    continuer à les traiter sans erreur."""
    script = {
        "hook": "Accroche",
        "slides": [{"numero": 1, "titre": "T1", "contenu": "C1"}],
        "conclusion": "Fin",
    }
    plan = plan_slides(script)
    assert "intro" not in [p["genre"] for p in plan]
