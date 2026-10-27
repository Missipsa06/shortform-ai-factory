"""
Figures du registre « dev » : un terminal qui se tape, et le récapitulatif final.

Deux figures pour les sujets d'outils (installation, ligne de commande, API),
sur le modèle des vidéos TikTok de développeurs : la commande se tape sous les
yeux du spectateur, et la vidéo se clôt sur le réflexe à retenir.

Contrairement aux schémas de `content/slides.py`, ces figures sont du **HTML et
non du SVG** : SVG ne mesure pas le texte, alors qu'un terminal *est* du texte.
La police mono rend la frappe exacte — une largeur en caractères révélée par `steps()`
montre un caractère par pas, sans mesure dans le navigateur.

Les fragments sont dimensionnés en relatif (largeur du conteneur, unités de
conteneur `cqw`/`cqh`) : `.graphic` est élastique, une taille en pixels fixes y
débordait de la colonne.

`slides.py` importe ce module à l'intérieur de `_graphic_svg` ; les imports de
`content.slides` se font donc ici dans les fonctions, jamais au niveau module,
pour éviter l'import circulaire.
"""

from __future__ import annotations

import logging
import re
import textwrap
from html import escape
from pathlib import Path

logger = logging.getLogger(__name__)

POLICE_MONO = Path(__file__).parent / "assets" / "fonts" / "JetBrainsMono.ttf"

# Fenêtre pendant laquelle les nouvelles lignes paraissent : pas avant que la
# zone graphique soit entrée, et tout posé avant la sortie de slide.
DEBUT_MS = 400
MARGE_FIN_MS = 600
# Vitesse de frappe nominale : réduite si la slide est trop courte pour tout taper.
MS_PAR_CARACTERE = 45
# L'invite et le curseur se voient seuls un instant avant la première touche.
AVANT_FRAPPE_MS = 250
# Une sortie suit sa commande, une sortie suit la précédente.
APRES_CMD_MS = 300
ENTRE_SORTIES_MS = 220
# Le curseur reste un instant au bout de la commande tapée, puis s'efface.
CURSEUR_RESTE_MS = 350
# Écart minimal entre deux lignes du récapitulatif.
ECART_RECAP_MS = 250

# Une glyphe de JetBrains Mono avance de 0,6 em. Le corps se déduit d'un nombre
# de colonnes FIXE et non de la ligne la plus longue : un `curl … | sh` de 47
# caractères ramenait tout le texte à ~18 px, illisible sur un téléphone.
# Au-delà, la ligne revient à la ligne, indentée, et la frappe continue
# dessous. 38 colonnes donnent ~38 px sur une fenêtre de 955 px.
AVANCE_MONO = 0.6
COLONNES = 38
CORPS_MAX_PX = 40
INDENT = "  "
INTERLIGNE = 1.6

# Mots trop banals pour dater une commande dans la narration.
_JETONS_IGNORES = {"sudo", "http", "https", "www", "com", "org", "the"}

