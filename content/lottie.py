"""
Import d'animations LottieFiles dans le dépôt, recolorées dans la charte.

**Pourquoi un outil plutôt qu'un téléchargement à la main.** Chaque import doit
faire les mêmes vérifications, et aucune ne se rattrape après coup :

- **La licence.** Seules les animations « Free to use under the Lottie Simple
  License » sont acceptées : usage commercial permis, attribution non exigée
  mais encouragée. Une animation modifiée — recolorée — est une œuvre dérivée,
  qui reste sous cette licence. Toute autre mention (Creative Commons, conditions
  propres à l'auteur) refuse l'import : elle demande une lecture humaine.
- **La compatibilité.** La version allégée de lottie-web embarquée n'exécute
  pas les expressions : une animation qui en contient serait faussée sans le
  moindre message. Les images bitmap sont refusées aussi — le rendu doit rester
  vectoriel et hors ligne.
- **La charte.** Une animation de banque arrive avec ses couleurs ; posée telle
  quelle à côté des figures, elle dépareille. Elle est recolorée : un seul accent
  cyan, traits clairs, aplats sombres — la règle des figures schématiques.
- **La traçabilité.** Auteur, page, licence et date vont dans `SOURCES.json`,
  comme l'attribution d'une figure arXiv : rien n'entre dans le dépôt sans dire
  d'où il vient.

Usage :
    python -m content.lottie --page https://lottiefiles.com/free-animation/... \\
        --nom loupe --sens "chercher, enquêter, examiner"
"""

import argparse
import io
import json
import logging
import re
import urllib.parse
import zipfile
from datetime import date
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

LOTTIE_DIR = Path(__file__).parent / "assets" / "lottie"
SOURCES = LOTTIE_DIR / "SOURCES.json"
LICENCE_ACCEPTEE = "Lottie Simple License"
UA = {"User-Agent": "Mozilla/5.0"}

# Charte des figures (content/slides.py), en composantes Lottie de 0 à 1.
ACCENT = (0.0, 0.831, 1.0)        # #00D4FF — le seul accent
TEXTE = (0.918, 0.933, 0.969)     # #EAEEF7 — traits sombres d'origine, éclaircis
APLAT = (0.118, 0.141, 0.251)     # #1E2440 — aplats clairs d'origine, assombris

# Au-delà de cet écart entre composantes, une couleur est « colorée » et devient
# l'accent ; en deçà, elle est un gris, éclairci ou assombri selon sa luminance.
SEUIL_CHROMA = 0.25
SEUIL_LUMINANCE = 0.5


class ImportRefuse(ValueError):
    """L'animation ne peut pas entrer dans le dépôt, et le message dit pourquoi."""


# ── Recoloration ──────────────────────────────────────────────────────────────

def vers_charte(r: float, g: float, b: float) -> tuple[float, float, float]:
    """
    Couleur de la charte qui remplace une couleur d'origine.

    Une illustration de banque est presque toujours dessinée pour un fond clair :
    traits sombres, aplats blancs. Sur le fond nuit des slides, on inverse le
    rapport — traits clairs, aplats sombres — et toute couleur vive devient le
    seul accent, comme dans les figures.
    """
    if max(r, g, b) - min(r, g, b) > SEUIL_CHROMA:
        return ACCENT
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return TEXTE if luminance < SEUIL_LUMINANCE else APLAT


def _normaliser(composantes: list) -> list[float]:
    """Ramène une couleur à des composantes de 0 à 1 (d'anciens exports en 0-255)."""
    return [c / 255 for c in composantes] if max(composantes[:3]) > 1 else composantes


def _recolorer_valeur(valeur: list) -> list:
    """[r, g, b(, a)] → même forme, couleur de la charte, alpha conservé."""
    rgb = vers_charte(*_normaliser(valeur)[:3])
    return [*rgb, *valeur[3:]]


