"""
Génération des slides 9:16 (1080x1920) à partir du script JSON.

Les slides sont du HTML/SVG rendu par un navigateur sans interface : le même
document sert à l'export PNG statique et au rendu vidéo animé, ce qui garantit
que l'aperçu et la vidéo montrent exactement la même chose.

Usage :
    python -m content.slides --input data/processed/manuel_rag.json
"""

import argparse
import json
import logging
import math
import re
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Charte des schémas ────────────────────────────────────────────────────────
#
# Les tailles sont calibrées pour un canevas de 970 px de large affiché en pleine
# largeur : un libellé à 32 px y paraissait plus petit que la légende du dessous,
# ce qui inversait la hiérarchie visuelle. La police est celle embarquée dans le
# dépôt, sinon le texte des schémas échapperait au rendu reproductible.
#
# Registre retenu : schéma technique épuré. Trois règles, qui suffisent à tenir
# la cohérence d'une figure à l'autre :
#
#   1. Une seule épaisseur de trait, des jointures arrondies.
#   2. Des formes ouvertes — le fond reste celui de la slide. Les cartes pleines
#      empilaient un second fond sur le premier et alourdissaient la figure.
#   3. Un seul accent coloré par figure, tout le reste en neutre. Faire tourner
#      la palette sur cinq teintes donnait un dessin bariolé, pas un schéma.

POLICE_SVG = "'Lecture', sans-serif"

# Conservée pour les figures pas encore reprises (comparaison, neurones).
PALETTE = ["#00D4FF", "#7B2FFF", "#FF6B6B", "#4EC94E", "#FFC107"]
FOND_CARTE = "#161630"


# Hauteur du canevas des schémas, ajustée à la place restante une fois les zones
# sûres de TikTok réservées (voir ZONE_SURE_* plus bas). Un canevas plus haut que
# son conteneur serait réduit à l'affichage et le texte des figures rétrécirait
# d'autant : c'est le rapport de forme qui compte, pas la valeur absolue.
CANEVAS_H = 900

TRAIT = 3
# Seule entorse à la règle « une épaisseur unique », et elle a un précédent : les
# barres de `comparaison` font déjà 30 px de haut. L'épaisseur est réservée à ce
# qui *porte une mesure*, jamais à ce qui l'encadre. Un anneau de progression à
# 3 px sur un rayon de 370 est une ligne de cheveu : la part y est vraie mais
# illisible en défilement, ce qui revient à ne pas l'avoir dessinée.
TRAIT_ANNEAU = 16
RAYON = 22
ACCENT = "#00D4FF"
NEUTRE = "#5C6A8A"       # traits et cadres secondaires
TEXTE = "#EAEEF7"
TEXTE_DOUX = "#95A1BC"


def _echapper(valeur: object) -> str:
    """
    Neutralise les caractères qui casseraient le SVG.

    Les libellés viennent du LLM : une esperluette dans « recherche & synthèse »
    suffit à produire un document invalide, que le navigateur rend alors à moitié.
    """
    return (
        str(valeur)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# Bande réservée tout autour de la zone utile, où se posent les repères d'angle.
# Les figures n'en savent rien et continuent de dessiner dans un repère qui part
# de (0, 0) : c'est le viewBox qui décale l'origine. Indispensable, car plusieurs
# d'entre elles occupent toute leur largeur — les cadres d'`etapes`, les barres
# de `comparaison`, la question d'`embranchement` — et un repère posé dans leur
# espace tomberait exactement sur l'angle de leur tracé.
CADRE_MARGE = 24


def _reperes(w: int, h: int) -> str:
    """
    Quatre équerres d'angle, dans le registre du plan technique.

    Le seul ornement du module, et il ne porte aucune information : il situe la
    figure dans un cadre. D'où l'opacité très basse et la couleur neutre — plus
    affirmées, les équerres concurrenceraient le trait de la figure au lieu de
    l'encadrer, et la charte n'admet qu'un accent par schéma.
    """
    bras, i = 32, TRAIT // 2 + 1
    x0, y0 = -CADRE_MARGE + i, -CADRE_MARGE + i
    x1, y1 = w + CADRE_MARGE - i, h + CADRE_MARGE - i
    coins = ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1))
    return "\n".join(
        f'  <path d="M {x} {y + sy * bras} L {x} {y} L {x + sx * bras} {y}" '
        f'fill="none" stroke="{NEUTRE}" stroke-width="{TRAIT}" stroke-linecap="round" '
        f'stroke-linejoin="round" opacity="0.28"/>'
        for x, y, sx, sy in coins
    )


def _svg(w: int, h: int, corps: str, defs: str = "") -> str:
    """
    Enveloppe SVG commune : pointes de flèche partagées et repères d'angle.

    `w` et `h` sont les dimensions **utiles**, celles dans lesquelles la figure
    dessine. Le document est plus grand de `CADRE_MARGE` de chaque côté, et son
    `viewBox` décale l'origine d'autant : la figure garde son repère habituel
    sans rien savoir de la marge. Un groupe translaté aurait fait la même chose,
    mais aurait ajouté un `<g transform>` que les icônes sont seules à produire.
    """
    total_w, total_h = w + 2 * CADRE_MARGE, h + 2 * CADRE_MARGE
    return f"""<svg width="{total_w}" height="{total_h}" viewBox="{-CADRE_MARGE} {-CADRE_MARGE} {total_w} {total_h}" xmlns="http://www.w3.org/2000/svg">
{_reperes(w, h)}
  <defs>
    <marker id="pointe" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="{NEUTRE}"/>
    </marker>
    <marker id="pointe-accent" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="{ACCENT}"/>
    </marker>{defs}
  </defs>
{corps}
</svg>"""


def _parait(corps: str, delai_ms: "int | None") -> str:
    """
    Enveloppe un groupe d'éléments pour qu'il paraisse d'un bloc à l'instant donné.

    Partagé entre toutes les figures cadencées : un cadre, une ligne de liste ou
    un bloc de comparaison doivent surgir comme une unité, jamais élément par
    élément, sinon la construction paraît hachée au lieu de suivre la voix.
    """
    if delai_ms is None:
        return corps
    return f'  <g class="parait anim" style="animation-delay:{delai_ms}ms">\n{corps}\n  </g>'


def _boite(x: int, y: int, w: int, h: int, label: str,
           accent: bool = False, indice: "int | None" = None,
           delai_ms: "int | None" = None, icone: "str | None" = None) -> str:
    """
    Cadre ouvert contenant un libellé centré.

    Args:
        x, y, w, h: Géométrie du cadre
        label:      Texte affiché, échappé ici
        accent:     Tracer le cadre dans la couleur d'accent
        indice:     Numéro affiché dans une pastille, en tête de cadre
        icone:      Pictogramme affiché en tête du cadre côté droit, facultatif
    """
    couleur = ACCENT if accent else NEUTRE
    cy = y + h // 2
    marge = 34
    x_texte = x + marge
    parts = [
        f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{RAYON}" fill="none" '
        f'stroke="{couleur}" stroke-width="{TRAIT}" stroke-linejoin="round"/>'
    ]
    if icone:
        # Toujours à droite, jamais entre la pastille et le texte : leur ordre de
        # lecture ne doit pas dépendre de la présence ou non d'un pictogramme.
        taille = min(h - 28, 64)
        parts.append(_icone(icone, x + w - 30 - taille / 2, cy, taille, couleur))
    if indice is not None:
        r = 26
        cx = x + marge + r
        parts.append(
            f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{couleur}" stroke-width="{TRAIT}"/>'
            f'\n  <text x="{cx}" y="{cy + 13}" text-anchor="middle" fill="{couleur}" '
            f'font-size="34" font-weight="700" font-family="{POLICE_SVG}">{indice}</text>'
        )
        x_texte = cx + r + 28
    parts.append(
        f'  <text x="{x_texte}" y="{cy + 16}" fill="{TEXTE}" font-size="46" '
        f'font-weight="600" font-family="{POLICE_SVG}">{_echapper(label)}</text>'
    )
    corps = "\n".join(parts)

    # Le groupe entier surgit d'un coup : cadre, pastille et libellé forment une
    # unité, les animer séparément donnerait un empilement de mouvements.
    return _parait(corps, delai_ms)


# ── Pictogrammes ─────────────────────────────────────────────────────────────
#
# Un item de figure ("Données", "Entraînement"...) n'était jusqu'ici qu'un mot
# dans un cadre. Les pictogrammes lui donnent un repère visuel immédiat, dans le
# même registre que le reste : trait unique, formes ouvertes, pas de remplissage.
# Chacun est dessiné dans un carré normalisé de 64x64, recoloré et redimensionné
# à l'affichage — jamais de couleur ni d'épaisseur figée dans le tracé lui-même.
ICONES: dict[str, str] = {
    # Deux lobes symétriques autour d'une scissure centrale, et quatre replis.
    # L'ancien tracé donnait une capsule verticale barrée d'un trait, que la
    # planche de contrôle a montrée illisible comme cerveau.
    "cerveau": (
        '<path d="M32 12c-6-4-15-2-17 5-6 2-8 9-4 14-3 6 1 12 8 13 2 5 9 6 13 2"/>'
        '<path d="M32 12c6-4 15-2 17 5 6 2 8 9 4 14 3 6-1 12-8 13-2 5-9 6-13 2"/>'
        '<line x1="32" y1="12" x2="32" y2="46"/>'
        '<path d="M24 22c4 1 5 5 2 7"/><path d="M40 22c-4 1-5 5-2 7"/>'
        '<path d="M24 34c4 1 5 5 2 7"/><path d="M40 34c-4 1-5 5-2 7"/>'
    ),
    "donnees": (
        '<ellipse cx="32" cy="16" rx="18" ry="7"/>'
        '<path d="M14 16v32c0 4 8 7 18 7s18-3 18-7V16"/>'
        '<path d="M14 32c0 4 8 7 18 7s18-3 18-7"/>'
    ),
    "reseau": (
        '<circle cx="32" cy="14" r="7"/><circle cx="14" cy="46" r="7"/>'
        '<circle cx="50" cy="46" r="7"/><line x1="32" y1="21" x2="17" y2="40"/>'
        '<line x1="32" y1="21" x2="47" y2="40"/><line x1="21" y1="46" x2="43" y2="46"/>'
    ),
    "croissance": (
        '<line x1="10" y1="54" x2="54" y2="54"/>'
        '<rect x="14" y="38" width="8" height="16"/>'
        '<rect x="28" y="26" width="8" height="28"/>'
        '<rect x="42" y="12" width="8" height="42"/>'
    ),
    "engrenage": (
        '<circle cx="32" cy="32" r="19"/><circle cx="32" cy="32" r="7"/>'
        '<line x1="52" y1="32" x2="59" y2="32"/>'
        '<line x1="46.1" y1="46.1" x2="51.1" y2="51.1"/>'
        '<line x1="32" y1="52" x2="32" y2="59"/>'
        '<line x1="17.9" y1="46.1" x2="12.9" y2="51.1"/>'
        '<line x1="12" y1="32" x2="5" y2="32"/>'
        '<line x1="17.9" y1="17.9" x2="12.9" y2="12.9"/>'
        '<line x1="32" y1="12" x2="32" y2="5"/>'
        '<line x1="46.1" y1="17.9" x2="51.1" y2="12.9"/>'
    ),
    "horloge": (
        '<circle cx="32" cy="32" r="22"/><line x1="32" y1="32" x2="32" y2="18"/>'
        '<line x1="32" y1="32" x2="45" y2="32"/>'
    ),
    "cible": '<circle cx="32" cy="32" r="22"/><circle cx="32" cy="32" r="13"/><circle cx="32" cy="32" r="4"/>',
    "ampoule": (
        '<circle cx="32" cy="24" r="14"/>'
        '<path d="M25 36h14M27 42h10M29 47h6"/>'
        '<path d="M26 18 L32 26 L38 18"/>'
    ),
    "bouclier": (
        '<path d="M32 8 L50 15 V30 C50 44 42 52 32 57 C22 52 14 44 14 30 V15 Z"/>'
        '<path d="M23 32 L29 39 L42 25"/>'
    ),
    "nuage": (
        '<path d="M18 42c-8 0-8-12 0-13 0-9 12-13 18-7 8-4 16 2 14 10'
        'c6 1 6 10 0 10z"/>'
    ),
    "robot": (
        '<rect x="16" y="18" width="32" height="28" rx="8"/>'
        '<circle cx="25" cy="32" r="4"/><circle cx="39" cy="32" r="4"/>'
        '<line x1="32" y1="18" x2="32" y2="10"/><circle cx="32" cy="8" r="3"/>'
        '<line x1="20" y1="46" x2="20" y2="52"/><line x1="44" y1="46" x2="44" y2="52"/>'
    ),
    "texte": (
        '<rect x="16" y="8" width="32" height="48" rx="4"/>'
        '<line x1="22" y1="20" x2="42" y2="20"/><line x1="22" y1="30" x2="42" y2="30"/>'
        '<line x1="22" y1="40" x2="36" y2="40"/>'
    ),
    "image": (
        '<rect x="10" y="14" width="44" height="36" rx="4"/>'
        '<circle cx="22" cy="26" r="5"/>'
        '<path d="M14 44 L26 32 L36 42 L44 34 L50 44"/>'
    ),
    "connexion": (
        '<rect x="8" y="24" width="26" height="16" rx="8" transform="rotate(-25 21 32)"/>'
        '<rect x="30" y="24" width="26" height="16" rx="8" transform="rotate(25 43 32)"/>'
    ),
    "eclair": '<path d="M36 6 L18 36 H30 L26 58 L48 26 H34 Z"/>',
    "oeil": (
        '<path d="M6 32c10-16 42-16 52 0-10 16-42 16-52 0z"/>'
        '<circle cx="32" cy="32" r="8"/>'
    ),
    # Second jeu, ajouté après avoir constaté combien de slides retombaient sur
    # l'absence d'icône : le vocabulaire initial couvrait le traitement de la
    # donnée mais ni la recherche, ni le langage, ni les limites d'un modèle.
    "code": (
        '<path d="M24 20 L10 32 L24 44"/><path d="M40 20 L54 32 L40 44"/>'
        '<line x1="36" y1="13" x2="28" y2="51"/>'
    ),
    "puce": (
        '<rect x="18" y="18" width="28" height="28" rx="4"/>'
        '<rect x="27" y="27" width="10" height="10"/>'
        '<line x1="26" y1="18" x2="26" y2="9"/><line x1="38" y1="18" x2="38" y2="9"/>'
        '<line x1="26" y1="46" x2="26" y2="55"/><line x1="38" y1="46" x2="38" y2="55"/>'
        '<line x1="18" y1="26" x2="9" y2="26"/><line x1="18" y1="38" x2="9" y2="38"/>'
        '<line x1="46" y1="26" x2="55" y2="26"/><line x1="46" y1="38" x2="55" y2="38"/>'
    ),
    "livre": (
        '<path d="M32 18c-5-5-13-6-20-5v32c7-1 15 0 20 5"/>'
        '<path d="M32 18c5-5 13-6 20-5v32c-7-1-15 0-20 5"/>'
    ),
    "vecteur": (
        '<path d="M12 52V10"/><path d="M12 52h42"/>'
        '<line x1="12" y1="52" x2="46" y2="18"/>'
        '<path d="M32 18 L46 18 L46 32"/>'
    ),
    "dialogue": (
        '<path d="M10 12h44a4 4 0 0 1 4 4v26a4 4 0 0 1-4 4H26L14 56V46h-4'
        'a4 4 0 0 1-4-4V16a4 4 0 0 1 4-4z"/>'
        '<line x1="18" y1="24" x2="46" y2="24"/>'
        '<line x1="18" y1="34" x2="38" y2="34"/>'
    ),
    "monde": (
        '<circle cx="32" cy="32" r="22"/><ellipse cx="32" cy="32" rx="9" ry="22"/>'
        '<line x1="10" y1="32" x2="54" y2="32"/>'
        '<path d="M14 20c10 5 26 5 36 0"/><path d="M14 44c10-5 26-5 36 0"/>'
    ),
    "loupe": '<circle cx="28" cy="28" r="16"/><line x1="40" y1="40" x2="54" y2="54"/>',
    "experience": (
        '<path d="M26 9v15L12 48a4 4 0 0 0 4 6h32a4 4 0 0 0 4-6L38 24V9"/>'
        '<line x1="22" y1="9" x2="42" y2="9"/><line x1="19" y1="38" x2="45" y2="38"/>'
    ),
    "filtre": '<path d="M8 12h48L38 34v18l-12 6V34z"/>',
    # Plateaux en triangles ouverts suspendus aux extrémités du fléau. La
    # première version les dessinait en demi-disques reliés par des suspentes :
    # à 82 px sur la planche de contrôle, les arcs se confondaient avec le mât
    # et la figure ne se lisait plus.
    "balance": (
        '<line x1="32" y1="12" x2="32" y2="50"/>'
        '<line x1="20" y1="50" x2="44" y2="50"/>'
        '<line x1="12" y1="20" x2="52" y2="20"/>'
        '<path d="M4 38 L12 20 L20 38 Z"/>'
        '<path d="M44 38 L52 20 L60 38 Z"/>'
    ),
    "utilisateur": (
        '<circle cx="32" cy="22" r="11"/>'
        '<path d="M12 54c0-11 9-18 20-18s20 7 20 18"/>'
    ),
    "alerte": (
        '<path d="M32 10 L58 52 H6 Z"/>'
        '<line x1="32" y1="26" x2="32" y2="38"/>'
        # Un cercle de rayon 1 deviendrait une pastille sous un trait de 4 :
        # un segment de longueur nulle à bout rond donne le point voulu.
        '<line x1="32" y1="45" x2="32" y2="45"/>'
    ),
    "fusee": (
        '<path d="M32 6c8 8 12 18 12 28l-12 10-12-10c0-10 4-20 12-28z"/>'
        '<circle cx="32" cy="26" r="5"/>'
        '<path d="M20 40l-6 14 12-6"/><path d="M44 40l6 14-12-6"/>'
    ),
    "micro": (
        '<rect x="24" y="8" width="16" height="28" rx="8"/>'
        '<path d="M14 30a18 18 0 0 0 36 0"/>'
        '<line x1="32" y1="48" x2="32" y2="56"/>'
    ),
    "camera": (
        '<rect x="6" y="18" width="40" height="28" rx="5"/>'
        '<path d="M46 30l12-8v20l-12-8z"/>'
    ),
    "calendrier": (
        '<rect x="8" y="14" width="48" height="42" rx="5"/>'
        '<line x1="8" y1="26" x2="56" y2="26"/>'
        '<line x1="20" y1="8" x2="20" y2="18"/><line x1="44" y1="8" x2="44" y2="18"/>'
    ),
}