_CSS = """
@font-face{font-family:'Mono';src:url('__MONO__') format('truetype');font-weight:100 800}
/* Longhands obligatoires : la forme raccourcie `animation:` réinitialiserait
   l'état de pause et l'animation échapperait au pilotage par seek(). */
.dev-terminal .anim,.dev-recap .anim{animation-play-state:paused;animation-fill-mode:both}
/* Hauteur = contenu final : les lignes encore invisibles occupent déjà leur
   place, la fenêtre ne bouge donc pas pendant la slide. */
.dev-terminal{container-type:inline-size;width:100%;max-height:100%;
  align-self:flex-start;display:flex;flex-direction:column;overflow:hidden;
  background:rgba(4,9,20,.88);border:2px solid rgba(64,190,214,.38);border-radius:22px;
  box-shadow:0 0 48px rgba(0,200,255,.07);font-family:'Mono',monospace}
.dev-barre{flex:none;height:8cqw;display:flex;align-items:center;gap:2.2cqw;padding:0 3.4cqw;
  border-bottom:2px solid rgba(64,190,214,.18);font-size:clamp(14px,2.7cqw,30px)}
.dev-pastilles{display:flex;gap:1.3cqw}
.dev-pastilles i{display:block;width:1.9cqw;height:1.9cqw;border-radius:50%}
.dev-pastilles i:nth-child(1){background:#FF5F57}
.dev-pastilles i:nth-child(2){background:#FEBC2E}
.dev-pastilles i:nth-child(3){background:#28C840}
.dev-chemin{color:#8B95A7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dev-etiquette{margin-left:auto;color:#3FA2B5;font-size:.78em;letter-spacing:.14em;
  text-transform:uppercase}
.dev-corps{min-height:0;padding:3.6cqw 3.4cqw calc(3.6cqw + .9em);line-height:__INTERLIGNE__;
  font-size:min(__CORPS_MAX__px,calc(92cqw / (__COLONNES__ * __AVANCE__)))}
.dev-ligne{white-space:pre;overflow:hidden}
.dev-invite{color:#00D4FF}
.dev-cmd{color:#EEF2F7}
.dev-out{color:#9AA3B2}
.dev-ok{color:#5EE08A}
/* Frappe : la largeur, en caractères, est révélée un caractère par pas. */
.dev-frappe{display:inline-block;vertical-align:top;overflow:hidden;white-space:pre;
  animation-name:dev-taper}
@keyframes dev-taper{from{width:0}}
/* Curseur : visible seulement entre ses deux instants. Deux animations d'1 ms,
   et non une seule en `fill-mode:none` : une animation finie sans remplissage
   sort de `document.getAnimations()`, et `seek()` ne peut plus revenir en
   arrière dessus — le curseur ne se rallumait plus. `dev-eteindre` n'a pas de
   `from` : avant son instant, elle laisse passer la valeur de `dev-allumer`.
   Une commande repliée a un curseur par rangée, chacun allumé pendant sa
   propre frappe. */
.dev-curseur{display:inline-block;width:.6em;height:1.25em;vertical-align:-.25em;
  background:#00D4FF;animation-name:dev-allumer,dev-eteindre;
  animation-duration:1ms,1ms;animation-timing-function:steps(1,end),steps(1,end)}
@keyframes dev-allumer{from{opacity:0}to{opacity:1}}
@keyframes dev-eteindre{to{opacity:0}}
.dev-montre{animation-name:dev-montrer;animation-timing-function:steps(1,end)}
.dev-fondu{animation-name:dev-montrer;animation-duration:160ms;animation-timing-function:linear}
@keyframes dev-montrer{from{opacity:0}to{opacity:1}}
/* Rangée ~110 px et code ~40 px : lisibles en défilement, comme le terminal. */
.dev-recap{container-type:inline-size;width:100%;align-self:flex-start;
  display:flex;flex-direction:column;gap:clamp(18px,3cqw,28px)}
.dev-item{display:flex;align-items:center;gap:clamp(18px,3cqw,30px);
  min-height:clamp(100px,13cqw,124px);padding:clamp(16px,2.4cqw,24px) clamp(22px,3.6cqw,36px);
  background:rgba(8,16,32,.55);border:2px solid rgba(64,190,214,.30);border-radius:20px}
.dev-num{flex:none;display:flex;align-items:center;justify-content:center;
  width:clamp(48px,7cqw,64px);height:clamp(48px,7cqw,64px);border-radius:50%;
  background:#00D4FF;color:#06121C;font-family:'Lecture',sans-serif;font-weight:800;
  font-size:clamp(26px,3.8cqw,34px)}
.dev-code{min-width:0;overflow-wrap:anywhere;color:#67E3EE;font-family:'Mono',monospace;
  font-weight:600;font-size:clamp(34px,4.8cqw,44px)}
.dev-texte{margin-left:auto;flex:none;max-width:45%;text-align:right;color:#A8B0C0;
  font-family:'Lecture',sans-serif;font-weight:600;font-size:clamp(30px,4.2cqw,38px)}
.dev-entre{animation-name:dev-entrer;animation-duration:520ms;
  animation-timing-function:cubic-bezier(.2,.8,.2,1)}
@keyframes dev-entrer{from{opacity:0;transform:translateY(24px)}to{opacity:1;transform:none}}
"""


