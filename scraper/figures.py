"""
Extraction des figures d'un article source, avec leur légende et leur crédit.

**Pourquoi.** Tout ce que le projet dessine est inventé : des schémas construits
sur des données que le modèle imagine, des fonds abstraits générés, des photos de
banque. Rien ne vient de l'article dont on parle. Une figure d'un vrai papier
montre ce qu'aucun schéma reconstitué ne montrera, et distingue enfin une vidéo
de la suivante.

**L'attribution n'est pas optionnelle.** La licence par défaut d'arXiv accorde à
arXiv un droit de diffusion, pas à un tiers. Chaque figure part donc avec son
crédit, que la slide affiche en petit — c'est la contrepartie assumée de son
emploi, et `Figure.attribution` n'est jamais vide.

**Ce module ne télécharge rien.** Il rend des descriptions ; le cache disque de
`content/images.py` reste le seul endroit qui écrit sous `data/images/`.

Usage :
    python -m scraper.figures --url https://huggingface.co/blog/x/y
    python -m scraper.figures --source arxiv --source-id 2609.11860v1
"""

import argparse
import logging
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; shortform-ai-factory/1.0)"}
TIMEOUT = 25

# Une figure de contenu se distingue mal d'un logo par son seul balisage. Ces
# fragments d'URL désignent le chrome de page — avatars d'auteur, badges,
# pictogrammes d'interface — que le blog HuggingFace mêle au corps de l'article.
_CHROME = ("cdn-avatars", "/avatars/", "/badge", "/logo", "favicon", "emoji")

LEGENDE_MAX = 220

# Plafond du nombre de figures rendues. Mesuré : un papier de benchmark en
# expose 46, dont l'essentiel sont des « qualitative examples » d'annexe. Les
# lister toutes gonfle le prompt et noie le choix du modèle, alors qu'un article
# place ses figures décisives en tête — l'architecture en 1, le résultat
# principal en 2 ou 3. Le plafond s'applique à l'extraction et non à
# l'affichage, pour que l'indice choisi par le modèle désigne la même figure au
# moment du téléchargement.
FIGURES_MAX = 12


@dataclass(frozen=True)
class Figure:
    """
    Une figure extraite d'un article, prête à illustrer une slide.

    Attributes:
        url:         Adresse absolue de l'image
        legende:     Légende d'origine, tronquée ; vide si l'article n'en donne pas
        attribution: Crédit à afficher, jamais vide
        page:        Page dont la figure est tirée, pour la traçabilité
    """

    url: str
    legende: str
    attribution: str
    page: str


def _pertinente(url: str) -> bool:
    """Écarte le chrome de page et les formats qui ne sont pas des figures."""
    if not url or url.startswith("data:"):
        return False
    minuscule = url.lower()
    if any(motif in minuscule for motif in _CHROME):
        return False
    # Le SVG est écarté : les figures utiles sont matricielles, et un SVG dans
    # ce contexte est presque toujours une icône d'interface. Une URL de CDN
    # sans extension reste plausible, seule une extension écartée disqualifie.
    return not urlparse(minuscule).path.endswith(".svg")


def _propre(texte: str) -> str:
    """Légende ramenée à une ligne, tronquée."""
    return " ".join((texte or "").split())[:LEGENDE_MAX]


def _est_nom_de_fichier(texte: str) -> bool:
    """
    Reconnaît une pseudo-légende qui n'est que le nom du fichier téléversé.

    Le blog HuggingFace remplit `alt` avec le nom d'origine du fichier :
    « census_v2_decoupled », « census_thumbnail_x_5x2_nocaption ». Affiché tel
    quel sous une figure, c'est pire que pas de légende du tout. Un vrai
    intitulé comporte au moins une espace.
    """
    return bool(texte) and " " not in texte


def _telecharger(url: str) -> "BeautifulSoup | None":
    try:
        reponse = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        reponse.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Page illisible (%s) : %s", url, e)
        return None
    return BeautifulSoup(reponse.text, "html.parser")


def _base_arxiv(page: str, source_id: str, source: str) -> str:
    """
    Base contre laquelle résoudre le chemin d'une image arXiv.

    Deux conventions coexistent selon la conversion. Le plus souvent le chemin
    porte déjà l'identifiant du papier — « 2609.11860v1/images/x.png » — et le
    résoudre contre la page le dupliquerait, ce qui donne un 404 : vérifié,
    l'adresse à deux identifiants renvoie 404 quand celle à un seul renvoie 200.
    D'autres conversions écrivent un chemin nu, « x1.png », relatif à la page.
    """
    if source.startswith(source_id):
        return page.rsplit("/", 1)[0] + "/"
    return page + "/"


