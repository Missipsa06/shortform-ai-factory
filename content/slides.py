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


# ── Constantes partagées des figures ─────────────────────────────────────────
#
# Les onze schémas vivent dans `content/schemas.py`, dans la charte « verre
# lumineux » de `design/schemas/reference.html`. Restent ici les couleurs et
# les outils de texte qu'ils partagent avec `figure_source`, et la police
# embarquée sans laquelle le texte des figures échapperait au rendu
# reproductible.

POLICE_SVG = "'Lecture', sans-serif"

# Hauteur par défaut du canevas d'une figure : c'est le rapport de forme qui
# compte, le document SVG se met à l'échelle de la place que la slide lui laisse.
CANEVAS_H = 900

ACCENT = "#00D4FF"
NEUTRE = "#5C6A8A"       # traits secondaires, crédit d'une figure source
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


# Bande réservée tout autour de la zone utile : les halos des figures y
# débordent sans être coupés par le bord du document.
CADRE_MARGE = 24


def _svg(w: int, h: int, corps: str, defs: str = "") -> str:
    """
    Enveloppe SVG de `figure_source`.

    `w` et `h` sont les dimensions **utiles**. Le document est plus grand de
    `CADRE_MARGE` de chaque côté, et son `viewBox` décale l'origine d'autant :
    la figure garde son repère habituel sans rien savoir de la marge.
    """
    total_w, total_h = w + 2 * CADRE_MARGE, h + 2 * CADRE_MARGE
    return f"""<svg width="{total_w}" height="{total_h}" viewBox="{-CADRE_MARGE} {-CADRE_MARGE} {total_w} {total_h}" xmlns="http://www.w3.org/2000/svg">
  <defs>{defs}</defs>
{corps}
</svg>"""


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
    from content.schemas import SCHEMAS

    renderers = {
        **SCHEMAS,
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
# Les schémas prennent une zone haute plutôt que large : 836 × 800, de sous le
# titre jusqu'au-dessus des sous-titres. Dans la bande large, ils tenaient en
# 950 × 550 et laissaient 300 px vides au-dessus des sous-titres. En contrepartie
# ils respectent la colonne des boutons sur toute leur hauteur.
SCHEMAS_HAUTS = {"stat", "etapes", "couches", "comparaison", "liste", "neurones",
                 "cycle", "matrice", "frise", "embranchement", "venn"}
FIGURE_BAS = SOUS_TITRES_HAUT - 20
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
/* Colonne de `comparaison` à deux valeurs : elle monte depuis sa base. */
.monte{transform-box:fill-box;transform-origin:center bottom;
       animation-name:monter;animation-duration:900ms;
       animation-timing-function:cubic-bezier(.2,.8,.2,1)}
@keyframes monter{from{transform:scaleY(0)} to{transform:scaleY(1)}}
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
/* ── Schémas (content/schemas.py) ── */
/* Entrées : glisser depuis la gauche, tomber à sa place, ou simple fondu. */
.parait.gauche{animation-name:gaucher;animation-duration:700ms}
@keyframes gaucher{from{opacity:0;transform:translateX(-60px)}to{opacity:1;transform:none}}
.parait.chute{animation-name:chuter;animation-duration:900ms}
@keyframes chuter{from{opacity:0;transform:translateY(-90px)}to{opacity:1;transform:none}}
.parait.fondu{animation-name:fondre;animation-duration:600ms}
@keyframes fondre{from{opacity:0}to{opacity:1}}
/* Points qui circulent sur une liaison. Le décalage d'une boucle vaut le pas
   des points (PAS_FLUX) : la reprise ne se voit pas. */
.coule{animation-name:couler;animation-duration:1100ms;animation-iteration-count:infinite;
       animation-timing-function:linear}
@keyframes couler{to{stroke-dashoffset:-30}}
/* L'élément dont la voix parle s'allume, puis rend la main. */
.eclaire{opacity:0;animation-name:eclairer;animation-duration:2400ms;animation-timing-function:ease}
@keyframes eclairer{0%,55%{opacity:1}100%{opacity:0}}
/* Chacun son tour, en boucle (cycle, embranchement) ; durée posée par élément. */
.tour{opacity:0;animation-name:tour-role;animation-iteration-count:infinite;
      animation-timing-function:ease-in-out}
@keyframes tour-role{0%{opacity:0}8%,22%{opacity:1}34%,100%{opacity:0}}
.respire{animation-name:respirer;animation-duration:2200ms;animation-iteration-count:infinite;
         animation-direction:alternate;animation-timing-function:ease-in-out}
@keyframes respirer{from{opacity:.35}to{opacity:1}}
.activation{animation-name:activer;animation-duration:2400ms;animation-iteration-count:infinite;
            animation-timing-function:ease-in-out}
@keyframes activer{0%{opacity:.12}22%{opacity:1}55%,100%{opacity:.12}}
.flotte{animation-name:flotter;animation-duration:3000ms;animation-iteration-count:infinite;
        animation-direction:alternate;animation-timing-function:ease-in-out}
@keyframes flotter{from{transform:translateY(-7px)}to{transform:translateY(7px)}}
.pivote{transform-box:view-box;animation-name:pivoter;animation-duration:9000ms;
        animation-iteration-count:infinite;animation-timing-function:linear}
@keyframes pivoter{to{transform:rotate(360deg)}}
/* Tête de l'anneau de `stat` : même durée et même courbe que `.remplit`. */
.tete{transform-box:view-box;transform:rotate(var(--angle));animation-name:tete;
      animation-duration:900ms;animation-timing-function:cubic-bezier(.2,.8,.2,1)}
@keyframes tete{from{transform:rotate(0deg)}}
.allume{animation-name:allumer;animation-duration:250ms;animation-timing-function:linear}
@keyframes allumer{from{stroke:#FFFFFF;stroke-opacity:.16}}
.glisse{animation-name:glisser;animation-duration:1100ms;
        animation-timing-function:cubic-bezier(.2,.8,.2,1)}
@keyframes glisser{from{opacity:0;transform:translateX(var(--dx))}}
.onde{transform-box:fill-box;transform-origin:center;animation-name:onde;
      animation-duration:2200ms;animation-iteration-count:infinite;animation-timing-function:ease-out}
@keyframes onde{from{opacity:.9;transform:scale(1)}to{opacity:0;transform:scale(1.9)}}
/* Reflet sur la barre en tête d'une comparaison, en boucle. */
.reflet{animation-name:refleter;animation-duration:2600ms;animation-iteration-count:infinite;
        animation-timing-function:ease-in-out}
@keyframes refleter{0%{transform:translateX(0)}60%,100%{transform:translateX(var(--course))}}
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
.slide:has(> .graphic.haute){{padding-bottom:{1920 - FIGURE_BAS}px}}
.graphic.haute{{margin-right:0}}
.graphic svg{{max-height:100%;max-width:100%;width:auto}}
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
    # figure : un seul calage, deux usages. Un schéma prend la zone haute,
    # les autres figures (terminal, figure d'article) la zone large.
    graphique = content.get("graphique")
    haute = isinstance(graphique, dict) and graphique.get("type") in SCHEMAS_HAUTS
    if haute:
        from content.schemas import ZONE_H, ZONE_W

        w, h = ZONE_W + 2 * CADRE_MARGE, ZONE_H + 2 * CADRE_MARGE
    else:
        w, h = 970, CANEVAS_LARGE_H
    svg = _graphic_svg(graphique, w=w, h=h, mots=mots, duree_ms=duree_ms)
    if svg:
        # Titre puis figure, rien entre : le LLM met surtitre et sous-titre
        # partout malgré la consigne, et ils repoussaient la figure vers le bas.
        corps = (f'<div class="titre contenu">{_titre_cinetique(titre, 180)}</div>'
                 f'<div class="graphic{" haute" if haute else ""} anim">{svg}</div>')
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
