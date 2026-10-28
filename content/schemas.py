"""
Schémas des slides, dans la charte « verre lumineux ».

Modèle : `design/schemas/reference.html`, dessiné à la main dans la DA de la
chaîne. Chaque figure dessine dans la zone de la référence, 836 × 800
(`ZONE_W`, `ZONE_H`) : ses coordonnées s'y reportent telles quelles, et le
document SVG se met à l'échelle de la place que la slide lui laisse.

Trois règles tiennent la cohérence d'une figure à l'autre :

  1. Des cartes de verre translucide au liseré clair, plutôt que le trait seul.
     Le registre « formes ouvertes, un trait » donnait des schémas justes mais
     creux, mesuré sur planche : la figure occupait le haut de sa zone et
     laissait le reste vide.
  2. Un seul élément en dégradé cyan → violet clair, avec un halo : celui où le
     regard doit finir (le résultat, la couche du dessus, la question). Le
     reste reste en verre neutre.
  3. Le mouvement porte le sens. Ce qui circule circule (points sur les
     liaisons), ce qui boucle tourne, l'élément dont la voix parle s'allume.

Toutes les animations, infinies comprises, sont des animations CSS en pause
que `window.seek(t)` place à l'instant voulu : le rendu reste reproductible
image par image. Leurs classes sont décrites dans `_ANIM_CSS` (slides.py).
"""

import logging
import math

from content.slides import (
    ACCENT,
    CADRE_MARGE,
    ICONES,
    POLICE_SVG,
    TEXTE,
    TEXTE_DOUX,
    VIOLET_CLAIR,
    _bloc_texte,
    _cadence,
    _echapper,
    _fraction,
    _icone,
    _instant_du_chiffre,
    _item,
    _repartir,
)

logger = logging.getLogger(__name__)

ZONE_W, ZONE_H = 836, 800
POLICE_TITRE = "'Titrage', sans-serif"
NOIR = "#0B0B1A"          # contenu posé sur le dégradé : un trait cyan sur du cyan ne se lit pas
LISERE = "rgba(255,255,255,0.14)"
HALO = "filter:drop-shadow(0 0 19px rgba(0,212,255,0.32))"
RAYON_CARTE = 28
# Pas des points qui circulent : il vaut le déplacement d'une boucle de
# `.coule` (slides.py), sinon la reprise se verrait à chaque tour.
PAS_FLUX = 30
# Épaisseur de l'anneau de `stat` : il porte une mesure, il doit se lire en
# défilement — même raison que la hauteur des barres de `comparaison`.
EPAISSEUR_ANNEAU = 30
# Délai entre l'entrée d'un élément et celle de la liaison qui en part.
APRES_ELEMENT_MS = 420

DEFS = f"""
    <linearGradient id="gA" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{ACCENT}"/><stop offset="1" stop-color="{VIOLET_CLAIR}"/>
    </linearGradient>
    <linearGradient id="gN" x1="0" y1="0" x2="1" y2="1">
      <stop offset=".3" stop-color="#FFFFFF"/><stop offset="1" stop-color="{VIOLET_CLAIR}"/>
    </linearGradient>
    <linearGradient id="gV" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2="{ZONE_H}">
      <stop offset="0" stop-color="{ACCENT}"/><stop offset="1" stop-color="{VIOLET_CLAIR}"/>
    </linearGradient>
    <linearGradient id="verre" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#FFFFFF" stop-opacity=".085"/>
      <stop offset="1" stop-color="#FFFFFF" stop-opacity=".03"/>
    </linearGradient>
    <linearGradient id="verre-acc" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0E203E" stop-opacity=".94"/>
      <stop offset="1" stop-color="#221646" stop-opacity=".94"/>
    </linearGradient>
    <linearGradient id="opaque" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1E2448" stop-opacity=".97"/>
      <stop offset="1" stop-color="#161836" stop-opacity=".97"/>
    </linearGradient>
    <linearGradient id="plaque" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#FFFFFF" stop-opacity=".13"/>
      <stop offset="1" stop-color="#FFFFFF" stop-opacity=".04"/>
    </linearGradient>
    <linearGradient id="barre" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#343E60"/><stop offset="1" stop-color="#66739A"/>
    </linearGradient>
    <linearGradient id="reflet" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#FFFFFF" stop-opacity="0"/>
      <stop offset=".5" stop-color="#FFFFFF" stop-opacity=".55"/>
      <stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/>
    </linearGradient>
    <radialGradient id="halo">
      <stop offset="0" stop-color="{ACCENT}" stop-opacity=".18"/>
      <stop offset=".7" stop-color="#7B2FFF" stop-opacity=".06"/>
      <stop offset="1" stop-color="#7B2FFF" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="venn-g" cx=".42" cy=".4">
      <stop offset="0" stop-color="{ACCENT}" stop-opacity=".40"/>
      <stop offset="1" stop-color="{ACCENT}" stop-opacity=".10"/>
    </radialGradient>
    <radialGradient id="venn-d" cx=".58" cy=".4">
      <stop offset="0" stop-color="#7B2FFF" stop-opacity=".55"/>
      <stop offset="1" stop-color="#7B2FFF" stop-opacity=".14"/>
    </radialGradient>
    <filter id="glow" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="10" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="flou" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="18"/>
    </filter>
    <marker id="fl" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5"
            orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{TEXTE_DOUX}"/></marker>
    <marker id="fl-a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4"
            orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{VIOLET_CLAIR}"/></marker>"""


# ── Briques ──────────────────────────────────────────────────────────────────

def _document(w: int, h: int, corps: str, defs: str = "") -> str:
    """
    Document SVG d'une figure, à la taille demandée.

    Le `viewBox` couvre toujours la zone de référence, augmentée d'une bande de
    `CADRE_MARGE` où débordent les halos : la figure dessine dans son repère
    fixe, et le navigateur la met à l'échelle de `w` × `h`.
    """
    m = CADRE_MARGE
    return (f'<svg width="{w + 2 * m}" height="{h + 2 * m}" '
            f'viewBox="{-m} {-m} {ZONE_W + 2 * m} {ZONE_H + 2 * m}" '
            f'xmlns="http://www.w3.org/2000/svg">\n  <defs>{DEFS}{defs}\n  </defs>\n'
            f'{corps}\n</svg>')


def _parait(corps: str, instant: "int | None", entree: str = "") -> str:
    """
    Groupe qui paraît d'un bloc à l'instant donné.

    `entree` choisit le geste (`gauche`, `chute`, `fondu`) ; vide, il surgit
    en grandissant. Sans instant, le groupe est posé dès le départ.
    """
    if instant is None:
        return corps
    classe = f"parait {entree} anim" if entree else "parait anim"
    return f'  <g class="{classe}" style="animation-delay:{instant}ms">\n{corps}\n  </g>'


def _apres(instant: "int | None", decalage: int) -> "int | None":
    """Instant décalé, ou None quand la figure n'est pas cadencée."""
    return None if instant is None else max(0, instant + decalage)


def _carte(x: float, y: float, w: float, h: float, accent: bool = False,
           opaque: bool = False, rx: float = RAYON_CARTE) -> str:
    """
    Carte de verre ; l'accent prend le liseré en dégradé et un halo.

    `opaque` pour une carte posée sur un tracé qui ne doit pas la traverser :
    l'anneau d'un cycle passe derrière ses nœuds.
    """
    geo = f'x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{rx:g}"'
    if accent:
        return (f'  <rect class="carte" {geo} fill="url(#verre-acc)" stroke="url(#gA)" '
                f'stroke-width="3" style="{HALO}"/>')
    fond = "url(#opaque)" if opaque else "url(#verre)"
    return f'  <rect class="carte" {geo} fill="{fond}" stroke="{LISERE}" stroke-width="2"/>'


