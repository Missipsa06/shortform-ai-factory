"""
Tests des sources non techniques : Montreal AI Ethics et NIST.

Aucune requête réseau : `_articles_rss` et `_corps_distant` sont remplacés par
des fragments écrits ici, calqués sur la structure réelle des deux flux. Le
comportement face aux vrais serveurs reste vérifié à la main, par la ligne de
commande — même limite que pour le reste de la suite.

Les trois sources historiques ne sont pas couvertes : elles préexistent à ces
tests et leur ajouter une couverture dépasse le sujet.
"""

from xml.etree import ElementTree as ET

import pytest

import scraper.sources as sources
from scraper.sources import (
    LIBELLES_FAMILLE,
    MONTREAL_LICENCE,
    NIST_LICENCE,
    NIST_MOTIF_IA,
    SOURCES,
    _date_rss,
    famille,
    profil,
    scrape_montreal_ai_ethics,
    scrape_nist,
    sources_de,
)

FLUX_MONTREAL = """<rss><channel>
  <item>
    <title>The AI Ethics Brief #199: The Capacity to Govern</title>
    <link>https://montrealethics.ai/the-ai-ethics-brief-199/</link>
    <pubDate>Mon, 15 Sep 2026 10:30:14 +0000</pubDate>
    <description>Un extrait trop court pour le pipeline.</description>
  </item>
</channel></rss>"""

FLUX_NIST = """<rss><channel>
  <item>
    <title>Announcing the &amp;quot;AI Agent&amp;quot; Consortium Scope</title>
    <link>https://www.nist.gov/news-events/news/2026/09/nist-ai-consortium</link>
    <pubDate>Tue, 04 Aug 2026 12:00:00 -0400</pubDate>
    <description>Work on artificial intelligence standards.</description>
  </item>
  <item>
    <title>NIST Releases Guide on Concrete Durability</title>
    <link>https://www.nist.gov/news-events/news/2026/09/concrete</link>
    <pubDate>Tue, 04 Aug 2026 12:00:00 -0400</pubDate>
    <description>Construction materials measurement.</description>
  </item>
</channel></rss>"""

CORPS = "Contenu d'article suffisamment long pour le pipeline. " * 8


@pytest.fixture
def sans_reseau(monkeypatch):
    """Remplace le flux et le suivi de lien par des valeurs fixes."""
    def poser(xml: str, corps: str = CORPS):
        monkeypatch.setattr(
            sources, "_articles_rss",
            lambda _url: ET.fromstring(xml).findall(".//item"),
        )
        monkeypatch.setattr(sources, "_corps_distant", lambda _u, _s: corps)
        monkeypatch.setattr(sources.time, "sleep", lambda _s: None)
    return poser


# ── Licences ────────────────────────────────────────────────────────────────

def test_chaque_article_porte_sa_licence(sans_reseau):
    """
    La licence manque toujours au moment où elle compte — à la publication,
    des semaines après la collecte. Elle voyage donc avec l'article.
    """
    sans_reseau(FLUX_MONTREAL)
    for article in scrape_montreal_ai_ethics(limit=1):
        assert article["licence"] == MONTREAL_LICENCE

    sans_reseau(FLUX_NIST)
    for article in scrape_nist(limit=1):
        assert article["licence"] == NIST_LICENCE


def test_aucune_source_retenue_ne_porte_de_clause_nd_ou_nc():
    """
    Le pipeline reformule, traduit et adapte en vidéo : il produit une œuvre
    dérivée. Une licence « No Derivatives » l'interdit, si généreuse
    paraisse-t-elle — c'est ce qui a écarté The Conversation et Numerama. Le
    « NC » a écarté AlgorithmWatch, par choix éditorial.
    """
    for licence in (MONTREAL_LICENCE, NIST_LICENCE):
        jetons = licence.replace("-", " ").upper().split()
        assert "ND" not in jetons
        assert "NC" not in jetons


# ── Filtrage NIST ───────────────────────────────────────────────────────────

def test_nist_ecarte_ce_qui_ne_parle_pas_dia(sans_reseau):
    """Le flux couvre tout l'institut : mesuré, 10 articles sur 40 parlent
    d'IA. Sans filtre, la file se remplirait de normes sur le béton."""
    sans_reseau(FLUX_NIST)
    titres = [a["title"] for a in scrape_nist(limit=10)]
    assert len(titres) == 1
    assert "Consortium" in titres[0]


@pytest.mark.parametrize("texte", [
    "Advances in AI research", "a new machine learning model",
    "neural network evaluation", "this algorithm is faster", "LLM benchmarks",
])
def test_le_motif_reconnait_le_vocabulaire_du_domaine(texte):
    assert NIST_MOTIF_IA.search(texte)


@pytest.mark.parametrize("texte", [
    "Concrete durability standards", "Quantum clock precision",
])
def test_le_motif_laisse_passer_le_reste_de_linstitut(texte):
    assert NIST_MOTIF_IA.search(texte) is None