def _style() -> str:
    """Feuille de style des deux figures, police mono par chemin absolu local."""
    return "<style>" + (
        _CSS.replace("__MONO__", POLICE_MONO.resolve().as_uri())
            .replace("__INTERLIGNE__", str(INTERLIGNE))
            .replace("__AVANCE__", str(AVANCE_MONO))
            .replace("__COLONNES__", str(COLONNES))
            .replace("__CORPS_MAX__", str(CORPS_MAX_PX))
    ) + "</style>"


def _instant_mot(texte: str, mots: list[dict] | None, apres_ms: int = 0) -> int | None:
    """
    Premier instant, en ms, où la narration prononce un mot de `texte`.

    Comparaison sur la forme pliée (sans casse ni accents) : « pull » retrouve
    « Pull, » dans le texte lu. Seuls les mots d'au moins trois lettres comptent,
    hors jetons banals d'une commande (`https`, `sudo`…). Rien avant `apres_ms` :
    une ligne ne peut pas se caler avant la précédente.
    """
    from content.slides import _plier

    if not mots:
        return None
    jetons = {_plier(j) for j in re.split(r"[^\w]+", str(texte))}
    jetons = {j for j in jetons if len(j) >= 3 and j not in _JETONS_IGNORES}
    for mot in mots:
        debut = mot.get("debut")
        if debut is None:
            continue
        instant = int(float(debut) * 1000)
        if instant >= apres_ms and _plier(mot.get("mot", "")) in jetons:
            return instant
    return None


def _lignes_terminal(donnees: dict) -> list[tuple[str, str]]:
    """Lignes du terminal en (genre, texte), genre parmi cmd, out, ok."""
    lignes = []
    for ligne in donnees.get("lignes") or []:
        if isinstance(ligne, str):
            lignes.append(("out", ligne))
            continue
        genre = next((g for g in ("cmd", "out", "ok") if g in (ligne or {})), None)
        if genre is None:
            logger.warning("Ligne de terminal ignorée (ni cmd, ni out, ni ok) : %r", ligne)
            continue
        lignes.append((genre, str(ligne[genre])))
    return lignes


def _besoin_ms(genre: str, texte: str, echelle: float = 1.0) -> float:
    """Temps qu'occupe une ligne avant que la suivante puisse paraître."""
    if genre == "cmd":
        return echelle * (AVANT_FRAPPE_MS + len(texte) * MS_PAR_CARACTERE + APRES_CMD_MS)
    return echelle * ENTRE_SORTIES_MS


def _instants_terminal(
    lignes: list[tuple[str, str]], depuis: int,
    mots: list[dict] | None, duree_ms: int,
) -> tuple[list[int | None], float]:
    """
    Instant d'apparition de chaque ligne, et échelle de temps retenue.

    L'échelle vaut 1, ou moins si la slide est trop courte pour tout taper à
    vitesse nominale : frappe et enchaînements accélèrent ensemble, plutôt que
    de couper la dernière ligne.

    Les lignes d'indice < `depuis` sont déjà là (None). Les suivantes se
    succèdent entre `DEBUT_MS` et la sortie de slide moins `MARGE_FIN_MS`. Le
    temps libre est réparti avant chaque commande ; une commande dont un mot est
    prononcé (« pull », « run ») commence à se taper sur ce mot. Une sortie suit
    toujours sa commande, sans datation propre : elle en est la conséquence.
    """
    from content.slides import SORTIE_MS

    instants: list[int | None] = [None] * len(lignes)
    nouvelles = range(depuis, len(lignes))
    fin = max(DEBUT_MS, duree_ms - SORTIE_MS - MARGE_FIN_MS)
    budget = fin - DEBUT_MS

    besoin = sum(_besoin_ms(*lignes[i]) for i in nouvelles)
    echelle = budget / besoin if besoin > budget else 1.0
    n_cmd = sum(1 for i in nouvelles if lignes[i][0] == "cmd")
    jeu = max(0.0, budget - besoin * echelle) / (n_cmd + 1)

    t = float(DEBUT_MS)
    for i in nouvelles:
        genre, texte = lignes[i]
        if genre != "cmd":
            instants[i] = int(t)
            t += _besoin_ms(genre, texte, echelle)
            continue
        reste = sum(_besoin_ms(*lignes[j], echelle) for j in range(i, len(lignes)))
        depart = t + jeu
        ancre = _instant_mot(texte, mots, apres_ms=int(t))
        if ancre is not None:
            # La première touche tombe sur le mot prononcé.
            depart = ancre - AVANT_FRAPPE_MS * echelle
        depart = max(t, min(depart, fin - reste))
        instants[i] = int(depart)
        t = depart + _besoin_ms(genre, texte, echelle)
    return instants, echelle