def _icone(slug: "str | None", cx: float, cy: float, taille: float, couleur: str) -> str:
    """Pictogramme centré sur (cx, cy), redimensionné et recoloré à la volée."""
    contenu = ICONES.get(slug or "")
    if not contenu:
        return ""
    echelle = taille / 64
    x = cx - taille / 2
    y = cy - taille / 2
    return (
        f'<g transform="translate({x:.1f},{y:.1f}) scale({echelle:.3f})" '
        f'stroke="{couleur}" fill="none" stroke-width="4" '
        f'stroke-linecap="round" stroke-linejoin="round">{contenu}</g>'
    )


def _item(item: "str | dict") -> "tuple[str, str | None]":
    """
    Normalise un élément de figure en (texte, icône).

    Le LLM peut fournir soit un simple texte, soit {"texte", "icone"} — les
    scripts déjà produits n'ont que des chaînes, ce doit donc rester accepté.
    Une icône hors vocabulaire est ignorée plutôt que de casser le rendu.
    """
    if isinstance(item, dict):
        texte = str(item.get("texte", ""))
        icone = item.get("icone")
        return texte, (icone if icone in ICONES else None)
    return str(item), None


def _mots_insecables(texte: str) -> list[str]:
    """
    Découpe en mots, en collant au précédent toute ponctuation détachée.

    La typographie française sépare d'une espace les signes doubles : « Les
    données sont-elles étiquetées ? ». Découpé naïvement, le « ? » devient un mot
    à part entière et tombe seul sur la ligne suivante — mesuré sur la première
    question rendue par `embranchement`.
    """
    mots: list[str] = []
    for mot in str(texte).split():
        if mots and all(c in "?!:;«»…" for c in mot):
            mots[-1] = f"{mots[-1]} {mot}"
        else:
            mots.append(mot)
    return mots


def _lignes(texte: str, par_ligne: int, max_lignes: int = 3) -> list[str]:
    """
    Découpe un texte en lignes d'au plus `par_ligne` caractères, sans couper de mot.

    SVG ne replie pas le texte : un libellé de phrase déborde du canevas sans le
    moindre message. Les figures à libellé court (`etapes`, `liste`) s'en passent,
    mais la zone commune d'un `venn` est étroite par construction et n'accueille
    presque jamais un seul mot.

    Au-delà du plafond de lignes, le reste est replié sur la dernière plutôt que
    perdu : un libellé trop long se voit et se corrige, un libellé tronqué en
    silence passe en production.
    """
    lignes: list[str] = []
    courante = ""
    for mot in _mots_insecables(texte):
        essai = f"{courante} {mot}".strip()
        if courante and len(essai) > par_ligne:
            lignes.append(courante)
            courante = mot
        else:
            courante = essai
    if courante:
        lignes.append(courante)
    if len(lignes) > max_lignes:
        lignes = lignes[: max_lignes - 1] + [" ".join(lignes[max_lignes - 1:])]
    return lignes


def _texte_lignes(lignes: list[str], x: float, y: float, corps: int, couleur: str,
                  ancre: str = "middle", poids: int = 600) -> str:
    """Bloc de plusieurs lignes, centré verticalement sur y."""
    if not lignes:
        return ""
    pas = corps * 1.18
    # 0,35 em sous la ligne de base : même correction que partout ailleurs dans
    # ce module pour centrer un texte sur une ordonnée plutôt que l'y poser.
    y0 = y - (len(lignes) - 1) * pas / 2 + corps * 0.35
    tspans = "".join(
        f'<tspan x="{x:.1f}" y="{y0 + i * pas:.1f}">{_echapper(ligne)}</tspan>'
        for i, ligne in enumerate(lignes)
    )
    return (
        f'  <text text-anchor="{ancre}" fill="{couleur}" font-size="{corps}" '
        f'font-weight="{poids}" font-family="{POLICE_SVG}">{tspans}</text>'
    )


def _bloc_texte(texte: str, x: float, y: float, dispo: float, couleur: str,
                plafond: int = 42, plancher: int = 22, max_lignes: int = 3,
                ancre: str = "middle", poids: int = 600) -> str:
    """
    Texte replié et dimensionné pour tenir dans une largeur donnée.

    Le corps se déduit du **mot le plus long**, et non de la longueur totale :
    SVG ne coupe pas les mots, donc un seul terme trop large déborde quelle que
    soit la découpe en lignes. « Apprentissage » dans une zone de 240 px impose
    à lui seul le corps du bloc entier.

    Même principe que les libellés latéraux de `cycle` et que le chiffre de
    `stat` : la taille vient de la place réellement disponible.
    """
    mots = _mots_insecables(texte)
    if not mots:
        return ""
    plus_long = max(len(m) for m in mots)
    corps = max(plancher, min(plafond, int(dispo / (plus_long * 0.58))))
    par_ligne = max(plus_long, int(dispo / (corps * 0.58)))
    return _texte_lignes(_lignes(texte, par_ligne, max_lignes), x, y, corps,
                         couleur, ancre=ancre, poids=poids)


def _plier(texte: str) -> str:
    """Forme comparable d'un mot : minuscules, sans accents ni ponctuation."""
    import unicodedata

    plie = unicodedata.normalize("NFD", str(texte).lower())
    plie = "".join(c for c in plie if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w]", "", plie)


def _cadence(
    libelles: list[str], mots: "list[dict] | None", duree_ms: int
) -> list[int]:
    """
    Instant d'apparition de chaque élément d'une figure, en millisecondes.

    Le principe : **le schéma se construit au rythme de la parole**. Quand la
    narration dit « recherche documents », le cadre correspondant apparaît. Les
    horodatages viennent de `media/align.py`, qui date déjà chaque mot pour le
    karaoké — cette donnée ne servait qu'au texte du bas.

    Sans horodatage exploitable, on répartit les éléments sur la première moitié
    de la slide : une figure ne doit jamais rester invisible parce que le LLM a
    choisi d'autres mots que ses propres libellés.

    Args:
        libelles: Textes des éléments, dans l'ordre d'affichage
        mots:     [{'mot', 'debut', 'fin'}] de la narration de cette slide
        duree_ms: Durée de la slide

    Returns:
        Un instant par libellé, croissant
    """
    n = len(libelles)
    if not n:
        return []

    # Sans narration datée, répartition sur l'horloge, première moitié de slide.
    if not mots:
        return [int((i + 1) * (duree_ms * 0.5) / (n + 1)) for i in range(n)]

    # Cadence sur le **débit de parole** plutôt que sur les libellés eux-mêmes.
    #
    # L'appariement mot à mot semblait la voie évidente : faire paraître « Casque
    # VR » quand la voix dit « casque ». Mesuré sur de vraies narrations, il rate
    # le plus souvent — le LLM écrit des libellés qui *résument* la phrase, sans
    # en reprendre les mots. Une slide parlait de « Meta Quest 3 » et de « Mac
    # Mini » là où la figure affichait « Casque VR » et « Connexion USB ». Les
    # rares correspondances tombaient tard et tassaient tout le reste en fin de
    # slide, produisant l'inverse de l'effet recherché.
    #
    # Découper la narration en n tranches égales donne un résultat toujours
    # défendable : la figure se construit au rythme où l'on parle, accélérant
    # quand le débit accélère, sans dépendre du vocabulaire choisi.
    debuts = [m.get("debut", 0.0) for m in mots if m.get("debut") is not None]
    if not debuts:
        return [int((i + 1) * (duree_ms * 0.5) / (n + 1)) for i in range(n)]

    # Le dernier élément paraît aux trois quarts de la narration : la figure doit
    # être complète pendant qu'on en parle encore, pas au moment du silence final.
    utile = debuts[: max(1, int(len(debuts) * 0.75))]
    return [
        int(utile[min(int(i * len(utile) / n), len(utile) - 1)] * 1000)
        for i in range(n)
    ]


def _fleche(x1: int, y1: int, x2: int, y2: int, delai_ms: "int | None" = None) -> str:
    """Segment terminé par une pointe, dans la couleur neutre."""
    return (
        f'  <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{NEUTRE}" '
        f'stroke-width="{TRAIT}" stroke-linecap="round" marker-end="url(#pointe)"'
        + (
            # Le trait se dessine de son origine vers sa pointe : `stroke-dashoffset`
            # animé de sa longueur vers zéro. La longueur est calculée ici plutôt
            # que mesurée dans le navigateur, la flèche étant toujours droite.
            f' class="trace anim" style="--long:{abs(y2 - y1) + abs(x2 - x1)};'
            f'animation-delay:{delai_ms}ms"'
            if delai_ms is not None else ""
        )
        + "/>"
    )


def _fraction(valeur: object) -> "float | None":
    """
    Part de l'anneau à remplir, déduite de la valeur affichée.

    Ne renvoie un nombre que si la valeur porte **son propre dénominateur** : un
    pourcentage, ou une fraction écrite « 3/4 ». « 175 Md de paramètres » ou
    « x3 » n'en ont aucun — remplir l'anneau aux trois quarts parce que le nombre
    commence par 3 inventerait une échelle absente des données. Un nombre nu
    (« 95 », sans signe) est écarté pour la même raison : rien ne dit que le
    total est 100.

    Au-delà de 100 %, la part est ramenée à 1 : l'anneau est alors plein, ce qui
    reste honnête, là où une part supérieure au tour n'a aucune traduction.

    Returns:
        Part entre 0 et 1, ou None si la valeur ne se rapporte à aucun total
    """
    texte = str(valeur).strip()
    nombre = r"\d+(?:[.,]\d+)?"

    fraction = re.fullmatch(rf"({nombre})\s*/\s*({nombre})", texte)
    if fraction:
        num = float(fraction.group(1).replace(",", "."))
        den = float(fraction.group(2).replace(",", "."))
        return min(1.0, num / den) if den else None

    if "%" in texte:
        trouve = re.search(nombre, texte)
        if trouve:
            return min(1.0, float(trouve.group(0).replace(",", ".")) / 100)
    return None


def _instant_du_chiffre(valeur: object, mots: "list[dict] | None") -> "int | None":
    """
    Instant, en millisecondes, où la narration prononce les chiffres de `valeur`.

    Comparaison sur les seuls chiffres : « 95% » dans la figure retrouve « 95 % »
    ou « 95%, » dans le texte lu. Le karaoké affiche le texte du script, donc les
    chiffres y sont écrits en chiffres. Rien trouvé : None, et l'appelant garde
    son instant par défaut.
    """
    chiffres = re.sub(r"\D", "", str(valeur))
    if not chiffres or not mots:
        return None
    for mot in mots:
        if re.sub(r"\D", "", str(mot.get("mot", ""))) == chiffres:
            return int(mot.get("debut", 0) * 1000)
    return None


