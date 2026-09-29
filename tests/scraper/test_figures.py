"""
Tests de scraper/figures.py : extraction des figures d'un article source.

Aucune requête réseau : `_telecharger` est remplacé par des fragments HTML
écrits ici, calqués sur la structure réelle des deux sources. Le comportement
face à un vrai serveur reste vérifié à la main, par la ligne de commande du
module — c'est la même limite que pour le reste de la suite.
"""

import pytest
from bs4 import BeautifulSoup

import scraper.figures as figures
from scraper.figures import (
    FIGURES_MAX,
    Figure,
    _auteur_huggingface,
    _base_arxiv,
    _est_nom_de_fichier,
    _pertinente,
    figures_de,
)

ARXIV_HTML = """
<html><body>
  <figure><img src="2609.11860v1/images/archi.png">
    <figcaption>Figure 1: Explainability Assistant system architecture.</figcaption>
  </figure>
  <figure><img src="2609.11860v1/images/radar.png">
    <figcaption>Figure 2: Individual expert ratings.</figcaption>
  </figure>
  <figure><img src="logo.svg"><figcaption>Écarté</figcaption></figure>
</body></html>
"""

HF_HTML = """
<html><body>
  <div class="not-prose"><img src="https://cdn-avatars.huggingface.co/a.png"></div>
  <div class="prose">
    <img src="https://cdn-uploads.huggingface.co/x/vraie-figure.png"
         alt="census_v2_decoupled">
    <em>Le bac à sable découplé du rollout</em>
    <img src="/relatif/seconde.png" alt="Une vraie légende ici">
  </div>
</body></html>
"""


@pytest.fixture
def sans_reseau(monkeypatch):
    """Remplace le téléchargement par un fragment fixe."""
    def poser(html):
        monkeypatch.setattr(
            figures, "_telecharger", lambda _url: BeautifulSoup(html, "html.parser")
        )
    return poser


# ── Filtrage des images ──────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://cdn-avatars.huggingface.co/v1/a.png",
    "https://huggingface.co/avatars/abc.svg",
    "https://exemple.fr/assets/logo.png",
    "https://exemple.fr/badge-v2.png",
    "https://exemple.fr/favicon.png",
    "data:image/png;base64,iVBOR",
    "https://exemple.fr/schema.svg",
    "",
])
def test_le_chrome_de_page_est_ecarte(url):
    """Sur un billet du blog, les avatars étaient la majorité des images."""
    assert _pertinente(url) is False


@pytest.mark.parametrize("url", [
    "https://cdn-uploads.huggingface.co/production/uploads/abc/XYZ.png",
    "https://arxiv.org/html/2609.11860v1/images/archi.png",
    "https://cdn-images-1.medium.com/max/3640/1*SSY.jpeg",
    "https://exemple.fr/figure-sans-extension",   # les CDN en servent
])
def test_les_figures_de_contenu_passent(url):
    assert _pertinente(url) is True


# ── Légendes ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("texte", ["census_v2_decoupled", "thumbnail_x_5x2", "IMG0012"])
def test_un_nom_de_fichier_nest_pas_une_legende(texte):
    """Le blog remplit `alt` avec le nom du fichier téléversé ; l'afficher sous
    la figure serait pire que de ne rien afficher."""
    assert _est_nom_de_fichier(texte) is True


@pytest.mark.parametrize("texte", ["Une vraie légende", "Figure 1: architecture", ""])
def test_une_vraie_legende_est_conservee(texte):
    assert _est_nom_de_fichier(texte) is False


# ── Résolution des chemins arXiv ─────────────────────────────────────────────

def test_chemin_arxiv_deja_prefixe_de_lidentifiant():
    """
    Régression : le chemin porte le plus souvent déjà l'identifiant du papier.
    Le résoudre contre la page le dupliquait, et l'adresse à deux identifiants
    renvoie 404 là où celle à un seul renvoie 200.
    """
    base = _base_arxiv("https://arxiv.org/html/2609.11860v1", "2609.11860v1",
                       "2609.11860v1/images/x.png")
    assert base == "https://arxiv.org/html/"


def test_chemin_arxiv_nu():
    """D'autres conversions écrivent un chemin relatif à la page."""
    base = _base_arxiv("https://arxiv.org/html/2609.11860v1", "2609.11860v1", "x1.png")
    assert base == "https://arxiv.org/html/2609.11860v1/"


# ── Extraction arXiv ─────────────────────────────────────────────────────────