def _replier(texte: str, largeur: int) -> list[str]:
    """
    Découpe une ligne en rangées d'au plus `largeur` caractères.

    Coupe aux espaces, et en plein mot seulement si un mot dépasse à lui seul :
    une URL n'a pas d'espace. Les tirets ne coupent pas, `-fsSL` reste entier.
    """
    return textwrap.wrap(texte, width=max(1, largeur), break_on_hyphens=False) or [""]


def _ligne_cmd(texte: str, instant: int | None, echelle: float) -> str:
    """
    Une commande : invite « $ », texte replié sur `COLONNES`, frappe et curseur.

    Chaque rangée a sa propre frappe, qui commence quand la précédente finit :
    la commande se tape d'un trait, en passant à la ligne comme un vrai terminal.
    Les rangées de continuation sont indentées sous le début de la commande.
    """
    invite = '<span class="dev-invite">$ </span>'
    # Une colonne réservée au curseur, au bout de chaque rangée.
    rangees = _replier(texte, COLONNES - len(INDENT) - 1)
    if instant is None:
        corps = ("\n" + INDENT).join(escape(r) for r in rangees)
        return f'<div class="dev-ligne dev-cmd">{invite}{corps}</div>'

    frappe = instant + int(AVANT_FRAPPE_MS * echelle)
    morceaux = []
    for k, rangee in enumerate(rangees):
        duree = max(1, int(len(rangee) * MS_PAR_CARACTERE * echelle))
        # Le curseur de la première rangée paraît avec l'invite, avant la
        # première touche ; les suivants avec leur propre frappe.
        allume = instant if k == 0 else frappe
        reste = int(CURSEUR_RESTE_MS * echelle) if k == len(rangees) - 1 else 0
        eteint = frappe + duree + reste
        morceaux.append(
            # En em et non en `ch` : Chromium résout `ch` sur la police de repli
            # (Consolas, 0,55 em) et non sur JetBrains Mono (0,6 em) — mesuré,
            # chaque rangée tapée perdait son dernier caractère.
            f'<span class="dev-frappe anim" style="width:calc({len(rangee)} * {AVANCE_MONO}em);'
            f'animation-duration:{duree}ms;animation-delay:{frappe}ms;'
            f'animation-timing-function:steps({max(1, len(rangee))},end)">{escape(rangee)}</span>'
            f'<span class="dev-curseur anim" '
            f'style="animation-delay:{allume}ms,{eteint}ms"></span>'
        )
        frappe += duree
    return (
        f'<div class="dev-ligne dev-cmd dev-montre anim" '
        f'style="animation-duration:1ms;animation-delay:{instant}ms">{invite}'
        + ("\n" + INDENT).join(morceaux) + "</div>"
    )