def _lueur(x: float, y: float, w: float, h: float, instant: "int | None",
           classe: str = "eclaire", style: str = "", rx: float = RAYON_CARTE) -> str:
    """
    Liseré lumineux posé sur une carte neutre, qui s'allume puis s'éteint.

    `eclaire` : une fois, le temps qu'on en parle — l'élément dont la voix parle
    est celui qui brille, puis il rend la main au suivant. `tour` : en boucle,
    chacun son tour (cycle, embranchement). Sans instant, rien : la figure
    posée ne doit pas garder un élément allumé au hasard.
    """
    if instant is None:
        return ""
    return (f'  <rect class="{classe} anim" x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" '
            f'rx="{rx:g}" fill="none" stroke="{ACCENT}" stroke-opacity=".85" stroke-width="3" '
            f'style="{HALO};animation-delay:{instant}ms{style}"/>')


def _pastille(cx: float, cy: float, r: float, accent: bool = False,
              icone: "str | None" = None, texte: "str | int | None" = None) -> str:
    """
    Disque porteur d'un pictogramme, d'un numéro ou d'un signe.

    Accentué, il prend le plein du dégradé et son contenu passe en noir ;
    sinon un disque à peine teinté et un contenu en accent.
    """
    if accent:
        disque, couleur = f'fill="url(#gA)" style="{HALO}"', NOIR
    else:
        disque = (f'fill="{ACCENT}" fill-opacity=".10" stroke="{ACCENT}" '
                  f'stroke-opacity=".42" stroke-width="2"')
        couleur = ACCENT
    parts = [f'  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:g}" {disque}/>']
    if icone:
        parts.append(_icone(icone, cx, cy, r * 1.12, couleur))
    elif texte is not None:
        corps = int(r * 1.0)
        parts.append(
            f'  <text x="{cx:.1f}" y="{cy + corps * 0.36:.1f}" text-anchor="middle" '
            f'fill="{couleur}" font-size="{corps}" font-weight="700" '
            f'font-family="{POLICE_TITRE}">{_echapper(texte)}</text>'
        )
    return "\n".join(parts)


def _liaison(d: str, instant: "int | None") -> str:
    """
    Liaison : un rail discret, et des points qui y circulent en boucle dans le
    sens de la lecture. Le rail seul disait « relié » ; le flux dit « passe de
    l'un à l'autre ».
    """
    corps = (
        f'  <path d="{d}" fill="none" stroke="{ACCENT}" stroke-opacity=".22" '
        f'stroke-width="3" stroke-linecap="round"/>\n'
        f'  <path class="coule anim" d="{d}" fill="none" stroke="{ACCENT}" stroke-width="7" '
        f'stroke-linecap="round" stroke-dasharray="0 {PAS_FLUX}"/>'
    )
    return _parait(corps, instant, "fondu")


def _kicker(texte: str, x: float, y: float, couleur: str = TEXTE_DOUX,
            ancre: str = "start") -> str:
    """Petit libellé en capitales espacées (nom d'axe, rang d'une couche)."""
    return (f'  <text x="{x:g}" y="{y:g}" text-anchor="{ancre}" fill="{couleur}" '
            f'font-size="26" font-weight="700" letter-spacing="5" '
            f'font-family="{POLICE_SVG}">{_echapper(str(texte).upper())}</text>')


def _instants(libelles: list, mots: "list[dict] | None", duree_ms: int) -> list:
    """Instant d'entrée de chaque élément, ou None partout sans cadence."""
    return _cadence(libelles, mots, duree_ms) if duree_ms else [None] * len(libelles)


# ── stat ─────────────────────────────────────────────────────────────────────

def schema_stat(donnees: dict, w: int, h: int,
                mots: "list[dict] | None" = None, duree_ms: int = 0) -> str:
    """
    Un chiffre clé dans une jauge : l'anneau **mesure** la valeur.

    Quand la valeur porte son propre dénominateur (`_fraction`), l'arc se
    remplit jusqu'à cette part ; une tête lumineuse mène le trait et les
    graduations s'allument à son passage. Sans dénominateur, l'anneau reste
    entier : remplir au prorata du premier chiffre lu inventerait une échelle.

    Le remplissage part à l'instant où la voix prononce le chiffre, qui rebondit
    alors ; sans datation, il part à 260 ms, sans rebond. L'arc est un cercle
    tronqué par `stroke-dasharray` et non un `path A`, qui à 100 % rejoindrait
    son point de départ et ne dessinerait rien.
    """
    brut = donnees.get("valeur", "?")
    texte = str(brut)
    description = donnees.get("description", "")
    part = _fraction(brut)
    instant = _instant_du_chiffre(brut, mots)
    depart = 260 if instant is None else instant
    cx, cy, r = 418, 330, 280
    tour = 2 * math.pi * r
    arc = tour * part if part is not None else 0.0
    rempli = part is not None and arc > EPAISSEUR_ANNEAU

    parts = [
        # Le rail d'abord : le tour entier, sans lequel un arc court ne se
        # compare à rien.
        f'  <circle class="rail" cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#FFFFFF" '
        f'stroke-opacity=".07" stroke-width="{EPAISSEUR_ANNEAU}"/>',
        f'  <circle cx="{cx}" cy="{cy}" r="{r - 18}" fill="url(#halo)"/>',
    ]
    # Graduations : allumées au passage de l'arc. Il suit une courbe rapide
    # puis lente ; l'instant de passage approche son inverse, 1 - √(1 - p).
    for i in range(60):
        f = i / 60
        a = f * 2 * math.pi - math.pi / 2
        majeure = i % 5 == 0
        r1, r2 = 318 - (10 if majeure else 0), 318 + (16 if majeure else 8)
        geo = (f'x1="{cx + r1 * math.cos(a):.1f}" y1="{cy + r1 * math.sin(a):.1f}" '
               f'x2="{cx + r2 * math.cos(a):.1f}" y2="{cy + r2 * math.sin(a):.1f}"')
        if rempli and f < part:
            t = depart + 900 * (1 - math.sqrt(max(0.0, 1 - f / part)))
            parts.append(f'  <line class="allume anim" {geo} stroke="{ACCENT}" stroke-opacity=".9" '
                         f'stroke-width="3" stroke-linecap="round" style="animation-delay:{t:.0f}ms"/>')
        else:
            parts.append(f'  <line {geo} stroke="#FFFFFF" stroke-opacity=".16" '
                         f'stroke-width="3" stroke-linecap="round"/>')

    if part is None:
        parts.append(f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="url(#gA)" '
                     f'stroke-width="{EPAISSEUR_ANNEAU}" filter="url(#glow)"/>')
    elif rempli:
        parts.append(
            f'  <circle class="remplit anim" cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="url(#gA)" stroke-width="{EPAISSEUR_ANNEAU}" stroke-linecap="round" '
            f'transform="rotate(-90 {cx} {cy})" filter="url(#glow)" '
            f'style="--arc:{arc:.1f};--tour:{tour:.1f};animation-delay:{depart}ms"/>'
        )
        # La tête tourne avec la même durée et la même courbe que l'arc : elle
        # reste à son extrémité pendant tout le remplissage.
        parts.append(
            f'  <g class="tete anim" style="--angle:{part * 360:.1f}deg;'
            f'transform-origin:{cx}px {cy}px;animation-delay:{depart}ms">'
            f'<circle cx="{cx}" cy="{cy - r}" r="19" fill="#FFFFFF" '
            f'style="filter:drop-shadow(0 0 14px {ACCENT})"/></g>'
        )

    # Le corps se déduit de la largeur disponible dans l'anneau : « 100% »
    # débordait là où « 95% » tenait. Le signe % est réduit, il ne porte rien.
    nombre, pct = (texte[:-1].rstrip(), "%") if texte.endswith("%") else (texte, "")
    largeur = len(nombre) + 0.55 * len(pct)
    corps = min(214, int(2 * (r - EPAISSEUR_ANNEAU - 26) / (max(largeur, 1) * 0.62)))
    rebond = "" if instant is None else f' class="rebond anim" style="animation-delay:{instant}ms"'
    suffixe = (f'<tspan font-size="{int(corps * 0.56)}" letter-spacing="0">%</tspan>'
               if pct else "")
    parts.append(
        f'  <text x="{cx}" y="{cy + corps * 0.36:.0f}" text-anchor="middle" fill="url(#gN)" '
        f'font-size="{corps}" font-weight="700" letter-spacing="-6" '
        f'font-family="{POLICE_TITRE}"{rebond}>{_echapper(nombre)}{suffixe}</text>'
    )
    if description:
        parts.append(_bloc_texte(description, cx, 712, ZONE_W, TEXTE_DOUX,
                                 plafond=50, plancher=30, max_lignes=2, poids=600))
    return _document(w, h, "\n".join(parts))