def test_arxiv_rend_les_figures_legendees(sans_reseau):
    sans_reseau(ARXIV_HTML)
    trouvees = figures_de("arxiv", source_id="2609.11860v1")
    assert len(trouvees) == 2                     # le SVG est écarté
    assert trouvees[0].url == "https://arxiv.org/html/2609.11860v1/images/archi.png"
    assert "architecture" in trouvees[0].legende


def test_arxiv_attribue_toujours(sans_reseau):
    """L'attribution est la contrepartie assumée de l'usage : jamais vide."""
    sans_reseau(ARXIV_HTML)
    for figure in figures_de("arxiv", source_id="2609.11860v1"):
        assert figure.attribution == "arXiv:2609.11860v1"


def test_page_illisible_ne_leve_pas(monkeypatch):
    """Un papier sur trois n'a pas de version HTML : un 404 est banal."""
    monkeypatch.setattr(figures, "_telecharger", lambda _url: None)
    assert figures_de("arxiv", source_id="2609.99999v1") == []


# ── Extraction HuggingFace ───────────────────────────────────────────────────

def test_huggingface_ecarte_le_chrome_et_garde_les_figures(sans_reseau):
    sans_reseau(HF_HTML)
    url = "https://huggingface.co/blog/sergiopaniego/rl-environments-2026"
    trouvees = figures_de("huggingface_blog", url=url)
    assert len(trouvees) == 2
    assert all("cdn-avatars" not in f.url for f in trouvees)


def test_huggingface_remplace_un_nom_de_fichier_par_la_legende_voisine(sans_reseau):
    """`alt` valant « census_v2_decoupled », la légende se cherche dans l'italique
    que les auteurs placent sous l'image."""
    sans_reseau(HF_HTML)
    url = "https://huggingface.co/blog/sergiopaniego/rl-environments-2026"
    premiere = figures_de("huggingface_blog", url=url)[0]
    assert premiere.legende == "Le bac à sable découplé du rollout"


def test_huggingface_resout_les_chemins_relatifs(sans_reseau):
    sans_reseau(HF_HTML)
    url = "https://huggingface.co/blog/sergiopaniego/rl-environments-2026"
    seconde = figures_de("huggingface_blog", url=url)[1]
    assert seconde.url == "https://huggingface.co/relatif/seconde.png"


@pytest.mark.parametrize("url,attendu", [
    ("https://huggingface.co/blog/mlabonne/decoding", "mlabonne"),
    ("https://huggingface.co/blog/officiel", "huggingface.co"),
])
def test_auteur_extrait_du_chemin(url, attendu):
    assert _auteur_huggingface(url) == attendu


# ── Dégradation ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("source,sid,url", [
    ("nist", "abc", ""),
    ("arxiv", "", ""),                 # identifiant manquant
    ("huggingface_blog", "", ""),      # url manquante
    ("", "", ""),
])
def test_source_non_geree_rend_une_liste_vide(source, sid, url):
    assert figures_de(source, sid, url) == []


def test_une_exception_inattendue_est_absorbee(monkeypatch):
    """Une illustration manquante ne doit jamais coûter une vidéo : le pipeline
    retombe sur le schéma généré. Même règle que pour les embeddings."""
    def explose(_url):
        raise ValueError("gabarit inattendu")
    monkeypatch.setattr(figures, "_telecharger", explose)
    assert figures_de("arxiv", source_id="2609.11860v1") == []


def test_le_nombre_de_figures_est_plafonne(sans_reseau):
    """
    Un papier de benchmark en expose 46, dont l'essentiel sont des exemples
    d'annexe. Les lister toutes gonflerait le prompt et noierait le choix du
    modèle, alors que les figures décisives ouvrent l'article.
    """
    blocs = "".join(
        f'<figure><img src="2609.1v1/im/{i}.png">'
        f"<figcaption>Figure {i}</figcaption></figure>"
        for i in range(40)
    )
    sans_reseau(f"<html><body>{blocs}</body></html>")
    trouvees = figures_de("arxiv", source_id="2609.1v1")
    assert len(trouvees) == FIGURES_MAX
    # Les premières, et dans l'ordre : l'indice doit désigner la même figure au
    # moment du téléchargement qu'au moment du choix.
    assert trouvees[0].legende == "Figure 0"
    assert trouvees[-1].legende == f"Figure {FIGURES_MAX - 1}"


def test_figure_est_immuable():
    """Une figure circule jusqu'au rendu : la muter en route rendrait
    l'attribution incertaine au moment de l'afficher."""
    figure = Figure(url="u", legende="l", attribution="a", page="p")
    with pytest.raises(Exception):
        figure.attribution = "autre"        # type: ignore[misc]