def _figures_arxiv(source_id: str) -> list[Figure]:
    """
    Figures de la version HTML d'un papier arXiv.

    Environ un papier sur trois n'en a pas : arXiv ne génère la conversion que
    pour les soumissions dont il a la source LaTeX. Un 404 n'est donc pas une
    anomalie, et la fonction rend simplement une liste vide.

    Les figures y sont correctement balisées, `<figure>` et `<figcaption>`, ce
    qui donne une légende fiable — au contraire du blog, où il faut la deviner.
    """
    page = f"https://arxiv.org/html/{source_id}"
    soup = _telecharger(page)
    if soup is None:
        return []

    trouvees: list[Figure] = []
    for bloc in soup.find_all("figure"):
        image = bloc.find("img")
        source = image.get("src") if image else None
        if not source or not _pertinente(source):
            continue
        legende = bloc.find("figcaption")
        trouvees.append(Figure(
            url=urljoin(_base_arxiv(page, source_id, source), source),
            legende=_propre(legende.get_text(" ", strip=True) if legende else ""),
            attribution=f"arXiv:{source_id}",
            page=page,
        ))
    return trouvees


def _auteur_huggingface(url: str) -> str:
    """Auteur du billet, tiré du chemin /blog/<auteur>/<titre>."""
    morceaux = [m for m in urlparse(url).path.split("/") if m]
    if len(morceaux) >= 3 and morceaux[0] == "blog":
        return morceaux[1]
    return "huggingface.co"


def _figures_huggingface(url: str) -> list[Figure]:
    """
    Figures d'un billet du blog HuggingFace.

    Le chrome est retiré par le même sélecteur `.not-prose` que `_hf_contenu` :
    votes, cartes d'auteur et boutons « Follow » y logent leurs avatars, qui
    représentaient la majorité des `<img>` de la page avant filtrage — mesuré,
    16 images dont 9 seulement de contenu sur un billet.

    Le blog n'utilisant presque jamais `<figure>`, la légende se cherche dans
    l'attribut `alt`, puis dans l'italique que les auteurs placent par convention
    juste sous l'image.
    """
    soup = _telecharger(url)
    if soup is None:
        return []

    for parasite in soup.select(".not-prose"):
        parasite.decompose()
    corps = soup.select_one(".prose") or soup

    credit = f"huggingface.co/blog — {_auteur_huggingface(url)}"
    trouvees: list[Figure] = []
    for image in corps.find_all("img"):
        source = image.get("src")
        if not _pertinente(source or ""):
            continue
        legende = _propre(image.get("alt", ""))
        if _est_nom_de_fichier(legende):
            legende = ""
        if not legende:
            voisin = image.find_next(["em", "figcaption", "small"])
            if voisin:
                candidate = _propre(voisin.get_text(" ", strip=True))
                legende = "" if _est_nom_de_fichier(candidate) else candidate
        trouvees.append(Figure(
            url=urljoin(url, source),
            legende=legende,
            attribution=credit,
            page=url,
        ))
    return trouvees


def figures_de(source: str, source_id: str = "", url: str = "") -> list[Figure]:
    """
    Figures illustratives d'un article, dans l'ordre où il les présente.

    Args:
        source:    Nom de la source ('arxiv', 'huggingface_blog', …)
        source_id: Identifiant, nécessaire à arXiv
        url:       Adresse de l'article, nécessaire au blog

    Returns:
        Les figures trouvées ; liste vide si la source est inconnue, la page
        illisible ou dépourvue de figure. **Jamais d'exception** : une
        illustration manquante ne doit pas coûter une vidéo, le pipeline
        retombant sur le schéma généré. Même règle que pour les embeddings.
    """
    try:
        if source == "arxiv" and source_id:
            trouvees = _figures_arxiv(source_id)
        elif source == "huggingface_blog" and url:
            trouvees = _figures_huggingface(url)
        else:
            logger.info("Pas d'extracteur de figures pour la source '%s'", source)
            return []
    except Exception as e:                       # noqa: BLE001 — repli délibéré
        logger.warning("Extraction des figures abandonnée (%s)", e)
        return []

    if len(trouvees) > FIGURES_MAX:
        logger.info("%d figures trouvées, %d retenues (les premières d'un article "
                    "sont les décisives)", len(trouvees), FIGURES_MAX)
        trouvees = trouvees[:FIGURES_MAX]

    logger.info("%d figure(s) retenue(s) pour %s", len(trouvees), source_id or url)
    return trouvees


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parseur = argparse.ArgumentParser(description="Figures d'un article source")
    parseur.add_argument("--source", default="", help="arxiv | huggingface_blog")
    parseur.add_argument("--source-id", default="", help="Identifiant arXiv")
    parseur.add_argument("--url", default="", help="Adresse de l'article")
    args = parseur.parse_args()

    source = args.source or ("arxiv" if args.source_id else "huggingface_blog")
    for i, figure in enumerate(figures_de(source, args.source_id, args.url), 1):
        print(f"\n[{i}] {figure.url}")
        print(f"    legende     : {figure.legende or '(aucune)'}")
        print(f"    attribution : {figure.attribution}")


if __name__ == "__main__":
    main()