# ── etapes ───────────────────────────────────────────────────────────────────

def schema_etapes(donnees: dict, w: int, h: int,
                  mots: "list[dict] | None" = None, duree_ms: int = 0) -> str:
    """
    Une suite d'étapes, en colonne : pastilles numérotées sur un rail, cartes.

    Chaque étape paraît quand la voix la nomme et s'allume le temps qu'on en
    parle ; des points circulent sur le rail vers l'étape suivante. La dernière
    — le résultat — porte l'accent.
    """
    items = [_item(it) for it in donnees.get("items", [])[:4]]
    n = len(items)
    if not n:
        return ""
    bloc, ecart, y0 = _repartir(ZONE_H, n, hauteur_max=150, ratio_ecart=0.73)
    instants = _instants(items, mots, duree_ms)
    r = min(44, bloc // 2 - 8)
    xc, xg = 48, 124
    corps_max = 52 if n < 4 else 46

    parts = []
    for i, (texte, icone) in enumerate(items):
        y = y0 + i * (bloc + ecart)
        cy = y + bloc / 2
        dernier = i == n - 1
        if not dernier:
            suivant = y0 + (i + 1) * (bloc + ecart) + bloc / 2
            parts.append(_liaison(f"M{xc} {cy + r + 6:.0f} V{suivant - r - 6:.0f}",
                                  _apres(instants[i], APRES_ELEMENT_MS)))
        carte = [_carte(xg, y, ZONE_W - xg, bloc, accent=dernier)]
        if not dernier:
            carte.append(_lueur(xg, y, ZONE_W - xg, bloc, instants[i]))
        dispo = ZONE_W - xg - 44 - (120 if icone else 40)
        carte.append(_bloc_texte(texte, xg + 44, cy, dispo, TEXTE, plafond=corps_max,
                                 plancher=30, max_lignes=2, ancre="start", poids=700))
        if icone:
            carte.append(_icone(icone, ZONE_W - 40 - 33, cy, 66,
                                ACCENT if dernier else TEXTE_DOUX))
        parts.append(_parait("\n".join(carte), instants[i], "gauche"))
        parts.append(_parait(_pastille(xc, cy, r, accent=dernier, texte=i + 1),
                             _apres(instants[i], -80)))
    return _document(w, h, "\n".join(parts))


# ── couches ──────────────────────────────────────────────────────────────────

def _icone_aplatie(slug: "str | None", cx: float, cy: float, taille: float,
                   couleur: str) -> str:
    """Pictogramme couché sur une plaque isométrique : écrasé de moitié en hauteur."""
    contenu = ICONES.get(slug or "")
    if not contenu:
        return ""
    e = taille / 64
    return (f'<g transform="translate({cx - taille / 2:.1f},{cy - taille / 4:.1f}) '
            f'scale({e:.3f},{e / 2:.3f})" stroke="{couleur}" fill="none" stroke-width="4.5" '
            f'stroke-linecap="round" stroke-linejoin="round">{contenu}</g>')


def schema_couches(donnees: dict, w: int, h: int,
                   mots: "list[dict] | None" = None, duree_ms: int = 0) -> str:
    """
    Une pile de couches, en plaques isométriques ; le premier item est le haut.

    Les étapes suggèrent une circulation dans le temps, la pile une
    superposition. Chaque plaque tombe à sa place, puis la pile respire — un
    flottement déphasé d'une plaque à l'autre. La couche du dessus porte
    l'accent, son nom et son rang sont posés à droite.
    """
    items = [_item(it) for it in donnees.get("items", [])[:5]]
    n = len(items)
    if not n:
        return ""
    cx, a, b = 230, 212, 106           # centre, demi-largeur et demi-hauteur du losange
    pas = min(160, (ZONE_H - 2 * b - 40) / max(n - 1, 1))
    haut = ZONE_H / 2 - pas * (n - 1) / 2
    instants = _instants(items, mots, duree_ms)

    def losange(y: float, dy: float = 0) -> str:
        return (f"M{cx} {y - b + dy:.0f} L{cx + a} {y + dy:.0f} "
                f"L{cx} {y + b + dy:.0f} L{cx - a} {y + dy:.0f} Z")

    plaques, etiquettes = [], []
    for i, (texte, icone) in enumerate(items):
        y = haut + i * pas
        dessus = i == 0
        # Épaisseur : trois tranches décalées vers le bas, puis l'ombre portée.
        tranches = ("#13213D", "#182E52", "#1D3D66") if dessus else ("#15172F", "#1C1F40", "#262A52")
        corps = [f'  <path d="{losange(y, 34)}" fill="#000" opacity=".45" filter="url(#flou)"/>']
        for k, teinte in enumerate(tranches):
            corps.append(f'  <path d="{losange(y, 18 - 6 * k)}" fill="{teinte}"/>')
        if dessus:
            corps.append(f'  <path class="carte" d="{losange(y)}" fill="url(#verre-acc)" '
                         f'stroke="url(#gA)" stroke-width="3" stroke-linejoin="round" '
                         f'style="filter:drop-shadow(0 0 30px rgba(0,212,255,.35))"/>')
        else:
            corps.append(f'  <path class="carte" d="{losange(y)}" fill="url(#plaque)" '
                         f'stroke="rgba(255,255,255,.2)" stroke-width="2" stroke-linejoin="round"/>')
        corps.append(_icone_aplatie(icone, cx, y, 110, ACCENT if dessus else TEXTE_DOUX))
        flotte = (f'  <g class="flotte anim" style="animation-delay:{-500 * i}ms">\n'
                  + "\n".join(corps) + "\n  </g>")
        plaques.append(_parait(flotte, instants[i], "chute"))

        etiquette = [
            f'  <line x1="{cx + a + 10}" y1="{y:.0f}" x2="{cx + a + 48}" y2="{y:.0f}" '
            f'stroke="{ACCENT}" stroke-opacity=".22" stroke-width="3" stroke-linecap="round"/>',
            f'  <circle cx="{cx + a + 10}" cy="{y:.0f}" r="5" fill="{ACCENT}" '
            f'fill-opacity="{1 if dessus else .6}"/>',
            _kicker(f"Couche {n - i}", 508, y - 26, ACCENT if dessus else TEXTE_DOUX),
            _bloc_texte(texte, 508, y + 24, ZONE_W - 508, TEXTE, plafond=52, plancher=30,
                        max_lignes=2, ancre="start", poids=700),
        ]
        etiquettes.append(_parait("\n".join(etiquette), _apres(instants[i], 200), "fondu"))
    # Du bas vers le haut dans le document : la couche du dessus est peinte en dernier.
    return _document(w, h, "\n".join(reversed(plaques)) + "\n" + "\n".join(etiquettes))


# ── comparaison ──────────────────────────────────────────────────────────────

def schema_comparaison(donnees: dict, w: int, h: int,
                       mots: "list[dict] | None" = None, duree_ms: int = 0) -> str:
    """
    Des valeurs comparées : libellé, chiffre, barre épaisse sur son rail.

    Seule la plus grande prend le dégradé, un halo et un reflet qui la balaie
    en boucle : c'est le résultat de la comparaison. Des valeurs toutes égales
    ne comparent rien — aucune barre n'est accentuée, et un WARNING signale que
    le type est mal choisi. La barre pousse 180 ms après son bloc : le libellé
    se lit, puis la longueur.
    """
    items = donnees.get("items", [])[:4]
    n = len(items)
    if not n:
        return ""
    valeurs = [it.get("valeur", 0) for it in items]
    max_val = max(valeurs, default=100) or 100
    departage = len(set(valeurs)) > 1
    if not departage:
        logger.warning(
            "Comparaison sans écart : %d valeurs identiques (%s). La figure "
            "n'oppose rien — un autre type de graphique conviendrait mieux.",
            len(valeurs), valeurs[0] if valeurs else "?",
        )
    if n == 2 and departage:
        duel = _duel(items, mots, duree_ms, str(donnees.get("unite", "")).strip())
        if duel:
            return _document(w, h, duel)
    bloc, ecart, y0 = _repartir(ZONE_H, n, hauteur_max=200, ratio_ecart=0.25)
    instants = _instants(items, mots, duree_ms)
    barre_h = 62

    parts = []
    for i, item in enumerate(items):
        y = y0 + i * (bloc + ecart)
        val = item.get("valeur", 0)
        tete = departage and val == max_val
        largeur = max(int(ZONE_W * (val / max_val)), barre_h)
        yb = y + 86
        pousse = ("" if instants[i] is None
                  else f' class="pousse anim" style="animation-delay:{instants[i] + 180}ms"')
        bloc_svg = [
            f'  <text x="0" y="{y + 52}" fill="{TEXTE}" font-size="52" font-weight="700" '
            f'font-family="{POLICE_SVG}">{_echapper(item.get("label", ""))}</text>',
            f'  <text x="{ZONE_W}" y="{y + 62}" text-anchor="end" '
            f'fill="{"url(#gA)" if tete else TEXTE_DOUX}" font-size="80" font-weight="700" '
            f'letter-spacing="-2" font-family="{POLICE_TITRE}">{_echapper(val)}</text>',
            f'  <rect x="0" y="{yb}" width="{ZONE_W}" height="{barre_h}" rx="{barre_h // 2}" '
            f'fill="#FFFFFF" fill-opacity=".05" stroke="#FFFFFF" stroke-opacity=".10" stroke-width="1.5"/>',
            f'  <rect x="0" y="{yb}" width="{largeur}" height="{barre_h}" rx="{barre_h // 2}" '
            f'fill="{"url(#gA)" if tete else "url(#barre)"}"'
            + (' style="filter:drop-shadow(0 0 17px rgba(0,212,255,.45))"' if tete else "")
            + f'{pousse}/>',
        ]
        if tete:
            # Le reflet glisse dans la forme de la barre, après qu'elle a poussé.
            debut = 1500 if instants[i] is None else instants[i] + 1500
            bloc_svg.append(
                f'  <clipPath id="barre-{i}"><rect x="0" y="{yb}" width="{largeur}" '
                f'height="{barre_h}" rx="{barre_h // 2}"/></clipPath>'
                f'<g clip-path="url(#barre-{i})"><rect class="reflet anim" x="-260" y="{yb}" '
                f'width="260" height="{barre_h}" fill="url(#reflet)" '
                f'style="--course:{largeur + 260}px;animation-delay:{debut}ms"/></g>'
            )
        parts.append(_parait("\n".join(bloc_svg), instants[i], "fondu"))
    return _document(w, h, "\n".join(parts))


# ── comparaison à deux valeurs ───────────────────────────────────────────────
#
# Deux barres horizontales laissaient la zone presque vide, et quatre points
# d'écart sur 66 ne s'y voyaient pas. À deux valeurs, la donnée choisit le
# dessin : deux parts → deux jauges ; un rapport d'au moins `SEUIL_SAUT` → le
# saut, le facteur au centre ; deux valeurs proches → deux colonnes dont seul
# l'écart brille. Choisies sur planche le 28/10/2026, toutes trois gardées.

SEUIL_SAUT = 1.5


def _nombre(valeur: float) -> str:
    """Chiffre à la française : virgule décimale, une décimale au plus."""
    return f"{round(valeur, 1):g}".replace(".", ",")


def _chiffre(valeur: float, unite: str, x: float, y: float, corps: int, fill: str,
             dispo: float, ancre: str = "start", centre: bool = False) -> str:
    """
    Chiffre et son unité réduite, au corps plafonné par la largeur disponible.

    `centre` centre le texte sur `y` au lieu d'y poser sa ligne de base.
    """
    texte = _nombre(valeur)
    suffixe = f" {unite}" if unite else ""
    largeur = len(texte) + 0.55 * len(suffixe)
    corps = min(corps, int(dispo / (max(largeur, 1) * 0.62)))
    if centre:
        y += corps * 0.36
    tspan = (f'<tspan font-size="{int(corps * 0.53)}" letter-spacing="0">{_echapper(suffixe)}</tspan>'
             if suffixe else "")
    return (f'  <text x="{x:g}" y="{y:.0f}" text-anchor="{ancre}" fill="{fill}" '
            f'font-size="{corps}" font-weight="700" letter-spacing="-3" '
            f'font-family="{POLICE_TITRE}">{_echapper(texte)}{tspan}</text>')


def _duel(items: list, mots: "list[dict] | None", duree_ms: int, unite: str) -> str:
    """Corps de la variante qui convient aux deux valeurs, ou "" pour les barres."""
    valeurs = [it.get("valeur") for it in items]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in valeurs):
        return ""
    if min(valeurs) < 0:
        return ""                     # des colonnes partent de zéro
    instants = _instants(items, mots, duree_ms)
    if unite == "%" and max(valeurs) <= 100:
        return _duel_jauges(items, valeurs, instants, mots)
    if min(valeurs) > 0 and max(valeurs) / min(valeurs) >= SEUIL_SAUT:
        return _duel_saut(items, valeurs, instants, unite)
    return _duel_colonnes(items, valeurs, instants, unite)