def figure_terminal(
    donnees: dict, w: int, h: int,
    mots: list[dict] | None = None, duree_ms: int = 0,
) -> str:
    """
    Fenêtre de terminal dont les commandes se tapent pendant la narration.

    `donnees` : {"chemin": "~/projet", "lignes": [{"cmd"|"out"|"ok": "..."}],
    "depuis": k}. Les k premières lignes sont déjà affichées : c'est le
    terminal de la slide précédente, qui grandit d'une slide à l'autre. Les
    suivantes paraissent en séquence — `cmd` tapée caractère par caractère avec
    un curseur bloc, `out` et `ok` d'un coup après leur commande.

    Le corps du texte est fixé par `COLONNES` : une ligne plus longue revient à
    la ligne, indentée, au lieu de faire rétrécir tout le terminal. La fenêtre
    prend la hauteur de son contenu final. `w` et `h` sont ignorés : elle suit
    la largeur de `.graphic`.

    `duree_ms` nul (aperçu PNG) : état final, sans aucune animation.
    """
    lignes = _lignes_terminal(donnees)
    if not lignes:
        return ""
    try:
        depuis = int(donnees.get("depuis") or 0)
    except (TypeError, ValueError):
        depuis = 0
    depuis = min(max(depuis, 0), len(lignes))

    if duree_ms:
        instants, echelle = _instants_terminal(lignes, depuis, mots, duree_ms)
    else:
        instants, echelle = [None] * len(lignes), 1.0

    rendu = []
    for (genre, texte), instant in zip(lignes, instants):
        if genre == "cmd":
            rendu.append(_ligne_cmd(texte, instant, echelle))
            continue
        # Préfixe « ✓ » ou rien ; les rangées repliées s'indentent d'autant.
        prefixe = "✓ " if genre == "ok" else ""
        rangees = _replier(texte, COLONNES - len(INDENT))
        contenu = ("\n" + INDENT).join(escape(r) for r in rangees)
        anim = ("" if instant is None else
                f' dev-fondu anim" style="animation-delay:{instant}ms')
        rendu.append(f'<div class="dev-ligne dev-{genre}{anim}">{prefixe}{contenu}</div>')

    chemin = escape(str(donnees.get("chemin") or "~"))
    return (
        f'{_style()}<div class="dev-terminal">'
        f'<div class="dev-barre"><span class="dev-pastilles"><i></i><i></i><i></i></span>'
        f'<span class="dev-chemin">{chemin}</span>'
        f'<span class="dev-etiquette">Simulation</span></div>'
        f'<div class="dev-corps">{"".join(rendu)}</div></div>'
    )


def _instants_recap(items: list[dict], mots: list[dict] | None, duree_ms: int) -> list[int]:
    """
    Instant d'apparition de chaque ligne du récapitulatif.

    Une ligne paraît quand la voix prononce son `code` ou son `texte` ; sinon,
    à l'instant que `_cadence` lui donne au rythme de la parole. Croissant, et
    jamais deux lignes à moins de `ECART_RECAP_MS`.
    """
    from content.slides import SORTIE_MS, _cadence

    repli = _cadence([str(i.get("code", "")) for i in items], mots, duree_ms)
    fin = max(0, duree_ms - SORTIE_MS - MARGE_FIN_MS)
    instants: list[int] = []
    precedent = -ECART_RECAP_MS
    for item, defaut in zip(items, repli):
        apres = max(0, precedent)
        ancre = _instant_mot(item.get("code", ""), mots, apres)
        if ancre is None:
            ancre = _instant_mot(item.get("texte", ""), mots, apres)
        instant = max(defaut if ancre is None else ancre, precedent + ECART_RECAP_MS)
        instant = min(instant, fin)
        instants.append(instant)
        precedent = instant
    return instants


def figure_recap(
    donnees: dict, w: int, h: int,
    mots: list[dict] | None = None, duree_ms: int = 0,
) -> str:
    """
    Le réflexe à retenir : 2 à 4 lignes « code → ce qu'il fait ».

    Pastille numérotée cyan, `code` en police mono, `texte` aligné à droite.
    Chaque ligne paraît quand la voix nomme son code ou son effet. `w` et `h`
    sont ignorés : la liste suit la largeur de `.graphic`.
    """
    items = [i for i in (donnees.get("items") or []) if isinstance(i, dict)][:4]
    if not items:
        return ""
    instants = _instants_recap(items, mots, duree_ms) if duree_ms else [None] * len(items)
    rendu = []
    for n, (item, instant) in enumerate(zip(items, instants), start=1):
        anim = ("" if instant is None else
                f' dev-entre anim" style="animation-delay:{instant}ms')
        rendu.append(
            f'<div class="dev-item{anim}"><span class="dev-num">{n}</span>'
            f'<span class="dev-code">{escape(str(item.get("code", "")))}</span>'
            f'<span class="dev-texte">{escape(str(item.get("texte", "")))}</span></div>'
        )
    return f'{_style()}<div class="dev-recap">{"".join(rendu)}</div>'