def test_la_limite_compte_les_articles_retenus(sans_reseau):
    """
    `limit` doit borner ce qui est *rendu*, pas ce qui est examiné : borner
    l'examen ferait rendre moins d'articles que demandé dès qu'un sujet hors
    IA occupe une des premières places du flux.
    """
    debut = FLUX_NIST.index("<item>")
    item = FLUX_NIST[debut:FLUX_NIST.index("</item>") + len("</item>")]
    hors_sujet = item.replace("AI Consortium Scope", "Concrete Durability")
    sans_reseau(f"<rss><channel>{hors_sujet * 3}{item * 2}</channel></rss>")
    assert len(list(scrape_nist(limit=2))) == 2


# ── Dégradation ─────────────────────────────────────────────────────────────

def test_un_article_sans_corps_est_ignore(sans_reseau):
    """Sans contenu, l'article ferait inventer le LLM : mieux vaut ne pas
    l'enregistrer que de polluer la file de sujets."""
    sans_reseau(FLUX_MONTREAL, corps="")
    assert list(scrape_montreal_ai_ethics(limit=5)) == []


def test_flux_injoignable_ne_leve_pas(monkeypatch):
    """Une source en panne ne doit pas interrompre la veille : les autres
    continuent. Même règle que pour Papers With Code."""
    monkeypatch.setattr(sources, "_articles_rss", lambda _url: [])
    assert list(scrape_montreal_ai_ethics(limit=5)) == []
    assert list(scrape_nist(limit=5)) == []


# ── Contrat commun ──────────────────────────────────────────────────────────

def test_les_nouvelles_sources_sont_au_registre():
    """Le registre alimente aussi les `choices` de la CLI : absente d'ici, une
    source n'est atteignable par aucune commande."""
    assert SOURCES["montreal_ai_ethics"] is scrape_montreal_ai_ethics
    assert SOURCES["nist"] is scrape_nist


def test_le_dict_rendu_respecte_le_contrat_de_save_article(sans_reseau):
    sans_reseau(FLUX_MONTREAL)
    article = next(iter(scrape_montreal_ai_ethics(limit=1)))
    for champ in ("source", "source_id", "title", "url", "summary",
                  "published", "tags", "licence", "raw"):
        assert champ in article, champ
    assert article["source"] == "montreal_ai_ethics"
    assert len(article["summary"]) >= 200      # RESUME_MINIMUM


@pytest.mark.parametrize("brut,attendu", [
    ("Mon, 15 Sep 2026 10:30:14 +0000", "2026-09-15"),
    ("Tue, 04 Aug 2026 12:00:00 -0400", "2026-08-04"),
])
def test_les_dates_rss_deviennent_iso(brut, attendu):
    """Les autres sources datent en ISO : mélanger les formats casserait tout
    tri par date sans le moindre message."""
    assert _date_rss(brut) == attendu


def test_une_date_illisible_ne_fait_pas_echouer():
    """Un flux mal formé coûterait sinon l'article entier."""
    assert _date_rss("jeudi prochain") is None


def test_les_entites_html_des_titres_sont_developpees(sans_reseau):
    """
    Un flux RSS encode ses guillemets en `&quot;`, que le parseur XML ne
    développe pas. Le titre finit à l'écran dans la vidéo : « Announcing the
    &quot;AI Agent&quot; » s'y lirait tel quel. Observé sur le flux du NIST.
    """
    sans_reseau(FLUX_NIST)
    titre = next(iter(scrape_nist(limit=1)))["title"]
    assert "&quot;" not in titre
    assert '"AI Agent"' in titre


# ── Familles de sources ─────────────────────────────────────────────────────

@pytest.mark.parametrize("source,attendue", [
    ("arxiv", "technique"),
    ("huggingface_blog", "technique"),
    ("papers_with_code", "technique"),
    ("montreal_ai_ethics", "societe"),
    ("nist", "societe"),
])
def test_chaque_source_appartient_a_une_famille(source, attendue):
    """La famille est ce qui sert à filtrer : « une vidéo technique » ou
    « une vidéo de société ». `nature` reste la granularité fine."""
    assert famille(source) == attendue


def test_les_deux_familles_partitionnent_le_registre():
    """Aucune source ne doit tomber entre les deux, sinon elle deviendrait
    injoignable dès qu'un registre est demandé."""
    from scraper.sources import PROFILS
    technique = set(sources_de("technique"))
    societe = set(sources_de("societe"))
    assert technique | societe == set(PROFILS)
    assert not technique & societe


def test_une_source_inconnue_est_reputee_technique():
    """`veille` et `manuel` nomment des scripts déjà produits, d'un temps où
    le corpus n'était que technique. Les classer ailleurs les ferait
    disparaître d'un filtre « technique »."""
    assert famille("veille") == "technique"
    assert famille("manuel") == "technique"
    assert famille("") == "technique"


def test_chaque_famille_a_un_libelle():
    """L'interface et la ligne de commande doivent nommer la même chose."""
    for cle in ("technique", "societe"):
        assert LIBELLES_FAMILLE[cle]
        assert sources_de(cle)


def test_le_libelle_nomme_les_sources_reelles():
    """Un libellé qui mentirait sur son contenu serait pire que pas de libellé
    du tout : c'est sur lui qu'on choisit."""
    assert "arXiv" in LIBELLES_FAMILLE["technique"]
    assert "NIST" in LIBELLES_FAMILLE["societe"]
    assert profil("nist").libelle in LIBELLES_FAMILLE["societe"]