def _duel_jauges(items: list, valeurs: list, instants: list,
                 mots: "list[dict] | None") -> str:
    """
    Deux parts, deux anneaux : la plus grande prend le dégradé, l'écart en
    points vient dessous. Chaque arc part quand la voix dit son chiffre.
    """
    r, ep, cy = 170, 26, 300
    tour = 2 * math.pi * r
    tete = valeurs.index(max(valeurs))
    parts = []
    for i, (cx, item, v) in enumerate(zip((200, 636), items, valeurs)):
        accent = i == tete
        prononce = _instant_du_chiffre(v, mots)
        depart = prononce if prononce is not None else (instants[i] if instants[i] is not None else 260)
        arc = tour * v / 100
        corps = [f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#FFFFFF" '
                 f'stroke-opacity=".08" stroke-width="{ep}"/>']
        if arc > ep:
            corps.append(
                f'  <circle class="remplit anim" cx="{cx}" cy="{cy}" r="{r}" fill="none" '
                f'stroke="{"url(#gA)" if accent else "#5C6A8A"}" stroke-width="{ep}" '
                f'stroke-linecap="round" transform="rotate(-90 {cx} {cy})"'
                + (' filter="url(#glow)"' if accent else "")
                + f' style="--arc:{arc:.1f};--tour:{tour:.1f};animation-delay:{depart}ms"/>'
            )
        corps.append(_chiffre(v, "%", cx, cy, 104 if accent else 88,
                              "url(#gA)" if accent else TEXTE_DOUX,
                              2 * (r - ep) - 20, ancre="middle", centre=True))
        corps.append(_bloc_texte(item.get("label", ""), cx, 560, 400,
                                 TEXTE if accent else TEXTE_DOUX,
                                 plafond=48, plancher=28, max_lignes=2, poids=700))
        parts.append(_parait("\n".join(corps), instants[i], "fondu"))
    ecart = "\n".join([
        _carte(248, 640, 340, 104, accent=True, rx=52),
        f'  <text x="418" y="708" text-anchor="middle" fill="{TEXTE}" font-size="50" '
        f'font-weight="700" font-family="{POLICE_TITRE}">'
        f'+{_nombre(max(valeurs) - min(valeurs))} pts</text>',
    ])
    parts.append(_parait(ecart, _apres(instants[1], 900), "fondu"))
    return "\n".join(parts)