def _recolorer_propriete(prop: dict) -> None:
    """Propriété de couleur, fixe ou animée."""
    if prop.get("a") == 1:
        for cle in prop.get("k", []):
            for champ in ("s", "e"):
                if isinstance(cle.get(champ), list) and len(cle[champ]) >= 3:
                    cle[champ] = _recolorer_valeur(cle[champ])
    elif isinstance(prop.get("k"), list) and len(prop["k"]) >= 3:
        prop["k"] = _recolorer_valeur(prop["k"])


def _recolorer_degrade(g: dict) -> None:
    """Dégradé fixe : [pos, r, g, b] × n, suivi des arrêts d'opacité, intacts."""
    n = g.get("p", 0)
    k = g.get("k", {})
    if k.get("a") == 1 or not isinstance(k.get("k"), list):
        return  # dégradé animé : laissé tel quel, rare dans les icônes
    valeurs = k["k"]
    for i in range(n):
        debut = i * 4 + 1
        valeurs[debut:debut + 3] = _recolorer_valeur(valeurs[debut:debut + 3])


def recolorer(noeud: object) -> None:
    """Parcourt l'animation et ramène chaque couleur dans la charte, en place."""
    if isinstance(noeud, dict):
        if noeud.get("ty") in ("fl", "st") and isinstance(noeud.get("c"), dict):
            _recolorer_propriete(noeud["c"])
        if noeud.get("ty") in ("gf", "gs") and isinstance(noeud.get("g"), dict):
            _recolorer_degrade(noeud["g"])
        for valeur in noeud.values():
            recolorer(valeur)
    elif isinstance(noeud, list):
        for valeur in noeud:
            recolorer(valeur)


def sans_aplats_de_fond(animation: dict) -> None:
    """
    Retire les calques « solide » (ty 1) : ce sont presque toujours les fonds
    blancs prévus pour une page claire, qui masqueraient la slide.
    """
    animation["layers"] = [c for c in animation.get("layers", []) if c.get("ty") != 1]


# ── Contrôles ─────────────────────────────────────────────────────────────────

def _a_des_expressions(noeud: object) -> bool:
    """Une expression est une propriété dont la clé « x » porte du code."""
    if isinstance(noeud, dict):
        if isinstance(noeud.get("x"), str):
            return True
        return any(_a_des_expressions(v) for v in noeud.values())
    if isinstance(noeud, list):
        return any(_a_des_expressions(v) for v in noeud)
    return False


def controler(animation: dict) -> None:
    """
    Refuse ce que le rendu ne saurait pas jouer fidèlement.

    Raises:
        ImportRefuse: images bitmap ou expressions
    """
    if any("p" in a and ("u" in a or "e" in a) for a in animation.get("assets", [])):
        raise ImportRefuse("l'animation contient des images bitmap ; le rendu "
                           "doit rester vectoriel")
    if _a_des_expressions(animation):
        raise ImportRefuse("l'animation contient des expressions, que la version "
                           "allégée de lottie-web n'exécute pas : elle serait "
                           "faussée sans message")


# ── Import ────────────────────────────────────────────────────────────────────