def _svg_stat(
    donnees: dict, w: int, h: int,
    mots: "list[dict] | None" = None, duree_ms: int = 0,
) -> str:
    """
    Un chiffre clé, cerclé d'un anneau ouvert.

    Le halo et le disque plein d'une version précédente empilaient deux fonds sur
    celui de la slide. Ici l'anneau n'est qu'un trait : le chiffre porte seul,
    ce qui est bien son rôle.

    **L'anneau mesure la valeur au lieu de l'encadrer.** Quand celle-ci porte son
    propre dénominateur, il se remplit jusqu'à cette part et devient la figure
    elle-même : « 12 % » et « 95 % » donnaient auparavant exactement le même
    dessin, un cercle entier dans les deux cas. Le chiffre portait donc seul toute
    l'information et le cercle n'était que décoratif — c'est ce qui faisait
    paraître cette figure plus pauvre que les autres, alors qu'elle occupe autant
    de place.

    Sans dénominateur, l'anneau reste entier : voir `_fraction`.

    **Le remplissage tombe sur le chiffre prononcé.** Fixé à 260 ms, il se
    jouait à l'entrée de la slide, quand la voix n'avait encore rien dit : le
    geste qui montre la part précédait la part elle-même. Retrouvé dans la
    datation des mots, il coïncide avec elle, et le chiffre marque l'instant par
    un léger rebond. Sans datation, on garde 260 ms et aucun rebond.
    """
    brut = donnees.get("valeur", "?")
    valeur = _echapper(brut)
    description = _echapper(donnees.get("description", ""))
    part = _fraction(brut)
    instant = _instant_du_chiffre(brut, mots)
    rebond = (
        "" if instant is None
        else f' class="rebond anim" style="animation-delay:{instant}ms"'
    )

    # La légende est placée sous l'anneau : sa hauteur est réservée AVANT de
    # calculer le rayon, sinon le texte sort du canevas et se retrouve tronqué.
    marge_legende = 120
    # Plafonné : un anneau qui touche les bords n'a plus de vide autour de lui,
    # et c'est ce vide qui fait porter le chiffre.
    r = min(370, min(w, h - marge_legende) // 2 - 8)
    hauteur_totale = 2 * r + marge_legende
    cy = (h - hauteur_totale) // 2 + r
    cx = w // 2
    # Le corps se déduit de la largeur disponible dans l'anneau, pas d'un palier
    # sur le nombre de caractères : « 100% » débordait là où « 95% » tenait.
    # Une glyphe de Manrope occupe environ 0,62 em.
    #
    # Le dégagement dépend de ce que l'anneau est devenu : porteur d'une part, son
    # trait épais est à lire et le « % » de « 50% » venait le frôler, d'où un recul
    # du chiffre. Resté simple cadre, il n'impose rien — reculer y creuserait du
    # vide sans rien protéger.
    degagement = 2.45 if part is not None else 2.74
    corps = min(int(r * 1.1), int(degagement * r / max(len(valeur), 1)))

    if part is None:
        anneau = [
            f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{ACCENT}" stroke-width="{TRAIT}"/>'
        ]
    else:
        tour = 2 * math.pi * r
        arc = tour * part
        # Le rail porte le tour entier : un arc de 20 % sans cercle de référence
        # ne se compare à rien et cesse d'être lisible comme une part. Même
        # raison que le rail des barres de `comparaison`.
        anneau = [
            f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{NEUTRE}" stroke-width="{TRAIT_ANNEAU}" opacity="0.22"/>'
        ]
        # Un arc plus court que l'épaisseur du trait n'est pas dessiné : ses
        # extrémités arrondies le rendraient plus long qu'il n'est, et une part
        # infime paraîtrait alors valoir le pour cent.
        if arc > TRAIT_ANNEAU:
            # `rotate(-90)` amène le départ du tracé de 3 h à midi : une part se
            # lit depuis le haut, dans le sens horaire. Le tracé est un cercle
            # entier tronqué par `stroke-dasharray`, et non un `path A` : un arc
            # de 100 % rejoindrait son point de départ et ne dessinerait rien.
            anneau.append(
                f'  <circle class="remplit anim" cx="{cx}" cy="{cy}" r="{r}" fill="none" '
                f'stroke="{ACCENT}" stroke-width="{TRAIT_ANNEAU}" stroke-linecap="round" '
                f'transform="rotate(-90 {cx} {cy})" '
                f'style="--arc:{arc:.1f};--tour:{tour:.1f};'
                f'animation-delay:{260 if instant is None else instant}ms"/>'
            )

    return _svg(w, h, "\n".join(anneau) + f"""
  <text x="{cx}" y="{cy + corps // 3}" text-anchor="middle" fill="{ACCENT}"
        font-size="{corps}" font-weight="700" font-family="{POLICE_SVG}"{rebond}>{valeur}</text>
  <text x="{cx}" y="{cy + r + 76}" text-anchor="middle" fill="{TEXTE_DOUX}"
        font-size="48" font-family="{POLICE_SVG}">{description}</text>""")


def _svg_etapes(
    donnees: dict, w: int, h: int,
    mots: "list[dict] | None" = None, duree_ms: int = 0,
) -> str:
    """
    Enchaînement d'étapes : cadres ouverts reliés par des flèches.

    La dernière étape porte l'accent — c'est le résultat, donc le point d'arrivée
    du regard. Les précédentes restent neutres pour que la lecture progresse au
    lieu de s'éparpiller.

    Quand la datation des mots est disponible, la figure **se construit au fil de
    la narration** : chaque cadre paraît lorsque la voix le nomme, chaque flèche
    se trace juste après. Sans elle, tout apparaît sur la première moitié de la
    slide, comme avant.
    """
    items = donnees.get("items", [])[:4]
    n = len(items)
    if n == 0:
        return ""

    # Cadres compacts, liaisons longues. L'inverse — de gros blocs séparés par
    # des flèches courtes — se lit comme une liste de cartes empilées, pas comme
    # un enchaînement : c'est la liaison qui porte l'idée de circulation.
    boite_h, liaison, y0 = _repartir(h, n, hauteur_max=168, ratio_ecart=1.6)
    instants = _cadence(items, mots, duree_ms) if duree_ms else [None] * n

    parts = []
    for i, item in enumerate(items):
        texte, icone = _item(item)
        y = y0 + i * (boite_h + liaison)
        parts.append(_boite(0, y, w, boite_h, texte,
                            accent=(i == n - 1), indice=i + 1,
                            delai_ms=instants[i], icone=icone))
        if i < n - 1:
            depart = y + boite_h + 14
            # La flèche se trace après le cadre qu'elle quitte, avant celui
            # qu'elle rejoint : elle porte le passage de l'un à l'autre.
            delai = None if instants[i] is None else instants[i] + 420
            parts.append(
                _fleche(w // 2, depart, w // 2, depart + liaison - 30, delai)
            )
    return _svg(w, h, "\n".join(parts))


def _svg_comparaison(
    donnees: dict, w: int, h: int,
    mots: "list[dict] | None" = None, duree_ms: int = 0,
) -> str:
    """
    Comparaison de valeurs : libellé, valeur, barre.

    Seule la valeur la plus élevée porte l'accent — c'est le résultat de la
    comparaison, donc la seule information que le regard doit trouver seul. Faire
    tourner la palette colorait des barres sans rien hiérarchiser.
    """
    items = donnees.get("items", [])[:4]
    n = len(items)
    if n == 0:
        return ""

    valeurs = [it.get("valeur", 0) for it in items]
    max_val = max(valeurs, default=100) or 100

    # Des valeurs toutes égales ne comparent rien : accentuer « la plus élevée »
    # désignerait alors tout le monde et inventerait une hiérarchie absente. Le
    # LLM produit ce cas quand il compare des notions non quantifiables.
    departage = len(set(valeurs)) > 1
    if not departage:
        logger.warning(
            "Comparaison sans écart : %d valeurs identiques (%s). La figure "
            "n'oppose rien — un autre type de graphique conviendrait mieux.",
            len(valeurs), valeurs[0] if valeurs else "?",
        )

    # Le libellé passe au-dessus de sa barre : sur un cadre vertical, une colonne
    # de libellés à gauche vole la largeur utile à la comparaison elle-même.
    bloc_h, ecart, y0 = _repartir(h, n, hauteur_max=190, ratio_ecart=0.32)
    barre_h = 30
    instants = _cadence(items, mots, duree_ms) if duree_ms else [None] * n

    parts = []
    for i, item in enumerate(items):
        y = y0 + i * (bloc_h + ecart)
        val = item.get("valeur", 0)
        couleur = ACCENT if (departage and val == max_val) else NEUTRE
        bar_w = max(int(w * (val / max_val)), 12)
        # La barre pousse juste après l'apparition de son bloc : le libellé se
        # lit d'abord, la longueur ensuite. Sans cadence, elle reste posée.
        pousse = (
            "" if instants[i] is None
            else f' class="pousse anim" style="animation-delay:{instants[i] + 180}ms"'
        )
        bloc = (
            f'  <text x="0" y="{y + 46}" fill="{TEXTE}" font-size="46" '
            f'font-weight="600" font-family="{POLICE_SVG}">{_echapper(item.get("label", ""))}</text>\n'
            f'  <text x="{w}" y="{y + 46}" text-anchor="end" fill="{couleur}" font-size="46" '
            f'font-weight="700" font-family="{POLICE_SVG}">{_echapper(val)}</text>\n'
            # Le rail marque la valeur maximale : sans lui, une barre courte ne se
            # compare à rien et l'écart cesse d'être lisible.
            f'  <line x1="0" y1="{y + 82}" x2="{w}" y2="{y + 82}" stroke="{NEUTRE}" '
            f'stroke-width="1" opacity="0.35"/>\n'
            f'  <rect x="0" y="{y + 82 - barre_h // 2}" width="{bar_w}" height="{barre_h}" '
            f'rx="{barre_h // 2}" fill="{couleur}"{pousse}/>'
        )
        parts.append(_parait(bloc, instants[i]))
    return _svg(w, h, "\n".join(parts))


def _svg_liste(
    donnees: dict, w: int, h: int,
    mots: "list[dict] | None" = None, duree_ms: int = 0,
) -> str:
    """
    Points clés : filet d'accent, libellé, règle de séparation.

    Pas de cadre ici, contrairement aux étapes. Une liste n'a ni ordre ni
    circulation : l'encadrer imiterait un schéma sans en être un. Le filet
    vertical suffit à rattacher chaque ligne à l'ensemble.
    """
    items = [_item(it) for it in donnees.get("items", [])[:5]]
    n = len(items)
    if n == 0:
        return ""
    instants = _cadence(items, mots, duree_ms) if duree_ms else [None] * n

    # Icônes réparties par tout ou rien : un mélange (certains items pictogrammés,
    # d'autres non) déciderait sans raison de leur poids visuel respectif. La
    # colonne d'icône n'est réservée — et le texte décalé d'autant — que si le LLM
    # en a proposé au moins une.
    a_icones = any(icone for _, icone in items)
    hauteur, ecart, y0 = _repartir(h, n, hauteur_max=210, ratio_ecart=0.22)
    filet_x = 6
    marge_icone = filet_x + 24
    marge_texte = marge_icone + 76 if a_icones else 54

    parts = []
    for i, (texte, icone) in enumerate(items):
        y = y0 + i * (hauteur + ecart)
        cy = y + hauteur // 2
        # Filet court centré sur la ligne plutôt qu'un trait continu : il marque
        # l'élément sans dessiner une colonne qui traverserait la figure.
        ligne = [
            f'  <line x1="{filet_x}" y1="{y + 12}" x2="{filet_x}" y2="{y + hauteur - 12}" '
            f'stroke="{ACCENT}" stroke-width="{TRAIT}" stroke-linecap="round"/>'
        ]
        if a_icones and icone:
            taille = min(hauteur - 20, 56)
            ligne.append(_icone(icone, marge_icone + taille / 2, cy, taille, NEUTRE))
        ligne.append(
            f'  <text x="{marge_texte}" y="{cy + 17}" fill="{TEXTE}" font-size="48" '
            f'font-weight="600" font-family="{POLICE_SVG}">{_echapper(texte)}</text>'
        )
        # La règle de séparation reste statique : elle structure la figure entière,
        # elle n'appartient à aucun des deux points qu'elle sépare.
        parts.append(_parait("\n".join(ligne), instants[i]))
        if i < n - 1:
            regle_y = y + hauteur + ecart // 2
            parts.append(
                f'  <line x1="{marge_texte}" y1="{regle_y}" x2="{w}" y2="{regle_y}" '
                f'stroke="{NEUTRE}" stroke-width="1" opacity="0.45"/>'
            )
    return _svg(w, h, "\n".join(parts))


def _svg_neurones(
    donnees: dict, w: int, h: int,
    mots: "list[dict] | None" = None, duree_ms: int = 0,
) -> str:
    """
    Réseau de neurones : nœuds ouverts, liaisons en trait fin.

    Les disques pleins de la version précédente écrasaient les liaisons, alors
    que ce sont elles qui portent l'idée de réseau. Entrée et sortie prennent
    l'accent, les couches cachées restent neutres.

    Cadencée par couche plutôt que par nœud : une couche entière — ses nœuds et
    les liaisons qui l'atteignent — paraît quand la narration en parle. Nœud par
    nœud aurait produit un scintillement au lieu d'une construction lisible.
    """
    couches = [min(c, 5) for c in donnees.get("couches", [3, 4, 2])[:4]]
    n_couches = len(couches)
    max_noeuds = max(couches)

    # Couches empilées de haut en bas, nœuds répartis en largeur. L'inverse —
    # des colonnes côte à côte — étalait trois nœuds sur toute la hauteur d'un
    # cadre 9:16 : les liaisons se croisaient et la figure devenait illisible.
    pad_x = 90
    bande_legende = 78          # « Entrée » au-dessus, « Sortie » en dessous
    espacement = min(300, (h - 2 * bande_legende) // max(n_couches - 1, 1))
    r = min((w - 2 * pad_x) // (max_noeuds * 3), espacement // 3, 54)

    total_h = espacement * (n_couches - 1)
    y0 = (h - total_h) // 2

    positions = []
    for li, n_noeuds in enumerate(couches):
        y = y0 + li * espacement
        rangee = []
        for ni in range(n_noeuds):
            if n_noeuds > 1:
                x = pad_x + ni * (w - 2 * pad_x) // (n_noeuds - 1)
            else:
                x = w // 2
            rangee.append((x, y))
        positions.append(rangee)

    instants = _cadence(couches, mots, duree_ms) if duree_ms else [None] * n_couches

    # Les liaisons se dessinent avant les nœuds : elles passent ainsi derrière,
    # et les cercles ouverts restent lisibles là où le maillage est dense. Une
    # liaison est datée sur la couche qu'elle atteint, pas celle dont elle part :
    # c'est l'arrivée d'une nouvelle couche que la narration annonce.
    parts = []
    for li in range(n_couches - 1):
        liaisons = [
            f'  <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{NEUTRE}" stroke-width="1.2" opacity="0.4"/>'
            for (x1, y1) in positions[li]
            for (x2, y2) in positions[li + 1]
        ]
        parts.append(_parait("\n".join(liaisons), instants[li + 1]))
    for li, colonne in enumerate(positions):
        accent = li in (0, n_couches - 1)
        couleur = ACCENT if accent else NEUTRE
        noeuds = [
            f'  <circle cx="{x}" cy="{y}" r="{r}" fill="none" '
            f'stroke="{couleur}" stroke-width="{TRAIT}"/>'
            for (x, y) in colonne
        ]
        parts.append(_parait("\n".join(noeuds), instants[li]))

    # Seules l'entrée et la sortie sont nommées : « Caché » répété à chaque
    # rangée n'apprend rien et encombre une figure qui vit de son vide.
    parts.append(
        f'  <text x="{w // 2}" y="{y0 - r - 58}" text-anchor="middle" fill="{TEXTE_DOUX}" '
        f'font-size="38" font-family="{POLICE_SVG}">Entrée</text>'
    )
    parts.append(
        f'  <text x="{w // 2}" y="{y0 + total_h + r + 62}" text-anchor="middle" fill="{TEXTE_DOUX}" '
        f'font-size="38" font-family="{POLICE_SVG}">Sortie</text>'
    )
    return _svg(w, h, "\n".join(parts))


def _svg_couches(
    donnees: dict, w: int, h: int,
    mots: "list[dict] | None" = None, duree_ms: int = 0,
) -> str:
    """
    Empilement de couches, du bas vers le haut.

    Répond à une famille de concepts que les autres figures rendaient mal : les
    couches d'un réseau, les blocs d'une architecture, les niveaux d'une pile
    technique. Les étapes suggèrent une circulation dans le temps, l'empilement
    une superposition — ce n'est pas la même idée.

    Chaque couche est légèrement plus étroite que celle du dessous : le décalage
    donne la profondeur sans recourir à une perspective, qui alourdirait le trait.
    """
    items = donnees.get("items", [])[:5]
    n = len(items)
    if n == 0:
        return ""

    # Écart faible à dessein : des couches franchement séparées ne se lisent plus
    # comme un empilement mais comme une liste de cadres.
    couche_h, ecart, y0 = _repartir(h, n, hauteur_max=230, ratio_ecart=0.18)
    retrait = 26  # pincement horizontal cumulé à chaque niveau
    instants = _cadence(items, mots, duree_ms) if duree_ms else [None] * n

    parts = []
    for i, item in enumerate(items):
        texte, icone = _item(item)
        # Le premier élément de la liste est la couche du dessus.
        niveau = n - 1 - i
        marge = retrait * niveau
        y = y0 + i * (couche_h + ecart)
        parts.append(_boite(marge, y, w - 2 * marge, couche_h, texte,
                            accent=(i == 0), icone=icone, delai_ms=instants[i]))
    return _svg(w, h, "\n".join(parts))


def _svg_cycle(
    donnees: dict, w: int, h: int,
    mots: "list[dict] | None" = None, duree_ms: int = 0,
) -> str:
    """
    Boucle : nœuds répartis en cercle, reliés dans le sens horaire.

    Répond à un cas que "etapes" rend mal : un processus qui ne se termine pas
    mais reprend sur lui-même (itération, cycle de vie, boucle de rétroaction).
    Seul l'arc de bouclage — du dernier nœud au premier — porte l'accent : c'est
    lui, et lui seul, qui montre que la figure boucle plutôt qu'elle ne finit.
    """
    items = [_item(it) for it in donnees.get("items", [])[:5]]
    n = len(items)
    if n < 2:
        return ""

    cx, cy = w / 2, h / 2
    r_noeud = 46
    marge_texte = 34
    # Plafonné pour réserver de la place aux libellés à gauche et à droite du
    # cercle : c'est là qu'ils ont le moins d'espace avant le bord du canevas.
    rayon = min(w, h) / 2 - r_noeud - 200

    instants = _cadence(items, mots, duree_ms) if duree_ms else [None] * n
    positions = [
        (
            cx + rayon * math.cos(math.radians(-90 + i * 360 / n)),
            cy + rayon * math.sin(math.radians(-90 + i * 360 / n)),
            math.radians(-90 + i * 360 / n),
        )
        for i in range(n)
    ]

    parts = []
    # Arcs d'abord, nœuds ensuite : même logique que "neurones", les liaisons
    # passent derrière pour que les nœuds restent lisibles.
    inset = math.radians(16)
    for i in range(n):
        j = (i + 1) % n
        a1, a2 = positions[i][2] + inset, positions[j][2] - inset
        x1, y1 = cx + rayon * math.cos(a1), cy + rayon * math.sin(a1)
        x2, y2 = cx + rayon * math.cos(a2), cy + rayon * math.sin(a2)
        bouclage = j == 0
        couleur = ACCENT if bouclage else NEUTRE
        marqueur = "pointe-accent" if bouclage else "pointe"
        arc = (
            f'  <path d="M {x1:.1f} {y1:.1f} A {rayon:.1f} {rayon:.1f} 0 0 1 '
            f'{x2:.1f} {y2:.1f}" fill="none" stroke="{couleur}" stroke-width="{TRAIT}" '
            f'stroke-linecap="round" marker-end="url(#{marqueur})"/>'
        )
        # L'arc de bouclage arrive au premier nœud, déjà paru en tout premier :
        # il surgit donc après le dernier nœud plutôt qu'avec sa cible.
        delai = (
            (None if instants[-1] is None else instants[-1] + 450)
            if bouclage else instants[j]
        )
        parts.append(_parait(arc, delai))

    for i, (texte, icone) in enumerate(items):
        x, y, angle = positions[i]
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        noeud = [
            f'  <circle cx="{x:.1f}" cy="{y:.1f}" r="{r_noeud}" fill="none" '
            f'stroke="{NEUTRE}" stroke-width="{TRAIT}"/>'
        ]
        if icone:
            noeud.append(_icone(icone, x, y, r_noeud * 1.1, NEUTRE))
        else:
            noeud.append(
                f'  <text x="{x:.1f}" y="{y + 14:.1f}" text-anchor="middle" fill="{NEUTRE}" '
                f'font-size="34" font-weight="700" font-family="{POLICE_SVG}">{i + 1}</text>'
            )
        # Le libellé prolonge le rayon vers l'extérieur : l'ancrage suit le
        # cadran pour ne jamais chevaucher le nœud voisin ni le cercle.
        lx = x + (r_noeud + marge_texte) * cos_a
        ly = y + (r_noeud + marge_texte) * sin_a
        if cos_a > 0.3:
            ancre, ly, disponible = "start", ly + 16, w - lx - 16
        elif cos_a < -0.3:
            ancre, ly, disponible = "end", ly + 16, lx - 16
        else:
            ancre = "middle"
            ly = ly + (36 if sin_a > 0 else -8)
            disponible = w - 32
        # La taille se déduit de la place réellement disponible, comme le chiffre
        # de "stat" : les nœuds latéraux ont bien moins de large que le canevas
        # entier, un libellé à taille fixe y déborderait sur le bord.
        corps_texte = max(24, min(42, int(disponible / (max(len(texte), 1) * 0.58))))
        noeud.append(
            f'  <text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{ancre}" fill="{TEXTE}" '
            f'font-size="{corps_texte}" font-weight="600" font-family="{POLICE_SVG}">{_echapper(texte)}</text>'
        )
        parts.append(_parait("\n".join(noeud), instants[i]))

    return _svg(w, h, "\n".join(parts))


def _svg_matrice(
    donnees: dict, w: int, h: int,
    mots: "list[dict] | None" = None, duree_ms: int = 0,
) -> str:
    """
    Grille 2x2 : deux axes indépendants, un quadrant par combinaison.

    Répond à un cas que "comparaison" ne sait pas montrer : un croisement de
    deux axes, pas un écart sur un seul. Toujours 4 quadrants, dans l'ordre de
    lecture haut-droite (les deux axes au maximum), haut-gauche, bas-droite,
    bas-gauche.
    """
    quadrants = [_item(it) for it in donnees.get("quadrants", [])[:4]]
    if len(quadrants) < 4:
        return ""
    axe_x = _echapper(donnees.get("axe_x", ""))
    axe_y = _echapper(donnees.get("axe_y", ""))

    pad = 78
    cx, cy = w / 2, h / 2

    parts = [
        f'  <line x1="{pad}" y1="{cy}" x2="{w - pad}" y2="{cy}" stroke="{NEUTRE}" '
        f'stroke-width="{TRAIT}" stroke-linecap="round" marker-end="url(#pointe)"/>',
        f'  <line x1="{cx}" y1="{h - pad}" x2="{cx}" y2="{pad}" stroke="{NEUTRE}" '
        f'stroke-width="{TRAIT}" stroke-linecap="round" marker-end="url(#pointe)"/>',
    ]
    if axe_x:
        parts.append(
            f'  <text x="{w - pad - 10}" y="{cy - 20}" text-anchor="end" fill="{TEXTE_DOUX}" '
            f'font-size="34" font-family="{POLICE_SVG}">{axe_x}</text>'
        )
    if axe_y:
        parts.append(
            f'  <text x="{cx}" y="{pad - 22}" text-anchor="middle" fill="{TEXTE_DOUX}" '
            f'font-size="34" font-family="{POLICE_SVG}">{axe_y}</text>'
        )

    centres = [
        ((cx + (w - pad)) / 2, (pad + cy) / 2),
        ((pad + cx) / 2, (pad + cy) / 2),
        ((cx + (w - pad)) / 2, (cy + (h - pad)) / 2),
        ((pad + cx) / 2, (cy + (h - pad)) / 2),
    ]
    instants = _cadence(quadrants, mots, duree_ms) if duree_ms else [None] * 4

    for i, ((texte, icone), (qx, qy)) in enumerate(zip(quadrants, centres)):
        bloc = []
        if icone:
            bloc.append(_icone(icone, qx, qy - 34, 60, NEUTRE))
            ty = qy + 34
        else:
            ty = qy
        bloc.append(
            f'  <text x="{qx:.1f}" y="{ty:.1f}" text-anchor="middle" fill="{TEXTE}" '
            f'font-size="42" font-weight="600" font-family="{POLICE_SVG}">{_echapper(texte)}</text>'
        )
        parts.append(_parait("\n".join(bloc), instants[i]))

    return _svg(w, h, "\n".join(parts))


def _svg_frise(
    donnees: dict, w: int, h: int,
    mots: "list[dict] | None" = None, duree_ms: int = 0,
) -> str:
    """
    Frise chronologique : jalons datés le long d'une colonne verticale.

    Verticale et non horizontale, contrairement à l'usage. Sur un cadre 9:16,
    quatre dates alignées en largeur laissent 240 px par libellé, ce qui n'écrit
    rien — même constat que pour `neurones`, dont les couches sont passées en
    rangées pour la même raison.

    Se distingue d'`etapes` par la donnée et non par le dessin : une étape est un
    rang dans une suite, un jalon porte une date. Sans date, c'est `etapes`.
    """
    bruts = donnees.get("items", [])[:5]
    n = len(bruts)
    if n == 0:
        return ""

    jalons = [
        (*_item(item), _echapper(item.get("date", "")) if isinstance(item, dict) else "")
        for item in bruts
    ]

    bloc, ecart, y0 = _repartir(h, n, hauteur_max=190, ratio_ecart=0.26)
    instants = _cadence(bruts, mots, duree_ms) if duree_ms else [None] * n

    # Le pictogramme est le jalon, il ne l'accompagne pas. Posé à droite de la
    # ligne il flottait dans le vide, sans cadre pour le rattacher à son libellé,
    # et devait rester assez petit pour ne pas voler la vedette au texte : deux
    # défauts que `cycle` avait déjà résolus en le logeant dans le nœud.
    # Tout ou rien, comme la colonne d'icônes de `liste` : un mélange de gros et
    # de petits jalons sur une même colonne se lirait comme une hiérarchie.
    a_icones = any(icone for _, icone, _ in jalons)
    colonne_x = 74 if a_icones else 58
    r_jalon = 42 if a_icones else 15
    x_texte = colonne_x + r_jalon + 44

    parts = []
    # La colonne relie les jalons entre eux : sans elle, ce sont des lignes
    # empilées et non une chronologie. Elle s'arrête au centre des jalons
    # extrêmes plutôt qu'aux bords du canevas, où elle pendrait dans le vide.
    if n > 1:
        haut = y0 + bloc // 2
        bas = y0 + (n - 1) * (bloc + ecart) + bloc // 2
        parts.append(
            f'  <line x1="{colonne_x}" y1="{haut}" x2="{colonne_x}" y2="{bas}" '
            f'stroke="{NEUTRE}" stroke-width="{TRAIT}" stroke-linecap="round"/>'
        )

    for i, (texte, icone, date) in enumerate(jalons):
        y = y0 + i * (bloc + ecart)
        cy = y + bloc // 2
        dernier = i == n - 1
        couleur = ACCENT if dernier else NEUTRE
        # Le jalon le plus récent est plein quand les autres sont ouverts : c'est
        # le point d'arrivée du regard, comme la dernière boîte d'`etapes`. Un
        # jalon porteur d'icône reste ouvert, sinon le pictogramme s'y noierait.
        plein = dernier and not a_icones
        marqueur = [
            f'  <circle cx="{colonne_x}" cy="{cy}" r="{r_jalon}" '
            f'fill="{ACCENT if plein else "none"}" stroke="{couleur}" '
            f'stroke-width="{TRAIT}"/>'
        ]
        if icone:
            marqueur.append(_icone(icone, colonne_x, cy, r_jalon * 1.05, couleur))
        if date:
            marqueur.append(
                f'  <text x="{x_texte}" y="{cy - 14}" fill="{couleur}" font-size="36" '
                f'font-weight="700" font-family="{POLICE_SVG}">{date}</text>'
            )
        # La date au-dessus du libellé, et non à gauche de la colonne : « Mars
        # 2024 » et « 2017 » n'ont pas la même largeur, une gouttière commune
        # devrait se caler sur la plus longue et voler la place du libellé.
        marqueur.append(
            f'  <text x="{x_texte}" y="{cy + (36 if date else 15)}" fill="{TEXTE}" '
            f'font-size="46" font-weight="600" font-family="{POLICE_SVG}">{_echapper(texte)}</text>'
        )
        parts.append(_parait("\n".join(marqueur), instants[i]))

    return _svg(w, h, "\n".join(parts))


def _svg_embranchement(
    donnees: dict, w: int, h: int,
    mots: "list[dict] | None" = None, duree_ms: int = 0,
) -> str:
    """
    Un point de décision et ses issues.

    Répond à un cas qu'aucune autre figure ne montre : une question qui ouvre
    plusieurs suites. `etapes` enchaîne sans jamais bifurquer, `matrice` croise
    deux axes continus, `comparaison` oppose des grandeurs déjà connues.

    La question porte l'accent, pas les issues : c'est elle qui tient la figure,
    et accentuer deux issues sur deux reviendrait à colorier tout le dessin.
    """
    question = donnees.get("question", "")
    branches = [_item(b) for b in donnees.get("branches", [])[:3]]
    n = len(branches)
    if not question or n < 2:
        return ""

    q_h = 190
    tronc = 96          # de la question au distributeur
    descente = 74       # du distributeur aux issues
    # Plafonné à 280 : à 340 le cadre gardait une bande vide sous son libellé,
    # l'icône et le texte occupant le haut. Un cadre qui dépasse son contenu se
    # lit comme une case à remplir, pas comme une issue.
    branche_h = min(280, max(160, h - q_h - tronc - descente - 40))
    total = q_h + tronc + descente + branche_h
    y0 = max(0, (h - total) // 2)

    y_distributeur = y0 + q_h + tronc
    y_branches = y_distributeur + descente

    ecart = 30
    bw = (w - (n - 1) * ecart) // n
    centres = [bw // 2 + i * (bw + ecart) for i in range(n)]

    instants = (
        _cadence([question] + [t for t, _ in branches], mots, duree_ms)
        if duree_ms else [None] * (n + 1)
    )

    question_bloc = (
        f'  <rect x="0" y="{y0}" width="{w}" height="{q_h}" rx="{RAYON}" fill="none" '
        f'stroke="{ACCENT}" stroke-width="{TRAIT}" stroke-linejoin="round"/>\n'
        + _bloc_texte(question, w / 2, y0 + q_h / 2, w - 80, TEXTE,
                      plafond=46, max_lignes=2)
    )
    parts = [_parait(question_bloc, instants[0])]

    # Le tronc et le distributeur paraissent avec la première issue : ils
    # annoncent la bifurcation, ils n'appartiennent à aucune branche en propre.
    liaison = [
        f'  <line x1="{w // 2}" y1="{y0 + q_h}" x2="{w // 2}" y2="{y_distributeur}" '
        f'stroke="{NEUTRE}" stroke-width="{TRAIT}" stroke-linecap="round"/>',
        f'  <line x1="{centres[0]}" y1="{y_distributeur}" x2="{centres[-1]}" '
        f'y2="{y_distributeur}" stroke="{NEUTRE}" stroke-width="{TRAIT}" '
        f'stroke-linecap="round"/>',
    ]
    parts.append(_parait("\n".join(liaison), instants[1]))

    for i, (texte, icone) in enumerate(branches):
        cx_b = centres[i]
        issue = [
            _fleche(cx_b, y_distributeur, cx_b, y_branches - 12),
            f'  <rect x="{cx_b - bw // 2}" y="{y_branches}" width="{bw}" '
            f'height="{branche_h}" rx="{RAYON}" fill="none" stroke="{NEUTRE}" '
            f'stroke-width="{TRAIT}" stroke-linejoin="round"/>',
        ]
        cy_b = y_branches + branche_h / 2
        if icone:
            issue.append(_icone(icone, cx_b, cy_b - branche_h * 0.22, 64, NEUTRE))
            cy_b += branche_h * 0.13
        issue.append(_bloc_texte(texte, cx_b, cy_b, bw - 44, TEXTE, plafond=40))
        parts.append(_parait("\n".join(issue), instants[i + 1]))

    return _svg(w, h, "\n".join(parts))


def _svg_venn(
    donnees: dict, w: int, h: int,
    mots: "list[dict] | None" = None, duree_ms: int = 0,
) -> str:
    """
    Deux ensembles et ce qu'ils partagent.

    Répond au cas où deux notions ne s'opposent pas mais se recouvrent : l'IA et
    l'apprentissage automatique, un modèle et un agent. `comparaison` en ferait
    deux barres, ce qui suppose une grandeur commune ; `matrice` deux axes, ce
    qui suppose leur indépendance. Ici c'est l'inverse — le recouvrement *est*
    l'information.

    L'accent va donc à la zone commune, seule chose que la figure apprenne. Les
    deux cercles restent neutres : ils sont les prémisses, pas la conclusion.
    """
    gauche = donnees.get("gauche", "")
    droite = donnees.get("droite", "")
    commun = donnees.get("commun", "")
    if not gauche or not droite:
        return ""

    marge = 12
    # Les deux contraintes se rencontrent : le rayon est borné en hauteur par le
    # canevas, en largeur par la somme (écart + rayon) des deux cercles.
    r = int(min(h / 2 - marge, (w / 2 - marge) / 1.6))
    # Centres distants de 1,2 r : l'écartement usuel d'un diagramme de Venn. Plus
    # serré, les deux cercles se lisent comme une seule forme ; plus lâche, la
    # zone commune devient trop mince pour porter un libellé.
    d = int(0.6 * r)
    cx, cy = w // 2, h // 2

    instants = _cadence([gauche, droite, commun or gauche], mots, duree_ms) if duree_ms else [None] * 3

    parts = []
    for i, (signe, texte) in enumerate(((-1, gauche), (1, droite))):
        centre = cx + signe * d
        ensemble = [
            f'  <circle cx="{centre}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{NEUTRE}" stroke-width="{TRAIT}"/>',
            # Le libellé se pose au cœur de la partie non recouverte, pas au
            # centre du cercle, qui tombe dans la zone commune.
            _bloc_texte(texte, centre + signe * r * 0.42, cy,
                        r * 0.84, TEXTE, plafond=40),
        ]
        parts.append(_parait("\n".join(ensemble), instants[i]))

    if commun:
        # La zone commune n'est pas cerclée : la souligner d'un troisième trait
        # ajouterait une forme fermée là où la figure vit de ses recouvrements.
        # C'est la couleur du texte, seule, qui la désigne.
        largeur_lentille = 2 * (r - d)
        # Corps plafonné plus bas que partout ailleurs : la lentille ne fait que
        # 0,8 rayon de large et se rétrécit dès qu'on s'éloigne de son centre. À
        # 34 px le libellé mordait sur les deux arcs, mesuré au rendu.
        parts.append(
            _parait(
                _bloc_texte(commun, cx, cy, largeur_lentille - 16, ACCENT,
                            plafond=30, plancher=20, poids=700),
                instants[2],
            )
        )

    return _svg(w, h, "\n".join(parts))


def _svg_figure_source(donnees: dict, w: int, h: int) -> str:
    """
    Une figure tirée de l'article lui-même, avec sa légende et son crédit.

    **Seule figure du module qui ne soit pas inventée.** Toutes les autres
    dessinent des données que le modèle imagine ; celle-ci montre ce que les
    auteurs ont publié. C'est aussi la seule qui porte du texte étranger à la
    charte, et c'est assumé : une figure de papier vaut par ce qu'elle montre.

    Rendue en `<image>` SVG et non en HTML, pour que rien ne change en amont —
    l'enveloppe, les repères d'angle, les polices et le repli de texte restent
    ceux des dix autres figures.

    L'attribution est **obligatoire** et n'est jamais tue. La licence par défaut
    d'arXiv accorde à arXiv un droit de diffusion, pas à un tiers : l'afficher
    est la contrepartie de l'emploi. Sans elle, la figure n'est pas rendue.
    """
    chemin = donnees.get("image", "")
    attribution = _echapper(donnees.get("attribution", ""))
    if not chemin or not attribution:
        if chemin:
            logger.warning(
                "Figure source sans attribution — non rendue. Le crédit est la "
                "contrepartie de l'usage, il ne peut pas être omis."
            )
        return ""

    fichier = Path(chemin)
    if not fichier.exists():
        logger.warning("Figure source introuvable : %s", chemin)
        return ""
    # `as_uri()` et non `as_posix()` : le chemin porte le stem du sujet, donc une
    # apostrophe dès que le titre en contient une. Même piège que le fond de
    # slide, qui a coûté une vidéo entière rendue sans feuille de style.
    adresse = _echapper(fichier.resolve().as_uri())

    legende = donnees.get("legende", "")
    corps_credit = 24
    bande_credit = corps_credit + 18
    bande_legende = 92 if legende else 0
    disponible = h - bande_credit - bande_legende - 18

    # Hauteur réellement occupée, déduite du rapport de forme du fichier. Sans
    # cette mesure, `meet` centre l'image dans toute la bande et la légende reste
    # collée en bas : une figure large de rapport 2 laissait alors plus de 200 px
    # de vide entre elle et son intitulé. La mesure est facultative — Pillow
    # n'arrive au projet que par transitivité — et son absence rend simplement
    # le cadrage d'avant.
    hauteur_image = disponible
    try:
        from PIL import Image

        with Image.open(fichier) as im:
            rapport = im.width / im.height if im.height else 0
        if rapport > 0:
            hauteur_image = min(disponible, int(w / rapport))
    except Exception:                            # noqa: BLE001 — mesure facultative
        logger.debug("Rapport de forme non mesuré pour %s", chemin)

    # Figure et légende forment un bloc centré dans la bande qui leur reste. Posé
    # en haut, un schéma large laissait tout le vide d'un seul côté, entre lui et
    # le texte lu — la zone graphique étant élastique, elle ne se resserre pas
    # sur son contenu.
    haut = max(0, (disponible - hauteur_image) // 2)

    parts = [
        # `meet` plutôt que `slice` : une figure recadrée perd ses axes et ses
        # libellés internes, c'est-à-dire ce qui la rend lisible. Une bande vide
        # vaut mieux qu'un schéma amputé.
        f'  <image href="{adresse}" x="0" y="{haut}" width="{w}" '
        f'height="{hauteur_image}" preserveAspectRatio="xMidYMid meet"/>'
    ]

    if legende:
        parts.append(
            _bloc_texte(legende, w / 2, haut + hauteur_image + bande_legende / 2,
                        w - 60, TEXTE_DOUX, plafond=30, plancher=20,
                        max_lignes=2, poids=400)
        )

    # Le crédit est discret mais jamais absent, et toujours au même endroit :
    # en pied de figure, aligné à droite, dans le neutre le plus effacé.
    parts.append(
        f'  <text x="{w}" y="{h - 6}" text-anchor="end" fill="{NEUTRE}" '
        f'font-size="{corps_credit}" font-family="{POLICE_SVG}">{attribution}</text>'
    )
    return _svg(w, h, "\n".join(parts))


# ── Animations Lottie ─────────────────────────────────────────────────────────
#
# Animations vectorielles prêtes à l'emploi, au-delà de ce que le CSS dessine :
# un pictogramme qui se construit, une petite scène. **Elles ne jouent jamais
# seules** : `window.seek(t)` les place à l'instant voulu par `goToAndStop`, comme
# il fige les animations CSS — le rendu reste déterministe image par image.
#
# La bibliothèque (lottie-web 5.12.2, version allégée, MIT) et les animations
# vivent dans le dépôt, comme les polices : le rendu reste hors ligne et
# identique en local et dans le conteneur. Les données sont injectées dans la
# page plutôt que chargées : un `fetch` est refusé sur une page `file://`.

LOTTIE_DIR = Path(__file__).parent / "assets" / "lottie"
LOTTIE_JS = LOTTIE_DIR / "lottie_light.min.js"
# Départ de l'animation : avec l'apparition de la zone graphique (`.graphic`).
LOTTIE_DEPART_MS = 420


def animations_disponibles() -> list[str]:
    """Noms des animations embarquées, sans extension (le manifeste n'en est pas une)."""
    return sorted(p.stem for p in LOTTIE_DIR.glob("*.json") if p.stem != "SOURCES")


def catalogue_animations() -> dict[str, str]:
    """
    {nom: ce qu'elle évoque}, pour le prompt du LLM.

    Lu dans le manifeste `SOURCES.json`, tenu par `content/lottie.py` : importer
    une animation suffit à la proposer au modèle, rien à tenir à jour à la main.
    Seules les animations présentes sur disque sont proposées.
    """
    manifeste = LOTTIE_DIR / "SOURCES.json"
    if not manifeste.is_file():
        return {}
    entrees = json.loads(manifeste.read_text(encoding="utf-8"))
    presentes = set(animations_disponibles())
    return {nom: e.get("sens", "") for nom, e in sorted(entrees.items()) if nom in presentes}


def _svg_animation(donnees: dict, w: int, h: int) -> str:
    """
    Une animation Lottie du dépôt, désignée par son nom.

    Une animation inconnue rend une zone vide avec un WARNING, comme une figure
    source introuvable : la slide se produit, le défaut se voit.
    """
    nom = str(donnees.get("nom", "")).strip()
    chemin = LOTTIE_DIR / f"{nom}.json"
    if not nom or not chemin.is_file():
        logger.warning("Animation Lottie inconnue : '%s'. Disponibles : %s",
                       nom, ", ".join(animations_disponibles()) or "aucune")
        return ""
    donnees_json = chemin.read_text(encoding="utf-8")
    # Boucle ou non : décidé à l'import et noté au manifeste. Absente du
    # manifeste (animation maison, comme `valide`), elle se joue une fois.
    manifeste = LOTTIE_DIR / "SOURCES.json"
    boucle = bool(manifeste.is_file() and json.loads(manifeste.read_text(
        encoding="utf-8")).get(nom, {}).get("boucle"))
    return (
        f'<div class="lottie" style="width:{w}px;height:{h}px"></div>\n'
        "<script>(window.__lotties = window.__lotties || []).push({"
        "el: document.currentScript.previousElementSibling, "
        f"donnees: {donnees_json}, depart: {LOTTIE_DEPART_MS}, "
        f"boucle: {'true' if boucle else 'false'}}});</script>"
    )


# Étirement maximal d'un clip de scène. Au-delà, 24 images/s deviennent 12 et le
# mouvement saccade ; la scène reste alors sur sa dernière image.
ETIREMENT_MAX = 2.0


def _svg_scene(donnees: dict, w: int, h: int,
               mots: "list[dict] | None" = None, duree_ms: int = 0) -> str:
    """
    Scène illustrée animée : les images d'un clip, choisies une à une par seek().

    Le clip (mascotte mise en scène puis animée par Wan2GP) est découpé en images
    à l'avance. Une balise `<video>` ne convient pas : son `currentTime` n'est
    pas exact à l'image près, et le calage sur la voix casserait. Ici `seek(t)`
    affiche l'image de l'instant t et **attend son décodage** avant la capture.
    Un clip plus court que la slide reste sur sa dernière image : une scène
    raconte un événement, sa fin est son point d'arrivée.

    **Le clip est étiré sur la narration** (au plus ×2) : joué à vitesse normale,
    un clip de 5 s finissait à mi-slide, et l'événement montré — le Wi-Fi qui se
    coupe — précédait de trois secondes la phrase qui le dit. Constaté sur le
    prototype du 25/10/2026.
    """
    dossier = Path(str(donnees.get("images", "")))
    images = sorted(dossier.glob("*.jpg")) if dossier.is_dir() else []
    if not images:
        logger.warning("Scène sans images : '%s'", dossier)
        return ""
    uris = [p.resolve().as_uri() for p in images]
    fps = int(donnees.get("fps", 24))
    clip_ms = len(images) * 1000 / fps
    utile_ms = duree_ms - LOTTIE_DEPART_MS - SORTIE_MS
    etirement = min(ETIREMENT_MAX, max(1.0, utile_ms / clip_ms)) if duree_ms else 1.0
    # Taille relative et non `w` px : une <img> ne rétrécit pas sous sa largeur
    # déclarée, et 922 px débordaient de la colonne — refusé par le contrôle de
    # mise en page le 26/10/2026. `object-fit:contain` (.scene) garde le cadrage.
    return (
        f'<img class="scene" src="{uris[-1]}" style="width:100%;height:100%">\n'
        "<script>(window.__scenes = window.__scenes || []).push({"
        "el: document.currentScript.previousElementSibling, "
        f"images: {json.dumps(uris)}, fps: {fps / etirement:.4f}, "
        f"depart: {LOTTIE_DEPART_MS}}});</script>"
    )


# Figures qui reçoivent la datation des mots. "stat" n'est pas une séquence,
# mais son anneau se remplit à l'instant où le chiffre est prononcé.
# "figure_source" reste exclue : une image n'a pas d'éléments qui se posent
# l'un après l'autre.
_FIGURES_CADENCEES = {
    "stat", "etapes", "couches", "comparaison", "liste", "neurones", "cycle",
    "matrice", "frise", "embranchement", "venn", "scene", "terminal", "recap",
}


def _graphic_svg(
    graphique: dict | None, w: int = 970, h: int = CANEVAS_H,
    mots: "list[dict] | None" = None, duree_ms: int = 0,
) -> str:
    """
    Rend la figure d'une slide.

    Args:
        mots:     Datation des mots prononcés, pour les figures qui s'animent
        duree_ms: Durée de la slide, nécessaire au repli quand un libellé n'est
                  pas retrouvé dans la narration
    """
    if not graphique:
        return ""
    gtype = graphique.get("type", "")
    donnees = graphique.get("donnees", {})
    # Import local : figures_dev importe lui-même ses helpers depuis ce module.
    from content.figures_dev import figure_recap, figure_terminal

    renderers = {
        "stat": _svg_stat, "etapes": _svg_etapes, "couches": _svg_couches,
        "comparaison": _svg_comparaison, "liste": _svg_liste, "neurones": _svg_neurones,
        "cycle": _svg_cycle, "matrice": _svg_matrice, "frise": _svg_frise,
        "embranchement": _svg_embranchement, "venn": _svg_venn,
        "figure_source": _svg_figure_source, "animation": _svg_animation,
        "scene": _svg_scene, "terminal": figure_terminal, "recap": figure_recap,
    }
    fn = renderers.get(gtype)
    if not fn:
        return ""
    # La bande des repères est prélevée ici, une fois : chaque figure reçoit la
    # zone utile et ignore que le document est plus grand qu'elle.
    utile_w, utile_h = w - 2 * CADRE_MARGE, h - 2 * CADRE_MARGE
    if gtype in _FIGURES_CADENCEES:
        return fn(donnees, utile_w, utile_h, mots=mots, duree_ms=duree_ms)
    return fn(donnees, utile_w, utile_h)


def _repartir(h: int, n: int, hauteur_max: int, ratio_ecart: float) -> tuple[int, int, int]:
    """
    Répartit n blocs sur la hauteur disponible, écarts compris.

    Plafonner la hauteur d'un bloc sans rendre l'écart proportionnel produisait
    un groupe compact centré dans un grand vide — le défaut même que ce cadrage
    corrige. Ici les deux grandissent ensemble jusqu'au plafond.

    Args:
        h:            Hauteur du canevas
        n:            Nombre de blocs
        hauteur_max:  Plafond esthétique d'un bloc
        ratio_ecart:  Écart entre blocs, en proportion de leur hauteur

    Returns:
        (hauteur d'un bloc, écart entre blocs, ordonnée du premier bloc)
    """
    unite = h / (n + max(n - 1, 0) * ratio_ecart)
    bloc = int(min(hauteur_max, unite))
    ecart = int(bloc * ratio_ecart)
    total = n * bloc + max(n - 1, 0) * ecart
    return bloc, ecart, max(0, (h - total) // 2)


# ── Animation ─────────────────────────────────────────────────────────────────
#
# Les slides sont animées mais ne jouent jamais toutes seules : chaque animation
# est en pause et c'est `window.seek(t)` qui fixe l'instant à afficher. Le rendu
# devient déterministe — même sortie quelle que soit la machine ou la charge,
# condition indispensable pour caler l'image sur la voix.
#
# Au chargement, la page se place sur son état final : une capture PNG sans appel
# à seek() donne donc exactement l'image d'avant, et l'export existant continue
# de fonctionner sans modification.

DUREE_SLIDE_DEFAUT_MS = 6000

# Durée de la sortie de chaque slide. Courte : elle tombe dans le silence qui
# sépare deux segments de narration, et plus longue elle effacerait le texte
# pendant que la voix prononce encore le dernier mot.
SORTIE_MS = 240
# Écart entre l'entrée de deux mots d'un titre cinétique.
PAS_TITRE_MS = 70


# Mises en valeur d'un titre (`**…**`), en alternance : cyan, violet, cyan…
# (« Ça marche **chez toi**, ça marche **chez eux** »).
CLASSES_VALEUR = ("val-accent", "val-violet")


def _texte_brut(texte: object) -> str:
    """Texte sans les marques de mise en valeur `**`, pour tout autre affichage."""
    return str(texte or "").replace("**", "")


def _mots_valorises(texte: object) -> list[list[tuple[str, "str | None"]]]:
    """
    Découpe un titre en mots, chaque mot en bouts (texte, classe de mise en valeur).

    Un mot peut mêler les deux (« **Docker**, ») et une mise en valeur couvrir
    plusieurs mots (« **chez toi** ») : l'état bascule à chaque `**` rencontré.
    La ponctuation détachée reste collée au mot précédent (`_mots_insecables`).
    Un `**` orphelin met en valeur la fin du titre plutôt que de s'afficher.
    """
    dedans, rang, mots = False, -1, []
    for mot in _mots_insecables(str(texte or "")):
        bouts: list[tuple[str, "str | None"]] = []
        for k, bout in enumerate(mot.split("**")):
            if k:
                dedans = not dedans
                rang += dedans
            if bout:
                classe = CLASSES_VALEUR[rang % 2] if dedans else None
                bouts.append((bout, classe))
        if bouts:
            mots.append(bouts)
    return mots


def _titre_cinetique(texte: str, depart_ms: int) -> str:
    """
    Titre dont les mots entrent l'un après l'autre, floutés, à partir de `depart_ms`.

    Les segments `**…**` sont mis en valeur (cyan puis violet en alternance) ;
    tout le texte est échappé.
    """
    from html import escape

    def bout_html(texte_bout: str, classe: "str | None") -> str:
        # Espace insécable : « vraiment ? » ne doit pas se couper dans son bloc.
        html = escape(texte_bout).replace(" ", " ")
        return f'<span class="{classe}">{html}</span>' if classe else html

    return " ".join(
        f'<span class="mot-titre anim" style="animation-delay:'
        f'{depart_ms + i * PAS_TITRE_MS}ms">'
        f'{"".join(bout_html(t, c) for t, c in mot)}</span>'
        for i, mot in enumerate(_mots_valorises(texte))
    )


def _attributs_slide(duree_ms: int, sortie: bool = True) -> str:
    """
    Classes et réglages du conteneur `.slide`, qui porte la sortie.

    Sans sortie, la slide s'enchaîne sur la suivante sans coupure visible
    (accroche suivie de l'introduction, voir `_intro_slide_html`).
    """
    if not sortie:
        return 'class="slide"'
    return (f'class="slide anim sortie" style="--sortie:{SORTIE_MS}ms;'
            f'animation-delay:{max(0, duree_ms - SORTIE_MS)}ms"')

# Nom de la chaîne. Il ne s'affiche plus en bandeau sur chaque slide (il y
# disputait la place au titre) mais signe la slide de fin, avec l'appel à s'abonner.
MARQUE = "IA.MALINE"
PSEUDO = "@" + MARQUE.lower()

# Titre de la slide de fin quand le script n'en fournit pas (`titre_conclusion`).
TITRE_CONCLUSION_DEFAUT = "Progresse en **IA**, une vidéo à la fois"

# Mascotte détourée (fond transparent) de l'accroche et de la fin. `robot.jpg`,
# à côté, reste la référence des scènes Wan2GP.
MASCOTTE = (Path(__file__).resolve().parent.parent
            / "media" / "assets" / "mascotte" / "robot_detoure.png")
# Rapport largeur / hauteur du fichier (850 × 1310), pour centrer sans mesurer.
MASCOTTE_RATIO = 850 / 1310

# ── Habillage « dev » ─────────────────────────────────────────────────────────
#
# Le registre des vidéos tech qui marchent sur TikTok, dans les couleurs de la
# chaîne : fond nuit à lueurs bleue et violette sous un nid d'abeille discret,
# titres très gras à gauche avec mises en valeur cyan et violet, sous-titres en
# capitales par morceaux de deux ou trois mots, barre de progression en haut.
VIOLET = "#7B2FFF"
# `VIOLET` n'offre que ~3:1 de contraste sur le fond nuit : assez pour une barre,
# trop peu pour du texte. Les mots en valeur prennent sa version claire.
VIOLET_CLAIR = "#A78BFA"
GRIS_SOUS_TITRE = "#9AA4BC"

# Sous-titres : morceaux courts, un seul visible à la fois. Bande au-dessus de
# la zone réservée à l'interface de TikTok (1920 − ZONE_SURE_BAS = 1500).
MORCEAU_MAX_MOTS = 3
MORCEAU_MAX_CARS = 18
SOUS_TITRES_HAUT = 1340
SOUS_TITRES_H = 150
SOUS_TITRE_CORPS = 66
# Le flux (titre, figure) s'arrête au-dessus de la bande des sous-titres.
FLUX_BAS = 1920 - SOUS_TITRES_HAUT + 20

# Le corps d'un titre suit sa longueur, en paliers (caractères au plus → corps
# en px) : le prompt demande des titres courts, mais un titre long d'un ancien
# script garderait sinon ce corps et descendrait dans la
# bande des sous-titres.
CORPS_ACCROCHE = ((34, 102), (56, 88), (10**6, 76))
CORPS_TITRE_SEUL = ((40, 120), (64, 100), (10**6, 84))
MASCOTTE_H = 400
# Canevas des schémas dans la zone élargie (952 × ~600 px, voir `.graphic`).
CANEVAS_LARGE_H = 600
# Début du bloc d'une slide sans figure, sous la zone sûre du haut.
HAUT_BLOC_SEUL = 110


def _corps_titre(texte: object, paliers: "tuple[tuple[int, int], ...]") -> int:
    """Corps d'un titre selon sa longueur affichée (sans les `**`)."""
    longueur = len(_texte_brut(texte))
    return next(corps for plafond, corps in paliers if longueur <= plafond)

# Désaturation appliquée aux photos de fond. Pas 1.0 : un gris parfait rendrait
# les fonds mornes, alors qu'un reste de teinte laisse vivre l'image sous le
# voile bleu nuit. Mettre 0 pour retrouver les couleurs d'origine.
DESATURATION = 0.85

# ── Typographie ───────────────────────────────────────────────────────────────
#
# Les polices vivent dans le dépôt et sont référencées par chemin absolu résolu à
# la génération. Indispensable : le rendu tourne dans un conteneur qui ne contient
# que 58 polices — ni Segoe UI ni aucune police système Windows. Sans cela, la
# vidéo produite par Airflow n'a pas la typographie de celle rendue en local.

POLICES_DIR = Path(__file__).parent / "assets" / "fonts"


def _polices_css() -> str:
    """Déclarations @font-face pointant vers les fichiers du dépôt."""
    titre = (POLICES_DIR / "SpaceGrotesk.ttf").resolve().as_uri()
    texte = (POLICES_DIR / "Manrope.ttf").resolve().as_uri()
    return f"""
@font-face{{font-family:'Titrage';src:url('{titre}') format('truetype');font-weight:300 700}}
@font-face{{font-family:'Lecture';src:url('{texte}') format('truetype');font-weight:200 800}}
"""


_POLICES_CSS = _polices_css()

# Hauteur réservée en bas de cadre : l'interface de TikTok (légende, boutons,
# pseudo) recouvre cette bande. Tout contenu qui y tombe est illisible.
# Bandes recouvertes par l'interface de TikTok, sur un cadre de 1080 × 1920.
# Aucun contenu ne doit y tomber : le badge disparaissait sous la barre d'état,
# et le texte lu sous la légende et le pseudo.
#
# Ces valeurs sont volontairement généreuses. L'interface varie selon l'appareil,
# la longueur de la légende et la présence d'une musique : viser au plus juste
# revient à parier sur le téléphone du spectateur.
ZONE_SURE_HAUT = 240
ZONE_SURE_BAS = 420
# Colonne des boutons « j'aime », commentaire, partage et profil.
ZONE_SURE_DROITE = 180
# Ordonnée où commence cette colonne. Elle n'occupe que le bas de l'écran
# (avatar, j'aime, commentaires, partage) : au-dessus, rien ne recouvre la
# droite du cadre : réserver la colonne sur toute la hauteur réduisait le
# terminal à 77 % de la largeur, et son texte d'autant.
# Le texte reste dans la colonne étroite ; seule la figure s'élargit, et elle
# s'arrête alors au-dessus de cette ordonnée.
ZONE_SURE_DROITE_DEPUIS = 1040
# Marge latérale du contenu, à gauche partout et à droite au-dessus de la colonne.
MARGE = 64

_ANIM_CSS = """
.anim{animation-play-state:paused;animation-fill-mode:both}
/* Longhands et non la forme raccourcie `animation:` : celle-ci réinitialiserait
   animation-play-state et animation-fill-mode posés par .anim, et l'animation
   échapperait au pilotage par seek(). */
/* Dérive lente du fond : un aplat immobile paraît figé. Discrète, le nid
   d'abeille ne doit pas attirer l'œil. */
.fond{position:absolute;inset:-6%;z-index:0;
      animation-name:derive;animation-duration:var(--duree);
      animation-timing-function:linear}
.slide{position:relative;z-index:1}
@keyframes derive{
  from{transform:scale(1.00) translate(0,0)}
  to  {transform:scale(1.05) translate(-1.2%,-0.9%)}
}
/* Entrée floutée : le flou se résorbe pendant que le mot apparaît. */
@keyframes floute{
  from{opacity:0;filter:blur(12px);transform:translateY(14px)}
  to  {opacity:1;filter:blur(0);transform:translateY(0)}
}
.entre{opacity:0;animation-name:floute;animation-duration:700ms;
       animation-timing-function:cubic-bezier(.2,.8,.2,1)}
/* Flottement de la mascotte, rejoué tant que la slide dure. */
@keyframes flotte{from{transform:translateY(0)} to{transform:translateY(-16px)}}
.flotte{animation-name:flotte;animation-duration:2400ms;
        animation-iteration-count:infinite;animation-direction:alternate;
        animation-timing-function:ease-in-out}
/* Barre de progression : ses keyframes, propres à chaque slide, sont dans la
   page (début et fin de la slide rapportés à la durée totale). */
.progres{position:absolute;top:0;left:0;width:100%;height:6px;z-index:2;
         transform-origin:left center;animation-name:progresser;
         animation-duration:var(--duree);animation-timing-function:linear}
/* Sous-titres : chaque morceau et chaque mot porte sa propre fenêtre de
   visibilité, en paliers (`step-end`) sur toute la durée de la slide. Le
   remplissage `both` les garde toujours actives : une animation terminée sans
   remplissage disparaîtrait de getAnimations() et seek() ne la reverrait plus. */
.morceau,.mot-st{animation-duration:var(--duree);animation-timing-function:step-end}
@keyframes surgit{
  from{opacity:0;transform:scale(.94)}
  to  {opacity:1;transform:scale(1)}
}
/* Construction progressive des figures : chaque élément paraît au moment où la
   narration le nomme, au lieu que le schéma soit posé d'un bloc. Les instants
   viennent de la datation mot à mot déjà produite pour le karaoké. */
.parait{opacity:0;transform-box:fill-box;transform-origin:center;
        animation-name:surgit;animation-duration:520ms;
        animation-timing-function:cubic-bezier(.2,.8,.2,1)}
/* Le trait se dessine de son origine vers sa pointe. `--long` porte la longueur,
   calculée en Python : mesurer dans le navigateur casserait le déterminisme. */
.trace{stroke-dasharray:var(--long);stroke-dashoffset:var(--long);
       animation-name:tracer;animation-duration:520ms;
       animation-timing-function:ease-out}
@keyframes tracer{to{stroke-dashoffset:0}}
/* L'anneau de `stat` se remplit jusqu'à sa valeur. `--arc` porte la longueur
   visible, `--tour` la circonférence entière : l'intervalle valant un tour
   complet, un seul tiret paraît sur le tracé, et le décalage le fait croître
   depuis son origine au lieu de le faire glisser. Plus lent que `.trace` — c'est
   le geste qui dit la part, il ne doit pas passer inaperçu. */
.remplit{stroke-dasharray:var(--arc) var(--tour);stroke-dashoffset:var(--arc);
         animation-name:remplir;animation-duration:900ms;
         animation-timing-function:cubic-bezier(.2,.8,.2,1)}
@keyframes remplir{to{stroke-dashoffset:0}}
/* La barre de `comparaison` pousse depuis son origine quand son bloc paraît :
   c'est sa longueur qui porte la valeur, la voir croître la fait lire. */
.pousse{transform-box:fill-box;transform-origin:left center;
        animation-name:pousser;animation-duration:700ms;
        animation-timing-function:cubic-bezier(.2,.8,.2,1)}
@keyframes pousser{from{transform:scaleX(0)} to{transform:scaleX(1)}}
/* Le chiffre de `stat` marque le moment où la voix le prononce. */
.rebond{transform-box:fill-box;transform-origin:center;
        animation-name:rebondir;animation-duration:600ms;
        animation-timing-function:ease-out}
@keyframes rebondir{0%{transform:scale(1)} 35%{transform:scale(1.12)} 100%{transform:scale(1)}}
/* Titre cinétique : les mots entrent l'un après l'autre au lieu d'un bloc. */
.mot-titre{display:inline-block;opacity:0;
           animation-name:floute;animation-duration:700ms;
           animation-timing-function:cubic-bezier(.2,.8,.2,1)}
/* Sortie de slide : le contenu s'efface en remontant sur ses dernières
   millisecondes, et l'entrée de la slide suivante prend le relais. Le fond
   reste : la coupure se fait sous le texte, pas sur toute l'image. */
.sortie{animation-name:sortir;animation-duration:var(--sortie);
        animation-timing-function:ease-in}
@keyframes sortir{to{opacity:0;transform:translateY(-36px)}}
/* Scène animée : bords fondus dans le fond de la slide, pour qu'elle n'y
   découpe pas un carré. `contain` : la scène n'est jamais recadrée. */
.scene{object-fit:contain;
       -webkit-mask-image:radial-gradient(closest-side,#000 72%,transparent 100%);
       mask-image:radial-gradient(closest-side,#000 72%,transparent 100%)}
"""


def _morceaux(mots: list[str]) -> list[list[int]]:
    """
    Groupe les mots en morceaux de sous-titres : 3 mots et MORCEAU_MAX_CARS
    caractères au plus, coupés après toute ponctuation.

    Returns:
        Indices des mots de chaque morceau, dans l'ordre
    """
    groupes: list[list[int]] = []
    courant: list[int] = []
    for i, mot in enumerate(mots):
        texte = " ".join(mots[j] for j in courant + [i])
        if courant and (len(courant) >= MORCEAU_MAX_MOTS or len(texte) > MORCEAU_MAX_CARS):
            groupes.append(courant)
            courant = []
        courant.append(i)
        # « 2. Télécharger » : un numéro de liste ouvre le morceau, il ne le clôt pas.
        if mot[-1:] in ".,;:!?…" and not re.fullmatch(r"\d+\.", mot):
            groupes.append(courant)
            courant = []
    if courant:
        groupes.append(courant)
    return groupes


def _texte_prononce(texte: object) -> str:
    """Texte tel qu'envoyé au TTS : sans `**`, emoji, hashtag ni URL."""
    from media.tts import _clean_text

    return _clean_text(_texte_brut(texte))


def _datation_sous_titres(
    texte: str, mots: "list[dict] | None", duree_ms: int, prefixe: str = "",
) -> list[tuple[str, int]]:
    """
    Mots à sous-titrer et instant de début de chacun, en millisecondes.

    Avec datation (media/align.py), les instants sont ceux de la voix. Le
    `prefixe` couvre les mots prononcés mais retirés de la datation — le titre
    d'une slide de contenu, voir `mots_par_slide` — répartis avant le premier
    mot daté. Sans datation, le texte est réparti uniformément jusqu'à la sortie.
    """
    if mots:
        premier = max(0, int(mots[0]["debut"] * 1000))
        tete = prefixe.split()
        pas = premier / len(tete) if tete else 0
        return ([(m, int(i * pas)) for i, m in enumerate(tete)]
                + [(m["mot"], max(0, int(m["debut"] * 1000))) for m in mots])
    tous = f"{prefixe} {texte}".split()
    if not tous:
        return []
    pas = max(1, duree_ms - SORTIE_MS) / len(tous)
    return [(m, int(i * pas)) for i, m in enumerate(tous)]


def _fenetre(nom: str, prop: str, hors: str, dans: str,
             debut_ms: int, fin_ms: int, duree_ms: int) -> str:
    """
    Keyframes en paliers : `prop` vaut `dans` sur [debut, fin[ et `hors` ailleurs.

    Jouées en `step-end` sur toute la durée de la slide, donc fixées par seek()
    à n'importe quel instant, dans les deux sens.
    """
    a = 100 * min(max(debut_ms, 0), duree_ms) / duree_ms
    b = 100 * min(max(fin_ms, debut_ms), duree_ms) / duree_ms
    return (f"@keyframes {nom}{{0%{{{prop}:{hors}}}{a:.3f}%{{{prop}:{dans}}}"
            f"{b:.3f}%{{{prop}:{hors}}}100%{{{prop}:{hors}}}}}")


def _sous_titres_html(datation: list[tuple[str, int]], duree_ms: int) -> str:
    """
    Sous-titres en morceaux de 2–3 mots, un seul visible à la fois, le mot en
    cours de prononciation en cyan. Remplace le paragraphe karaoké.

    Le premier morceau paraît dès l'instant 0, le dernier reste jusqu'à la fin.
    """
    from html import escape

    if not datation:
        return ""
    duree_ms = max(duree_ms, 1)
    groupes = _morceaux([m for m, _ in datation])
    largeur = 1080 - 64 - ZONE_SURE_DROITE
    keyframes, blocs = [], []
    for g, idx in enumerate(groupes):
        debut = 0 if g == 0 else datation[idx[0]][1]
        fin = datation[groupes[g + 1][0]][1] if g + 1 < len(groupes) else duree_ms
        keyframes.append(_fenetre(f"st{g}", "opacity", "0", "1", debut, fin, duree_ms))
        texte = " ".join(datation[i][0] for i in idx)
        # Corps réduit pour un morceau long : un mot seul ne se coupe pas.
        corps = max(36, min(SOUS_TITRE_CORPS, int(largeur / (0.74 * max(len(texte), 1)))))
        spans = []
        for k, i in enumerate(idx):
            mot_debut = debut if k == 0 else datation[i][1]
            mot_fin = datation[idx[k + 1]][1] if k + 1 < len(idx) else fin
            keyframes.append(_fenetre(f"sm{i}", "color", "#FFFFFF", ACCENT,
                                      mot_debut, mot_fin, duree_ms))
            spans.append(f'<span class="mot-st anim" style="animation-name:sm{i}">'
                         f'{escape(datation[i][0])}</span>')
        blocs.append(f'<div class="morceau anim" style="animation-name:st{g};'
                     f'font-size:{corps}px">{" ".join(spans)}</div>')
    return (f'<div class="soustitres"><style>{"".join(keyframes)}</style>'
            f'{"".join(blocs)}</div>')


def _seek_js(duree_ms: int) -> str:
    """Script de pilotage temporel injecté dans chaque slide."""
    return f"""
<script>
  const DUREE_MS = {duree_ms};
  // Animations Lottie de la page, jamais jouées seules : seek() les place.
  const lotties = (window.__lotties || []).map(l => ({{
    anim: lottie.loadAnimation({{container: l.el, renderer: 'svg', loop: false,
                                autoplay: false, animationData: l.donnees}}),
    depart: l.depart, boucle: l.boucle,
  }}));
  window.seek = (t) => {{
    document.getAnimations().forEach(a => {{ a.pause(); a.currentTime = t; }});
    lotties.forEach(l => {{
      // Une boucle rejoue tant que la slide dure ; sinon l'animation reste
      // posée sur sa dernière image.
      const duree = l.anim.getDuration(false) * 1000;
      const x = Math.max(0, t - l.depart);
      l.anim.goToAndStop(l.boucle ? x % duree : Math.min(duree - 1, x), false);
    }});
    // Scènes animées : l'image de l'instant t, et une promesse qui ne se
    // résout qu'une fois décodée — Playwright l'attend avant la capture.
    return Promise.all((window.__scenes || []).map(s => {{
      const i = Math.min(s.images.length - 1,
                         Math.max(0, Math.floor((t - s.depart) * s.fps / 1000)));
      if (s.el.dataset.i === String(i)) return Promise.resolve();
      s.el.dataset.i = String(i);
      s.el.src = s.images[i];
      return s.el.decode().catch(() => {{}});
    }}));
  }};
  // État par défaut : le dernier instant **avant la sortie**, où tout est posé.
  // L'aperçu PNG et le contrôle de mise en page le lisent ; le vrai dernier
  // instant montrerait une slide déjà effacée.
  window.seek(Math.max(0, DUREE_MS - {SORTIE_MS}));
</script>"""


def _bg_style(image_path: "Path | None") -> str:
    """
    Retourne le CSS de fond : photo assombrie, ou dégradé par défaut.

    Le repli n'est pas un aplat mais un dégradé à deux foyers : un aplat n'offre
    aucune prise au mouvement de caméra, la slide paraîtrait figée dès la fin de
    l'animation d'apparition. Le voile sur les photos est allégé (0,55–0,70 au
    lieu de 0,75–0,85), sinon l'illustration est invisible.
    """
    if image_path and image_path.exists():
        # `as_uri()` et non `as_posix()` : le chemin porte le stem du sujet, donc
        # une apostrophe dès que le titre en contient une — ce qui, en français,
        # est la règle plus que l'exception. Posée telle quelle dans `url('…')`,
        # elle referme la chaîne CSS, invalide la déclaration, et le navigateur
        # jette **toute la feuille de style** : plus de flex, plus de polices,
        # plus de mise en page. Mesuré sur le stem
        # « veille_les_agents_ia_et_l'apprentissage_par_renforcement », dont la
        # vidéo est sortie sans aucune mise en forme. `as_uri()` encode
        # l'apostrophe en %27 et l'espace en %20, comme le fait déjà
        # `_polices_css()` — d'où des polices intactes et un fond cassé.
        url = image_path.resolve().as_uri()
        # Voile dégressif et non uniforme : le texte vit dans le bas de la slide
        # (accroche, contenu lu, zone sûre), l'illustration dans le haut. Un
        # voile constant devait donc arbitrer entre les deux — assez sombre pour
        # le texte, l'image disparaissait ; assez clair pour l'image, l'accroche
        # se posait sur des aplats clairs et devenait illisible. Le dégradé rend
        # l'image au haut de cadre et protège le bas.
        # Désaturation du calque de fond. La sélection de la photo la plus sombre
        # règle le contraste, pas la couleur : sur « global communication
        # network », la plus sombre disponible restait jaune vif. Le filtre rend
        # la charte indépendante de ce que la banque d'images propose, alors
        # qu'une recherche par couleur ne fait qu'espérer un bon résultat.
        # `.fond` ne contient aucun texte, il peut donc être filtré entièrement.
        return (
            f"background:linear-gradient(180deg,"
            f"rgba(13,13,26,0.42) 0%,"
            f"rgba(13,13,26,0.52) 40%,"
            f"rgba(13,13,26,0.88) 74%,"
            f"rgba(13,13,26,0.96) 100%),"
            # `as_uri()` porte déjà le schéma « file:// », ne pas le redoubler.
            f"url('{url}') center/cover no-repeat;"
            f"filter:grayscale({DESATURATION}) brightness(0.92)"
        )
    return (
        "background:"
        "radial-gradient(circle at 28% 22%, #1b3a6b 0%, transparent 55%),"
        "radial-gradient(circle at 78% 72%, #3d1a5c 0%, transparent 52%),"
        "linear-gradient(160deg, #0D0D1A 0%, #141428 100%)"
    )


def _fond_css() -> str:
    """
    Fond de l'habillage, aux couleurs de la chaîne : nuit #0D0D1A → #141428,
    lueur bleue en haut à gauche, violette en bas à droite, sous un nid
    d'abeille.

    Le motif a été choisi sur planche contre une grille, une trame de points et
    des ondes (27/10/2026) : les points disparaissaient à l'échelle d'un
    téléphone, les ondes se lisaient comme un défaut d'image. Opacité 0,13 :
    à 0,10 il s'effaçait dans le haut, à 0,16 il disputait l'attention au texte.
    """
    hexagones = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
                 "width='56' height='100'%3E%3Cpath d='M28 66L0 50V16L28 0l28 16v34L28 "
                 "66v34' fill='none' stroke='rgba(255,255,255,0.13)' "
                 "stroke-width='1.5'/%3E%3C/svg%3E")
    return (
        f"background-image:"
        f"url(\"{hexagones}\"),"
        f"radial-gradient(circle at 28% 22%,rgba(27,58,107,0.95) 0%,transparent 55%),"
        f"radial-gradient(circle at 78% 72%,rgba(61,26,92,0.95) 0%,transparent 52%),"
        f"linear-gradient(160deg,#0D0D1A 0%,#141428 100%);"
        f"background-size:56px 100px,100% 100%,100% 100%,100% 100%"
    )


_HABILLAGE_CSS = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1080px;height:1920px;background:#0D0D1A;overflow:hidden;
     font-family:'Lecture',sans-serif;color:#fff}}
.fond{{{_fond_css()}}}
.progres{{background:linear-gradient(90deg,{ACCENT},{VIOLET})}}
/* Le flux (titres, figure) s'arrête au-dessus de la bande des sous-titres. */
.slide{{width:100%;height:100%;
       padding:{ZONE_SURE_HAUT}px {ZONE_SURE_DROITE}px {FLUX_BAS}px 64px;
       display:flex;flex-direction:column}}
.titre{{font-family:'Titrage',sans-serif;font-weight:700;color:#fff;
       line-height:1.08;letter-spacing:-1.5px;
       /* Space Grotesk plafonne à 700 : un contour fin l'épaissit pour qu'un
          titre se lise d'un coup d'œil en défilement. */
       -webkit-text-stroke:1.4px currentColor}}
.titre.accroche{{letter-spacing:-2.5px;margin-top:44px}}
.titre.seul{{letter-spacing:-2.5px;line-height:1.02}}
.titre.contenu{{font-size:72px;margin-top:90px}}
.val-accent{{color:{ACCENT};text-shadow:0 0 26px rgba(0,212,255,0.40)}}
.val-violet{{color:{VIOLET_CLAIR};text-shadow:0 0 26px rgba(167,139,250,0.40)}}
.surtitre{{font-size:26px;font-weight:700;color:{ACCENT};letter-spacing:7px;
          text-transform:uppercase;margin-bottom:30px}}
.surtitre::after{{content:"";display:block;width:52px;height:4px;margin-top:14px;
                 border-radius:2px;background:{ACCENT}}}
.sous-titre{{font-size:46px;font-weight:500;color:{GRIS_SOUS_TITRE};line-height:1.4;
            margin-top:30px}}
.mascotte-boite{{align-self:center;position:relative;
                left:{(ZONE_SURE_DROITE - 64) // 2}px}}
.mascotte{{display:block;width:auto;
          filter:drop-shadow(0 22px 34px rgba(0,0,0,0.55))
                 drop-shadow(0 0 40px rgba(0,212,255,0.18))}}
/* Élastique : la figure prend la place restante sous le titre. Elle s'élargit
   jusqu'à la marge droite (au-dessus de la colonne des boutons, rien ne la
   recouvre) et, en contrepartie, le flux d'une slide à figure s'arrête à
   ZONE_SURE_DROITE_DEPUIS. Borner la hauteur plutôt que garder la largeur
   réduite : un terminal ne vit que de sa largeur, c'est elle qui fait la
   taille de son texte. */
.slide:has(> .graphic){{padding-bottom:{1920 - ZONE_SURE_DROITE_DEPUIS}px}}
.graphic{{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;
         margin-right:{MARGE - ZONE_SURE_DROITE}px;padding:28px 0;opacity:0;
         animation-name:surgit;animation-duration:800ms;animation-delay:420ms;
         animation-timing-function:cubic-bezier(.2,.8,.2,1)}}
.graphic svg{{max-height:100%;width:auto}}
.soustitres{{position:absolute;left:64px;right:{ZONE_SURE_DROITE}px;
            top:{SOUS_TITRES_HAUT}px;height:{SOUS_TITRES_H}px}}
.morceau{{position:absolute;inset:0;display:flex;align-items:center;
         justify-content:center;flex-wrap:wrap;gap:0 .28em;white-space:nowrap;
         font-weight:800;text-transform:uppercase;letter-spacing:.5px;
         text-shadow:0 4px 18px rgba(0,0,0,0.85)}}
.filet{{height:1px;margin-top:70px;background:linear-gradient(90deg,rgba(0,212,255,0.05),rgba(0,212,255,0.32),rgba(0,212,255,0.05))}}
.signature{{display:flex;align-items:center;gap:22px;margin-top:48px;
           font-family:'Titrage',sans-serif;font-size:56px;font-weight:700}}
.signature small{{font-size:32px;color:{GRIS_SOUS_TITRE};font-weight:500}}
{_ANIM_CSS}
"""


def _progres_html(progres: "tuple[int, int] | None", duree_ms: int) -> str:
    """
    Barre de progression globale : sa largeur suit l'instant dans la vidéo,
    pas dans la slide. Absente sans durées (aperçu PNG).

    Args:
        progres: (début de la slide, durée totale de la vidéo), en ms
    """
    if not progres or progres[1] <= 0:
        return ""
    debut, total = progres
    p0 = min(1.0, debut / total)
    p1 = min(1.0, (debut + duree_ms) / total)
    return (f"<style>@keyframes progresser{{from{{transform:scaleX({p0:.5f})}}"
            f"to{{transform:scaleX({p1:.5f})}}}}</style>"
            f'<div class="progres anim"></div>')


def _mascotte_html(hauteur: int, delai_ms: int = 0, flottement_ms: int = 0) -> str:
    """
    Mascotte détourée, entrée floutée puis léger flottement piloté par seek().

    Args:
        delai_ms:      Instant de l'entrée
        flottement_ms: Phase du flottement à l'instant 0, pour reprendre le
                       mouvement là où la slide précédente l'a laissé
    """
    from html import escape

    if not MASCOTTE.exists():
        logger.warning("Mascotte absente (%s) : slide sans mascotte", MASCOTTE)
        return ""
    return (f'<div class="mascotte-boite entre anim" style="animation-delay:{delai_ms}ms">'
            f'<img class="mascotte flotte anim" style="height:{hauteur}px;'
            f'animation-delay:{-flottement_ms}ms" '
            f'src="{escape(MASCOTTE.as_uri())}" alt=""></div>')


def _page(corps: str, duree_ms: int, mots: "list[dict] | None",
          texte_lu: str, prefixe_lu: str = "",
          progres: "tuple[int, int] | None" = None,
          enchainement: "tuple[int, int] | None" = None) -> str:
    """
    Document complet d'une slide : fond, barre de progression, contenu qui
    porte la sortie, sous-titres et pilotage par seek().

    Args:
        corps:      Contenu de `.slide` (titres, figure…)
        texte_lu:   Narration de la slide, sous-titrée en repli sans datation
        prefixe_lu: Mots prononcés avant ceux datés (titre d'une slide de contenu)
        progres:    (début de la slide, durée totale) pour la barre globale
        enchainement: (instant de la slide dans une séquence, durée de la
                      séquence) : la dérive du fond court sur toute la séquence
                      et seule la dernière slide de la séquence sort
    """
    fond = ""
    sortie = True
    if enchainement:
        debut, total = enchainement
        fond = f' style="animation-duration:{total}ms;animation-delay:{-debut}ms"'
        sortie = debut + duree_ms >= total
    lottie_js = (
        f'<script src="{LOTTIE_JS.resolve().as_uri()}"></script>'
        if 'class="lottie"' in corps else ""
    )
    sous_titres = _sous_titres_html(
        _datation_sous_titres(_texte_prononce(texte_lu), mots, duree_ms,
                              _texte_prononce(prefixe_lu)),
        duree_ms,
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">{lottie_js}
<style>
{_POLICES_CSS}
{_HABILLAGE_CSS}
body{{--duree:{duree_ms}ms}}
</style></head>
<body>
<div class="fond anim"{fond}></div>
{_progres_html(progres, duree_ms)}
<div {_attributs_slide(duree_ms, sortie)}>
{corps}
{sous_titres}
</div>{_seek_js(duree_ms)}
</body></html>"""


def _entree(classe: str, texte: str, delai_ms: int) -> str:
    """Bloc de texte échappé à entrée floutée ; rien si le texte est vide."""
    from html import escape

    if not texte:
        return ""
    return (f'<div class="{classe} entre anim" style="animation-delay:{delai_ms}ms">'
            f'{escape(_texte_brut(texte))}</div>')


def _slide_html(content: dict, total: int, bg: str = "#0D0D1A",
                image_path: "Path | None" = None,
                duree_ms: int = DUREE_SLIDE_DEFAUT_MS,
                mots: "list[dict] | None" = None,
                progres: "tuple[int, int] | None" = None) -> str:
    """
    Slide de contenu : surtitre et titre en haut, figure au milieu, sous-titres.

    Le paragraphe lu n'est plus affiché : les sous-titres le portent. Sans
    figure, le titre grandit et se centre, avec son sous-titre gris.
    `total`, `bg` et `image_path` restent acceptés pour les appelants existants ;
    ce rendu n'affiche plus d'illustration de fond.
    """
    titre = content.get("titre", "")
    # Les mêmes horodatages servent aux sous-titres et à la construction de la
    # figure : un seul calage, deux usages. Le canevas suit la zone élargie,
    # plus large que haute (voir `.graphic`).
    svg = _graphic_svg(content.get("graphique"), w=970, h=CANEVAS_LARGE_H,
                       mots=mots, duree_ms=duree_ms)
    if svg:
        # Titre puis figure, rien entre : le LLM met surtitre et sous-titre
        # partout malgré la consigne, et ils repoussaient la figure vers le bas.
        corps = (f'<div class="titre contenu">{_titre_cinetique(titre, 180)}</div>'
                 f'<div class="graphic anim">{svg}</div>')
    else:
        # Sans figure, le bloc de titre occupe le haut : centré, il laissait
        # un vide au-dessus qui faisait paraître la slide creuse.
        corps = (
            f'<div style="margin-top:{HAUT_BLOC_SEUL}px">'
            + _entree("surtitre", content.get("surtitre", ""), 80)
            + f'<div class="titre seul" style="font-size:'
              f'{_corps_titre(titre, CORPS_TITRE_SEUL)}px">{_titre_cinetique(titre, 180)}</div>'
            + _entree("sous-titre", content.get("sous_titre", ""), 520)
            + '<div class="filet entre anim" style="animation-delay:800ms"></div></div>'
        )
    return _page(corps, duree_ms, mots, content.get("contenu", ""),
                 prefixe_lu=titre, progres=progres)


# Délai qui place une entrée bien avant l'instant 0 : elle est déjà jouée,
# l'élément paraît posé dès la première image.
DEJA_ENTRE_MS = -100_000


def _accroche_corps(script: dict, deja_entre: bool = False, flottement_ms: int = 0) -> str:
    """
    Contenu de l'accroche : mascotte, titre de la vidéo, sous-titre gris.

    Args:
        deja_entre:    Éléments posés dès l'instant 0, sans animation d'entrée
        flottement_ms: Phase du flottement de la mascotte à l'instant 0
    """
    titre = script.get("titre_video", "")
    decale = DEJA_ENTRE_MS if deja_entre else 0
    return (
        f'<div style="margin-top:100px"></div>'
        f'{_mascotte_html(MASCOTTE_H, decale, flottement_ms)}'
        f'<div class="titre accroche" style="font-size:{_corps_titre(titre, CORPS_ACCROCHE)}px">'
        f'{_titre_cinetique(titre, 250 + decale)}</div>'
        + _entree("sous-titre", script.get("sous_titre_video", ""), 700 + decale)
    )


def _title_slide_html(script: dict, bg: str = "#0D0D1A",
                      image_path: "Path | None" = None,
                      duree_ms: int = DUREE_SLIDE_DEFAUT_MS,
                      mots: "list[dict] | None" = None,
                      progres: "tuple[int, int] | None" = None,
                      enchainement: "tuple[int, int] | None" = None) -> str:
    """
    Accroche : mascotte centrée, titre de la vidéo, sous-titre gris.

    Args:
        enchainement: (instant de la slide dans la séquence accroche +
                      introduction, durée de la séquence), en ms. Fourni quand
                      une introduction suit : l'accroche ne sort pas, elle se
                      prolonge dans l'introduction.
    """
    return _page(_accroche_corps(script), duree_ms, mots, script.get("hook", ""),
                 progres=progres, enchainement=enchainement)


def _intro_slide_html(script: dict, bg: str = "#0D0D1A",
                      image_path: "Path | None" = None,
                      duree_ms: int = DUREE_SLIDE_DEFAUT_MS,
                      mots: "list[dict] | None" = None,
                      progres: "tuple[int, int] | None" = None,
                      enchainement: "tuple[int, int] | None" = None) -> str:
    """
    Introduction : à l'écran, la suite exacte de l'accroche.

    Un écran « Au programme » qui répétait le titre sur un fond vide était un
    temps mort, juste au moment où le spectateur décide de rester. La slide
    reprend donc l'accroche déjà posée — sans entrée, mascotte et fond à
    la phase où l'accroche les a laissés — et seuls les sous-titres défilent.
    """
    debut, total = enchainement or (0, duree_ms)
    corps = _accroche_corps(script, deja_entre=True, flottement_ms=debut)
    return _page(corps, duree_ms, mots, script.get("introduction", ""),
                 progres=progres, enchainement=(debut, total))


def _conclusion_slide_html(script: dict, bg: str = "#0D0D1A",
                           image_path: "Path | None" = None,
                           duree_ms: int = DUREE_SLIDE_DEFAUT_MS,
                           mots: "list[dict] | None" = None,
                           progres: "tuple[int, int] | None" = None) -> str:
    """Slide de fin : mascotte, titre de conclusion, appel à s'abonner, signature."""
    from html import escape

    titre = script.get("titre_conclusion") or TITRE_CONCLUSION_DEFAUT
    mini = (f'<img style="height:104px" src="{escape(MASCOTTE.as_uri())}" alt="">'
            if MASCOTTE.exists() else "")
    nom, _, suite = MARQUE.lower().partition(".")
    signature = (f'<div class="signature entre anim" style="animation-delay:1100ms">'
                 f'{mini}<span>{escape(nom)}<small>{"." + escape(suite) if suite else ""}'
                 f'</small></span></div>')
    corps = (
        f'<div style="margin-top:100px"></div>{_mascotte_html(MASCOTTE_H)}'
        f'<div class="titre accroche" style="font-size:{_corps_titre(titre, CORPS_ACCROCHE)}px">'
        f'{_titre_cinetique(titre, 250)}</div>'
        + _entree("sous-titre", f"Abonne-toi à {PSEUDO}", 800)
        + signature
    )
    return _page(corps, duree_ms, mots, script.get("conclusion", ""), progres=progres)


def plan_slides(script: dict) -> list[dict]:
    """
    Ordonnance les slides d'un script et les relie à leurs segments audio.

    Source unique de l'ordre : le rendu et la génération HTML s'appuyaient
    auparavant chacun sur leur propre reconstruction à partir du champ `numero`,
    et toute insertion de slide les faisait diverger en silence.

    Les identifiants visuels sont attribués séquentiellement (slide_00, 01, …)
    tandis que `segment` conserve le nom du fichier audio correspondant, qui suit
    la numérotation du LLM.

    Returns:
        [{'id', 'genre', 'segment', 'donnees'}] dans l'ordre d'affichage
    """
    plan: list[dict] = []

    def ajouter(genre: str, segment: str, donnees=None) -> None:
        plan.append({
            "id": f"slide_{len(plan):02d}",
            "genre": genre,
            "segment": segment,
            "donnees": donnees,
        })

    # L'accroche ouvre la vidéo : les deux premières secondes décident du
    # visionnage, une salutation y serait un temps mort. La présentation du
    # sujet vient juste après.
    ajouter("titre", "hook")

    # Slide d'accueil, facultative : absente des scripts générés avant son
    # introduction, le pipeline doit continuer à les traiter.
    if script.get("introduction"):
        ajouter("intro", "intro")
    for slide in script.get("slides", []):
        ajouter("contenu", f"slide_{slide.get('numero', len(plan)):02d}", slide)
    ajouter("conclusion", "conclusion")

    return plan


def generate_html_slides(
    script_path: Path,
    images: "dict[str, Path] | None" = None,
    durees_ms: "dict[str, int] | None" = None,
    mots_ms: "dict[str, list[dict]] | None" = None,
) -> list[Path]:
    """
    Génère des slides HTML avec graphiques SVG directement depuis le JSON.

    Args:
        script_path: Chemin vers le fichier JSON du script
        images:      Dictionnaire optionnel {slide_id: image_path} depuis fetch_slide_images()
        durees_ms:   Durée d'affichage de chaque slide, en millisecondes, indexée
                     par slide_id. Cale les animations sur le segment audio et,
                     si toutes les slides en ont une, la barre de progression
                     globale ; sans elle, une durée par défaut s'applique et la
                     page reste utilisable pour un export PNG statique.
        mots_ms:     Horodatage des mots prononcés, par slide_id, produit par
                     media/align.py. Cale les sous-titres sur la voix ; sans lui,
                     ils sont répartis uniformément sur la slide.

    Returns:
        Liste ordonnée des fichiers HTML générés
    """
    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    output_dir = script_path.parent / script_path.stem / "html"
    output_dir.mkdir(parents=True, exist_ok=True)

    total = len(script.get("slides", []))
    html_files: list[Path] = []
    imgs = images or {}
    durees = durees_ms or {}
    mots = mots_ms or {}
    plan = plan_slides(script)

    # Progression globale : render.py fournit la durée de chaque slide, donc
    # l'instant où chacune commence dans la vidéo montée bout à bout. Sans la
    # durée de toutes les slides (aperçu PNG, segment manquant), pas de barre :
    # une progression partielle serait fausse.
    debuts: dict[str, int] = {}
    if durees and all(e["id"] in durees for e in plan):
        cumul = 0
        for e in plan:
            debuts[e["id"]] = cumul
            cumul += durees[e["id"]]
    duree_totale = sum(durees.get(e["id"], 0) for e in plan)

    # Accroche et introduction ne font qu'un écran : la seconde reprend la
    # première là où elle en est (voir `_intro_slide_html`).
    enchainement: dict[str, tuple[int, int]] = {}
    if [e["genre"] for e in plan[:2]] == ["titre", "intro"]:
        d_titre, d_intro = (durees.get(e["id"], DUREE_SLIDE_DEFAUT_MS) for e in plan[:2])
        enchainement = {plan[0]["id"]: (0, d_titre + d_intro),
                        plan[1]["id"]: (d_titre, d_titre + d_intro)}

    for entree in plan:
        sid = entree["id"]
        commun = {
            "image_path": imgs.get(sid) or imgs.get("slide_00"),
            "duree_ms": durees.get(sid, DUREE_SLIDE_DEFAUT_MS),
            "mots": mots.get(sid),
            "progres": (debuts[sid], duree_totale) if sid in debuts else None,
        }
        genre = entree["genre"]

        if genre == "intro":
            html = _intro_slide_html(script, **commun, enchainement=enchainement.get(sid))
        elif genre == "titre":
            html = _title_slide_html(script, **commun, enchainement=enchainement.get(sid))
        elif genre == "conclusion":
            html = _conclusion_slide_html(script, **commun)
        else:
            html = _slide_html(entree["donnees"], total, **commun)

        p = output_dir / f"{sid}.html"
        p.write_text(html, encoding="utf-8")
        html_files.append(p)
        logger.debug("HTML %s (%s) créé", sid, genre)

    logger.info("%d slides HTML préparées", len(html_files))

    logger.info("%d fichiers HTML générés dans %s", len(html_files), output_dir)
    return html_files


def main() -> None:
    """Point d'entrée CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Génération de slides 9:16 depuis un script JSON")
    parser.add_argument("--input", required=True, help="Chemin vers le fichier JSON du script")
    args = parser.parse_args()

    script_path = Path(args.input)
    if not script_path.exists():
        logger.error("Fichier introuvable : %s", script_path)
        return

    fichiers = generate_html_slides(script_path)
    print(f"{len(fichiers)} slides générées dans {fichiers[0].parent}")


if __name__ == "__main__":
    main()