def _duel_saut(items: list, valeurs: list, instants: list, unite: str) -> str:
    """
    Avant → après : la première valeur en verre neutre, la seconde accentuée,
    et le facteur qui les sépare dans une pastille sur la liaison.
    """
    a, b = valeurs
    echelle = max(valeurs)
    facteur = f"×{_nombre(b / a)}" if b >= a else f"÷{_nombre(a / b)}"

    def jauge(y: int, v: float, fill: str, instant: "int | None") -> str:
        largeur = max(748 * v / echelle, 14)
        pousse = ("" if instant is None
                  else f' class="pousse anim" style="animation-delay:{instant + 180}ms"')
        return (f'  <rect x="44" y="{y}" width="748" height="14" rx="7" fill="#FFFFFF" '
                f'fill-opacity=".08"/>\n'
                f'  <rect x="44" y="{y}" width="{largeur:.1f}" height="14" rx="7" '
                f'fill="{fill}"{pousse}/>')

    avant = "\n".join([
        _carte(0, 20, ZONE_W, 250),
        _kicker(items[0].get("label", ""), 44, 80),
        _chiffre(a, unite, 44, 200, 120, TEXTE_DOUX, 748),
        jauge(228, a, "#5C6A8A", instants[0]),
    ])
    apres = "\n".join([
        _carte(0, 500, ZONE_W, 290, accent=True),
        _kicker(items[1].get("label", ""), 44, 560, ACCENT),
        _chiffre(b, unite, 44, 712, 170, "url(#gA)", 748),
        jauge(748, b, "url(#gA)", instants[1]),
    ])
    pastille = "\n".join([
        _pastille(418, 385, 74, accent=True),
        f'  <text x="418" y="403" text-anchor="middle" fill="{NOIR}" '
        f'font-size="{min(52, int(130 / (len(facteur) * 0.6)))}" font-weight="700" '
        f'font-family="{POLICE_TITRE}">{_echapper(facteur)}</text>',
    ])
    return "\n".join([
        _parait(avant, instants[0], "fondu"),
        _liaison("M418 270 V500", _apres(instants[0], APRES_ELEMENT_MS)),
        _parait(pastille, instants[1]),
        _parait(apres, _apres(instants[1], 200), "chute"),
    ])


def _duel_colonnes(items: list, valeurs: list, instants: list, unite: str) -> str:
    """
    Deux rivaux proches : deux colonnes depuis zéro — un axe tronqué grossirait
    l'écart —, et c'est la tranche d'écart, seule, qui brille. La plus grande
    passe à gauche, l'étiquette d'écart a la marge pour elle.
    """
    base, haut, larg = 700, 520, 240
    echelle = max(valeurs)
    ordre = sorted(range(2), key=lambda i: -valeurs[i])
    xs = (236, 556)
    sommets = [base - haut * valeurs[i] / echelle for i in ordre]
    parts = [f'  <line x1="150" y1="{base}" x2="{ZONE_W}" y2="{base}" stroke="#FFFFFF" '
             f'stroke-opacity=".18" stroke-width="3"/>']
    for rang, (x, i, y) in enumerate(zip(xs, ordre, sommets)):
        accent = rang == 0
        cx = x + larg / 2
        monte = ("" if instants[i] is None
                 else f' class="monte anim" style="animation-delay:{instants[i]}ms"')
        fond = (f'fill="url(#gV)" fill-opacity=".35" stroke="url(#gA)" stroke-width="3"' if accent
                else f'fill="url(#verre)" stroke="{LISERE}" stroke-width="2"')
        bloc = [
            f'  <rect x="{x}" y="{y:.1f}" width="{larg}" height="{base - y:.1f}" rx="22" {fond}{monte}/>',
            _chiffre(valeurs[i], unite, cx, y - 30, 96 if accent else 76,
                     "url(#gA)" if accent else TEXTE_DOUX, 300, ancre="middle"),
            _bloc_texte(items[i].get("label", ""), cx, 752, 300, TEXTE if accent else TEXTE_DOUX,
                        plafond=40, plancher=24, max_lignes=1, poids=700),
        ]
        parts.append(_parait("\n".join(bloc), instants[i], "fondu"))

    dessus, dessous = sommets
    milieu = (dessus + dessous) / 2
    etiquette = f"+{_nombre(valeurs[ordre[0]] - valeurs[ordre[1]])}" + (f" {unite}" if unite else "")
    ecart = "\n".join([
        f'  <rect x="{xs[0]}" y="{dessus:.1f}" width="{larg}" height="{max(dessous - dessus, 6):.1f}" '
        f'rx="12" fill="url(#gA)" filter="url(#glow)"/>',
        f'  <line x1="{xs[0]}" y1="{dessous:.1f}" x2="{xs[1] + larg}" y2="{dessous:.1f}" '
        f'stroke="{VIOLET_CLAIR}" stroke-width="3" stroke-dasharray="10 10"/>',
        _carte(0, milieu - 36, 200, 72, accent=True, rx=36),
        f'  <text x="100" y="{milieu + 14:.0f}" text-anchor="middle" fill="{TEXTE}" '
        f'font-size="{min(40, int(180 / (len(etiquette) * 0.6)))}" font-weight="700" '
        f'font-family="{POLICE_TITRE}">{_echapper(etiquette)}</text>',
        f'  <path d="M200 {milieu:.1f} H{xs[0] - 4}" stroke="{ACCENT}" stroke-width="3"/>',
    ])
    fin = None if instants[0] is None else max(instants) + 1000
    parts.append(_parait(ecart, fin, "fondu"))
    return "\n".join(parts)