def _lire_page(url_page: str) -> dict:
    """URL du fichier, titre, auteur et licence, lus sur la page de l'animation."""
    page = requests.get(url_page, timeout=30, headers=UA)
    page.raise_for_status()
    html = page.text

    if f"under the {LICENCE_ACCEPTEE}" not in html:
        raise ImportRefuse(f"la page n'indique pas « {LICENCE_ACCEPTEE} » : licence "
                           "à lire avant tout import")

    titre = auteur = apercu = ""
    for bloc in re.findall(r'<script type="application/ld\+json">(.+?)</script>', html, re.S):
        try:
            graphe = json.loads(bloc).get("@graph", [])
        except json.JSONDecodeError:
            continue
        for element in graphe:
            entite = element.get("mainEntity") or {}
            if entite.get("creator"):
                auteur = entite["creator"].get("name", "")
                titre = entite.get("name", "")
                apercu = entite.get("contentUrl", "")

    # **Le premier fichier de la page n'est pas forcément le bon.** Une page
    # porte aussi les fichiers d'animations mises en avant : trois imports du
    # 25/10/2026 ont ramené le même dessin isométrique sous trois noms. Le bon
    # fichier est rangé dans le même dossier que l'aperçu de l'animation
    # principale, désigné sans ambiguïté par le JSON-LD.
    dossier = re.search(r"/a/([0-9a-f-]{36})/", apercu)
    fichiers = [urllib.parse.unquote(u)
                for u in re.findall(r"lottieUrl=([^&\"'\\]+)", html)]
    retenus = [f for f in fichiers if dossier and dossier.group(1) in f]
    if not retenus:
        raise ImportRefuse("fichier de l'animation introuvable : la page ne porte "
                           "que ceux d'autres animations")
    return {"fichier": retenus[0], "titre": titre, "auteur": auteur}


def _extraire(contenu: bytes) -> dict:
    """JSON de l'animation, depuis un .lottie (archive) ou un JSON nu."""
    if contenu[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
            nom = next(n for n in archive.namelist()
                       if n.startswith("animations/") and n.endswith(".json"))
            return json.loads(archive.read(nom))
    return json.loads(contenu)


def importer(url_page: str, nom: str, sens: str, boucle: bool = True) -> Path:
    """
    Importe une animation LottieFiles, recolorée, et l'inscrit au manifeste.

    Args:
        url_page: Page de l'animation sur lottiefiles.com
        nom:      Nom court, en minuscules : celui que le LLM désignera
        sens:     Ce que l'animation évoque, pour guider le choix du LLM
        boucle:   Rejouer l'animation tant que la slide dure. Vrai par défaut :
                  les animations de banque sont presque toutes des boucles, dont
                  la dernière image ressemble à la première — souvent vide.

    Returns:
        Le fichier écrit dans content/assets/lottie/

    Raises:
        ImportRefuse: licence non acceptée, images, expressions
    """
    if not re.fullmatch(r"[a-z][a-z0-9_]*", nom):
        raise ValueError(f"Nom invalide : '{nom}' (minuscules, chiffres, _)")
    infos = _lire_page(url_page)
    reponse = requests.get(infos["fichier"], timeout=60, headers=UA)
    reponse.raise_for_status()
    animation = _extraire(reponse.content)

    controler(animation)
    sans_aplats_de_fond(animation)
    recolorer(animation)

    cible = LOTTIE_DIR / f"{nom}.json"
    cible.write_text(json.dumps(animation, separators=(",", ":")), encoding="utf-8")

    manifeste = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else {}
    manifeste[nom] = {
        "titre": infos["titre"], "auteur": infos["auteur"], "page": url_page,
        "licence": f"{LICENCE_ACCEPTEE} (recolorée : œuvre dérivée, même licence)",
        "sens": sens, "boucle": boucle, "importe_le": date.today().isoformat(),
    }
    SOURCES.write_text(json.dumps(manifeste, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Animation '%s' importée (%s, par %s) : %d Ko",
                nom, infos["titre"], infos["auteur"], cible.stat().st_size // 1024)
    return cible


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parseur = argparse.ArgumentParser(description="Importe une animation LottieFiles")
    parseur.add_argument("--page", required=True, help="URL de la page de l'animation")
    parseur.add_argument("--nom", required=True, help="Nom court, en minuscules")
    parseur.add_argument("--sens", required=True, help="Ce que l'animation évoque")
    parseur.add_argument("--une-fois", action="store_true",
                         help="Jouer une seule fois puis rester sur la dernière image")
    args = parseur.parse_args()
    try:
        importer(args.page, args.nom, args.sens, boucle=not args.une_fois)
    except ImportRefuse as e:
        logger.error("Import refusé : %s", e)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