# ── liste ────────────────────────────────────────────────────────────────────

def schema_liste(donnees: dict, w: int, h: int,
                 mots: "list[dict] | None" = None, duree_ms: int = 0) -> str:
    """
    Des points clés, une carte par point.

    Pas d'accent permanent : une liste n'a ni ordre ni hiérarchie. Chaque point
    s'allume le temps d'être dit. Pastilles tout ou rien : un mélange de points
    pictogrammés et nus déciderait sans raison de leur poids visuel.
    """
    items = [_item(it) for it in donnees.get("items", [])[:5]]
    n = len(items)
    if not n:
        return ""
    instants = _instants(items, mots, duree_ms)
    a_icones = any(icone for _, icone in items)
    bloc, ecart, y0 = _repartir(ZONE_H, n, hauteur_max=180, ratio_ecart=0.39)
    r = min(54, bloc // 2 - 18)

    parts = []
    for i, (texte, icone) in enumerate(items):
        y = y0 + i * (bloc + ecart)
        cy = y + bloc / 2
        ligne = [_carte(0, y, ZONE_W, bloc), _lueur(0, y, ZONE_W, bloc, instants[i])]
        if a_icones:
            ligne.append(_pastille(40 + r, cy, r, icone=icone))
            if not icone:
                # Icône hors vocabulaire : la colonne reste, un point la marque.
                ligne.append(f'  <circle cx="{40 + r}" cy="{cy:.0f}" r="9" fill="{ACCENT}"/>')
            x_texte = 40 + 2 * r + 36
        else:
            ligne.append(f'  <circle cx="52" cy="{cy:.0f}" r="10" fill="url(#gA)" style="{HALO}"/>')
            x_texte = 92
        ligne.append(_bloc_texte(texte, x_texte, cy, ZONE_W - x_texte - 40, TEXTE,
                                 plafond=52, plancher=30, max_lignes=2, ancre="start", poids=700))
        parts.append(_parait("\n".join(ligne), instants[i], "gauche"))
    return _document(w, h, "\n".join(parts))


# ── neurones ─────────────────────────────────────────────────────────────────

def schema_neurones(donnees: dict, w: int, h: int,
                    mots: "list[dict] | None" = None, duree_ms: int = 0) -> str:
    """
    Un réseau de neurones, couches empilées de haut en bas.

    Sur un cadre 9:16 on empile, on n'aligne pas. Les couches paraissent l'une
    après l'autre ; des impulsions descendent le long des liaisons et une onde
    d'activation traverse le réseau de l'entrée vers la sortie, en boucle.
    Entrée et sortie portent l'accent.
    """
    couches = [max(1, min(int(c), 5)) for c in donnees.get("couches", [3, 4, 2])[:4]]
    n = len(couches)
    if not n:
        return ""
    max_n = max(couches)
    x_gauche, x_droite = 200, ZONE_W
    pas = min(188.0, (x_droite - x_gauche - 120) / max(max_n - 1, 1))
    xc = (x_gauche + x_droite) / 2
    yh, yb = 110, 690
    ys = [yh + (yb - yh) * i / (n - 1) if n > 1 else ZONE_H / 2 for i in range(n)]
    r = min(54, pas * 0.3 if max_n > 1 else 54, (yb - yh) / max(n - 1, 1) * 0.3)
    positions = [[(xc + (j - (k - 1) / 2) * pas, ys[i]) for j in range(k)]
                 for i, k in enumerate(couches)]
    instants = _instants(couches, mots, duree_ms)
    # L'onde part quand le réseau est complet, couche après couche.
    onde = 0 if instants[-1] is None else instants[-1] + 700

    parts = []
    for i in range(n - 1):
        liens = []
        for x1, y1 in positions[i]:
            for x2, y2 in positions[i + 1]:
                liens.append(f'  <line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
                             f'stroke="{ACCENT}" stroke-opacity=".14" stroke-width="2"/>')
                liens.append(f'  <path class="coule anim" d="M{x1:.0f} {y1:.0f} L{x2:.0f} {y2:.0f}" '
                             f'fill="none" stroke="{ACCENT}" stroke-opacity=".75" stroke-width="5" '
                             f'stroke-linecap="round" stroke-dasharray="0 {PAS_FLUX}"/>')
        parts.append(_parait("\n".join(liens), _apres(instants[i + 1], -200), "fondu"))
    for i, rangee in enumerate(positions):
        bord = i in (0, n - 1)
        nom = "Entrée" if i == 0 else "Sortie" if i == n - 1 else "Cachée"
        noeuds = [_kicker(nom, 0, ys[i] + 9, ACCENT if bord else TEXTE_DOUX)]
        for x, y in rangee:
            trait = ('stroke="url(#gA)" stroke-width="4" filter="url(#glow)"' if bord
                     else f'stroke="{ACCENT}" stroke-opacity=".5" stroke-width="3"')
            noeuds.append(f'  <circle cx="{x:.0f}" cy="{y:.0f}" r="{r:.0f}" fill="#10122A" {trait}/>')
            noeuds.append(f'  <circle class="activation anim" cx="{x:.0f}" cy="{y:.0f}" '
                          f'r="{r * 0.32:.0f}" fill="{"url(#gA)" if bord else ACCENT}" '
                          f'style="animation-delay:{onde + i * 450}ms"/>')
        parts.append(_parait("\n".join(noeuds), instants[i]))
    return _document(w, h, "\n".join(parts))


# ── cycle ────────────────────────────────────────────────────────────────────

def schema_cycle(donnees: dict, w: int, h: int,
                 mots: "list[dict] | None" = None, duree_ms: int = 0) -> str:
    """
    Une boucle : nœuds sur un anneau elliptique, flèche tournante au centre.

    Pour un processus qui reprend sur lui-même. L'anneau se ferme au dernier
    nœud ; alors seulement des points y circulent dans le sens horaire et la
    lumière passe de nœud en nœud. La figure tourne, elle ne finit pas. Le
    premier nœud porte le dégradé : c'est là que la boucle commence.
    """
    items = [_item(it) for it in donnees.get("items", [])[:5]]
    n = len(items)
    if n < 2:
        return ""
    cw, ch = (300, 180) if n < 5 else (250, 170)
    cx, cy = ZONE_W / 2, ZONE_H / 2
    rx, ry = cx - cw / 2, cy - ch / 2
    instants = _instants(items, mots, duree_ms)
    ferme = _apres(instants[-1], 450)
    tour_ms = 1100
    depart_tour = (0 if ferme is None else ferme) + 600
    anneau = (f"M{cx - rx:.0f} {cy:.0f} A{rx:.0f} {ry:.0f} 0 1 1 {cx + rx:.0f} {cy:.0f} "
              f"A{rx:.0f} {ry:.0f} 0 1 1 {cx - rx:.0f} {cy:.0f}")

    parts = [_parait(f'  <path d="{anneau}" fill="none" stroke="url(#gA)" stroke-opacity=".35" '
                     f'stroke-width="4"/>', instants[0], "fondu")]
    flux = [f'  <path class="coule anim" d="{anneau}" fill="none" stroke="{ACCENT}" '
            f'stroke-width="7" stroke-linecap="round" stroke-dasharray="0 {PAS_FLUX}"/>']
    for i in range(n):
        # Chevron à mi-chemin entre deux nœuds, tangent à l'anneau.
        t = math.radians(-90 + (i + 0.5) * 360 / n)
        x, y = cx + rx * math.cos(t), cy + ry * math.sin(t)
        angle = math.degrees(math.atan2(ry * math.cos(t), -rx * math.sin(t)))
        flux.append(f'  <path d="M-12 -15 L8 0 L-12 15" fill="none" stroke="{VIOLET_CLAIR}" '
                    f'stroke-width="5" stroke-linecap="round" stroke-linejoin="round" '
                    f'transform="translate({x:.1f} {y:.1f}) rotate({angle:.1f})"/>')
    parts.append(_parait("\n".join(flux), ferme, "fondu"))

    rayon = 74
    a0, a1 = math.radians(-75), math.radians(225)
    centre = (
        f'  <circle cx="{cx:.0f}" cy="{cy:.0f}" r="110" fill="url(#halo)"/>\n'
        f'  <g class="pivote anim" style="transform-origin:{cx:.0f}px {cy:.0f}px">'
        f'<path d="M{cx + rayon * math.cos(a0):.1f} {cy + rayon * math.sin(a0):.1f} '
        f'A{rayon} {rayon} 0 1 1 {cx + rayon * math.cos(a1):.1f} {cy + rayon * math.sin(a1):.1f}" '
        f'fill="none" stroke="url(#gA)" stroke-width="10" stroke-linecap="round" '
        f'marker-end="url(#fl-a)"/></g>'
    )
    parts.append(_parait(centre, ferme))

    for i, (texte, icone) in enumerate(items):
        t = math.radians(-90 + i * 360 / n)
        x, y = cx + rx * math.cos(t) - cw / 2, cy + ry * math.sin(t) - ch / 2
        noeud = [
            _carte(x, y, cw, ch, opaque=True, rx=32),
            _lueur(x, y, cw, ch, depart_tour + i * tour_ms, classe="tour",
                   style=f";animation-duration:{n * tour_ms}ms", rx=32),
            _pastille(x + cw / 2, y + 56, 38, accent=i == 0, icone=icone,
                      texte=None if icone else i + 1),
            _bloc_texte(texte, x + cw / 2, y + 132, cw - 28, TEXTE, plafond=40, plancher=26,
                        max_lignes=2, poids=700),
        ]
        parts.append(_parait("\n".join(noeud), instants[i]))
    return _document(w, h, "\n".join(parts))


# ── matrice ──────────────────────────────────────────────────────────────────

def schema_matrice(donnees: dict, w: int, h: int,
                   mots: "list[dict] | None" = None, duree_ms: int = 0) -> str:
    """
    Une grille 2x2 sur deux axes indépendants.

    Quadrants dans l'ordre des données : haut-droite (les deux axes au maximum),
    haut-gauche, bas-droite, bas-gauche. Le premier porte l'accent, le dernier
    s'efface un peu. Les axes se tracent d'abord, puis chaque quadrant paraît
    quand la voix le nomme.
    """
    quadrants = [_item(q) for q in donnees.get("quadrants", [])[:4]]
    if len(quadrants) < 4:
        return ""
    axe_x, axe_y = donnees.get("axe_x", ""), donnees.get("axe_y", "")
    instants = _instants(quadrants, mots, duree_ms)
    depart_axes = _apres(instants[0], -700)

    def axe(d: str, longueur: int) -> str:
        trace = ("" if depart_axes is None else
                 f' class="trace anim" style="--long:{longueur};animation-delay:{depart_axes}ms"')
        return (f'  <path d="{d}" fill="none" stroke="{TEXTE_DOUX}" stroke-width="3" '
                f'stroke-linecap="round" marker-end="url(#fl)"{trace}/>')

    parts = [axe("M50 726 V14", 712), axe("M80 762 H822", 742)]
    libelles = []
    if axe_y:
        # Le rotate() est posé sur le texte et l'animation sur le groupe : la
        # transform-origin d'une animation CSS déplacerait le centre du rotate().
        libelles.append(f'  <text x="22" y="360" text-anchor="middle" transform="rotate(-90 22 360)" '
                        f'fill="{TEXTE_DOUX}" font-size="26" font-weight="700" letter-spacing="5" '
                        f'font-family="{POLICE_SVG}">{_echapper(str(axe_y).upper())}</text>')
    if axe_x:
        libelles.append(_kicker(axe_x, 451, 800, ancre="middle"))
    parts.append(_parait("\n".join(libelles), _apres(depart_axes, 500), "fondu"))

    cases = [(468, 0), (80, 0), (468, 370), (80, 370)]
    qw, qh = 368, 350
    for i, ((texte, icone), (x, y)) in enumerate(zip(quadrants, cases)):
        accent = i == 0
        case = [_carte(x, y, qw, qh, accent=accent)]
        if icone:
            case.append(_pastille(x + qw / 2, y + 118, 48, accent=accent, icone=icone))
            ty = y + 236
        else:
            ty = y + qh / 2
        case.append(_bloc_texte(texte, x + qw / 2, ty, qw - 48, TEXTE, plafond=46,
                                plancher=28, max_lignes=3, poids=700))
        corps = "\n".join(case)
        if i == 3:
            corps = f'  <g opacity=".75">\n{corps}\n  </g>'
        parts.append(_parait(corps, instants[i]))
    return _document(w, h, "\n".join(parts))


# ── frise ────────────────────────────────────────────────────────────────────

def schema_frise(donnees: dict, w: int, h: int,
                 mots: "list[dict] | None" = None, duree_ms: int = 0) -> str:
    """
    Une chronologie verticale : jalons datés le long d'une colonne.

    Verticale sur un cadre 9:16 : quatre dates en largeur laisseraient 240 px
    par libellé. Le temps s'écoule le long de la colonne (points vers le bas) ;
    chaque jalon porte son année en filigrane, et le plus récent émet une onde,
    il est « maintenant ». Sans date, c'est `etapes`.
    """
    bruts = donnees.get("items", [])[:5]
    n = len(bruts)
    if not n:
        return ""
    jalons = [(*_item(it), str(it.get("date", "")) if isinstance(it, dict) else "")
              for it in bruts]
    bloc, ecart, y0 = _repartir(ZONE_H, n, hauteur_max=190, ratio_ecart=0.39)
    instants = _instants(bruts, mots, duree_ms)
    a_icones = any(icone for _, icone, _ in jalons)
    xc, xg = 52, 132
    r = 46 if a_icones else 18
    centres = [y0 + i * (bloc + ecart) + bloc / 2 for i in range(n)]

    parts = []
    if n > 1:
        d = f"M{xc} {centres[0]:.0f} V{centres[-1]:.0f}"
        parts.append(_parait(
            f'  <path d="{d}" fill="none" stroke="url(#gV)" stroke-opacity=".35" stroke-width="4" '
            f'stroke-linecap="round"/>\n'
            f'  <path class="coule anim" d="{d}" fill="none" stroke="{ACCENT}" stroke-width="7" '
            f'stroke-linecap="round" stroke-dasharray="0 {PAS_FLUX}"/>',
            _apres(instants[0], -100), "fondu"))

    for i, (texte, icone, date) in enumerate(jalons):
        y, cy = centres[i] - bloc / 2, centres[i]
        dernier = i == n - 1
        carte = [_carte(xg, y, ZONE_W - xg, bloc, accent=dernier)]
        if date:
            carte.append(f'  <text x="{ZONE_W - 26}" y="{cy + 60:.0f}" text-anchor="end" fill="none" '
                         f'stroke="#FFFFFF" stroke-opacity=".07" stroke-width="2" font-size="170" '
                         f'font-weight="700" letter-spacing="-6" font-family="{POLICE_TITRE}">'
                         f'{_echapper(date)}</text>')
            carte.append(f'  <text x="{xg + 40}" y="{cy - 14:.0f}" '
                         f'fill="{"url(#gA)" if dernier else ACCENT}" font-size="46" '
                         f'font-weight="700" font-family="{POLICE_TITRE}">{_echapper(date)}</text>')
        carte.append(_bloc_texte(texte, xg + 40, cy + 38 if date else cy, ZONE_W - xg - 80, TEXTE,
                                 plafond=50, plancher=30, max_lignes=1 if date else 2,
                                 ancre="start", poids=700))
        parts.append(_parait("\n".join(carte), instants[i], "gauche"))
        if a_icones:
            jalon = _pastille(xc, cy, r, accent=dernier, icone=icone)
        else:
            jalon = (f'  <circle cx="{xc}" cy="{cy:.0f}" r="{r}" '
                     + (f'fill="url(#gA)" style="{HALO}"/>' if dernier
                        else f'fill="#10122A" stroke="{ACCENT}" stroke-width="4"/>'))
        if dernier:
            jalon += (f'\n  <circle class="onde anim" cx="{xc}" cy="{cy:.0f}" r="{r}" fill="none" '
                      f'stroke="{ACCENT}" stroke-width="3" '
                      f'style="animation-delay:{(instants[i] or 0) + 400}ms"/>')
        parts.append(_parait(jalon, _apres(instants[i], -80)))
    return _document(w, h, "\n".join(parts))


# ── embranchement ────────────────────────────────────────────────────────────

def schema_embranchement(donnees: dict, w: int, h: int,
                         mots: "list[dict] | None" = None, duree_ms: int = 0) -> str:
    """
    Une question fermée et ses issues (2 ou 3).

    La question porte l'accent : c'est elle qui tient la figure. Les liaisons
    se tracent vers chaque issue et y font couler des points ; la lumière
    alterne d'une issue à l'autre — l'une *ou* l'autre. Au-delà de trois
    branches, les cartes seraient trop étroites pour porter un libellé.
    """
    question = donnees.get("question", "")
    branches = [_item(b) for b in donnees.get("branches", [])[:3]]
    n = len(branches)
    if not question or n < 2:
        return ""
    instants = _instants([question] + [t for t, _ in branches], mots, duree_ms)
    ecart = 30
    bw = (ZONE_W - (n - 1) * ecart) / n
    yq, hq, yb, hb = 40, 196, 424, 310
    alternance = 1600
    depart_tour = (0 if instants[-1] is None else instants[-1]) + 900

    tete = [
        _carte(0, yq, ZONE_W, hq, accent=True),
        _pastille(86, yq + hq / 2, 50, accent=True, texte="?"),
        _bloc_texte(question, 170, yq + hq / 2, ZONE_W - 170 - 44, TEXTE, plafond=50,
                    plancher=30, max_lignes=2, ancre="start", poids=700),
    ]
    parts = [_parait("\n".join(tete), instants[0])]
    for i, (texte, icone) in enumerate(branches):
        x = i * (bw + ecart)
        cxb = x + bw / 2
        parts.append(_liaison(f"M418 {yq + hq} C418 330 {cxb:.0f} 330 {cxb:.0f} {yb}",
                              _apres(instants[i + 1], -300)))
        issue = [
            _carte(x, yb, bw, hb),
            _lueur(x, yb, bw, hb, depart_tour + i * alternance, classe="tour",
                   style=f";animation-duration:{n * alternance}ms"),
        ]
        if icone:
            issue.append(_pastille(cxb, yb + 104, 56, icone=icone))
        issue.append(_bloc_texte(texte, cxb, yb + (226 if icone else hb / 2), bw - 52, TEXTE,
                                 plafond=48, plancher=28, max_lignes=2, poids=700))
        parts.append(_parait("\n".join(issue), instants[i + 1]))
    return _document(w, h, "\n".join(parts))


# ── venn ─────────────────────────────────────────────────────────────────────

def schema_venn(donnees: dict, w: int, h: int,
                mots: "list[dict] | None" = None, duree_ms: int = 0) -> str:
    """
    Deux ensembles et ce qu'ils partagent.

    Les cercles sont les prémisses, teintés chacun de sa couleur ; le
    recouvrement est la conclusion, seul à porter le dégradé et à respirer. Les
    deux ensembles glissent l'un vers l'autre avant que la zone commune ne
    s'allume. Son libellé est en noir : il est posé sur le dégradé.
    """
    gauche, droite = donnees.get("gauche", ""), donnees.get("droite", "")
    commun = donnees.get("commun", "")
    if not gauche or not droite:
        return ""
    instants = _instants([gauche, droite, commun or gauche], mots, duree_ms)
    cy, r = 400, 250
    centres = (268, 568)            # écartés de 1,2 r : la lentille porte un libellé
    teintes = (("url(#venn-g)", ACCENT, ".6"), ("url(#venn-d)", VIOLET_CLAIR, ".65"))
    lib_x = (163, 673)

    parts = []
    for i, (cx, (fond, trait, opacite), dx) in enumerate(zip(centres, teintes, (-130, 130))):
        cercle = (f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="{fond}" stroke="{trait}" '
                  f'stroke-opacity="{opacite}" stroke-width="3" style="mix-blend-mode:screen"/>')
        if instants[i] is not None:
            cercle = (f'  <g class="glisse anim" style="--dx:{dx}px;animation-delay:{instants[i]}ms">'
                      f'\n{cercle}\n  </g>')
        parts.append(cercle)
    if commun:
        lentille = f"M418 200 A{r} {r} 0 0 1 418 600 A{r} {r} 0 0 1 418 200 Z"
        parts.append(_parait(
            f'  <path class="respire anim" d="{lentille}" fill="url(#gA)" filter="url(#flou)"/>\n'
            f'  <path class="commun" d="{lentille}" fill="url(#gA)" fill-opacity=".92"/>\n'
            + _bloc_texte(commun, 418, cy, 170, NOIR, plafond=34, plancher=22,
                          max_lignes=3, poids=800),
            instants[2]))
    for i, texte in enumerate((gauche, droite)):
        parts.append(_parait(_bloc_texte(texte, lib_x[i], cy, 250, TEXTE, plafond=40,
                                         plancher=26, max_lignes=3, poids=700),
                             _apres(instants[i], 300), "fondu"))
    return _document(w, h, "\n".join(parts))


SCHEMAS = {
    "stat": schema_stat, "etapes": schema_etapes, "couches": schema_couches,
    "comparaison": schema_comparaison, "liste": schema_liste, "neurones": schema_neurones,
    "cycle": schema_cycle, "matrice": schema_matrice, "frise": schema_frise,
    "embranchement": schema_embranchement, "venn": schema_venn,
}
