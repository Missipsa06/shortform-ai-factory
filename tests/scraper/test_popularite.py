"""
Tests des sources en tendance et du classement par popularité.

Aucune requête réseau : `_json` et `requests.get` sont remplacés par des
réponses calquées sur celles des API réelles, relevées le 07/10/2026.
"""

from datetime import datetime, timedelta, timezone

import pytest

import scraper.sources as sources
from automation.workflow import _par_popularite
from scraper.sources import (
    HN_MOTIF_IA,
    SOURCES,
    _texte_fiche,
    scrape_hf_modeles,
    scrape_hf_papers,
)

MAINTENANT = datetime(2026, 10, 7, 8, 0, tzinfo=timezone.utc)
FICHE = "---\nlicense: apache-2.0\n---\n# Modèle\n" + "Texte utile de la fiche. " * 20


def _article(source: str, ident: str, popularite=None, age_jours: float = 0) -> dict:
    return {
        "source": source, "source_id": ident, "popularite": popularite,
        "popularite_le": MAINTENANT - timedelta(days=age_jours)
        if popularite is not None else None,
    }


# ── Classement ──────────────────────────────────────────────────────────────

def test_le_rang_se_calcule_au_sein_de_chaque_source():
    """Un modèle à 800 likes ne doit pas écraser le billet le plus voté du
    blog : les deux sont premiers de leur source, et passent avant les seconds."""
    articles = [
        _article("huggingface_modeles", "m_second", 800),
        _article("huggingface_modeles", "m_premier", 3000),
        _article("huggingface_blog", "b_premier", 15),
        _article("huggingface_blog", "b_second", 4),
    ]
    ordre = [a["source_id"] for a in _par_popularite(articles, MAINTENANT)]
    assert set(ordre[:2]) == {"m_premier", "b_premier"}
    assert set(ordre[2:]) == {"m_second", "b_second"}


def test_un_buzz_eteint_ne_passe_plus_devant():
    """Un relevé vieux de plus de deux jours ne dit plus rien du présent."""
    articles = [_article("hacker_news", "vieux", 1700, age_jours=5),
                _article("hacker_news", "frais", 150)]
    assert _par_popularite(articles, MAINTENANT)[0]["source_id"] == "frais"


def test_sans_popularite_lordre_recu_est_conserve():
    """Les sources sans signal gardent le comportement d'avant : le plus
    récent d'abord, tel que la base les rend."""
    articles = [_article("nist", "recent"), _article("nist", "ancien")]
    assert [a["source_id"] for a in _par_popularite(articles, MAINTENANT)] == \
        ["recent", "ancien"]


# ── Sources ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("titre", [
    "Run Qwen 3.8 Flash Next (125B) on consumer hardware",
    "Gemini 4 Argon", "Kolibri: A Sovereign Open-Weight Model",
    "Getting the most out of Opus 5.5 in Claude",
])
def test_hacker_news_reconnait_les_sujets_ia(titre):
    assert HN_MOTIF_IA.search(titre)


@pytest.mark.parametrize("titre", [
    "Powerless F1 drivers frustrated by Bahrain F1 software glitch",
    "The lamps in my house", "Git 3.0's upcoming SHA-256 default",
])
def test_hacker_news_ecarte_le_reste_de_la_tech(titre):
    """Relevés tels quels sur une recherche « LLM » de la semaine."""
    assert HN_MOTIF_IA.search(titre) is None


def test_modeles_ecarte_les_variantes_sans_censure_et_les_licences_nd(monkeypatch):
    """Une variante « uncensored » occupait une place sur six du classement ;
    une licence ND interdirait l'adaptation en vidéo."""
    monkeypatch.setattr(sources, "_json", lambda *_a, **_k: [
        {"id": "x/Qwen-Image-Uncensored-GGUF", "trendingScore": 900, "tags": []},
        {"id": "x/sous-nd", "trendingScore": 800, "tags": [],
         "cardData": {"license": "cc-by-nc-nd-4.0"}},
        {"id": "Cloudflare/clef", "trendingScore": 700, "tags": [],
         "cardData": {"license": "apache-2.0"}},
    ])

    class Reponse:
        text = FICHE
        def raise_for_status(self): pass

    monkeypatch.setattr(sources.requests, "get", lambda *_a, **_k: Reponse())
    monkeypatch.setattr(sources.time, "sleep", lambda _s: None)

    retenus = list(scrape_hf_modeles(limit=5))
    assert [m["title"] for m in retenus] == ["Cloudflare/clef"]
    assert retenus[0]["licence"] == "apache-2.0"
    assert retenus[0]["popularite"] == 700


def test_la_fiche_perd_son_entete_et_ses_balises():
    texte = _texte_fiche(
        "---\nlicense: mit\n---\n![logo](a.png) Voir [le blog](https://x.y) <b>ici</b>"
    )
    assert texte == "Voir le blog ici"


def test_daily_papers_enregistre_sous_arxiv_par_votes(monkeypatch):
    """Les figures et la licence ne connaissent qu'`arxiv` : un papier voté sur
    HuggingFace reste un papier arXiv, le vote seul s'ajoute."""
    monkeypatch.setattr(sources, "_json", lambda *_a, **_k: [
        {"paper": {"id": "2610.1", "title": "Peu voté", "summary": "x" * 300,
                   "upvotes": 3}},
        {"paper": {"id": "2610.2", "title": "Très  voté", "summary": "y" * 300,
                   "upvotes": 146}},
    ])
    papiers = list(scrape_hf_papers(limit=1))
    assert papiers[0]["source"] == "arxiv"
    assert papiers[0]["source_id"] == "2610.2"
    assert papiers[0]["title"] == "Très voté"
    assert papiers[0]["popularite"] == 146


def test_une_page_qui_nest_pas_du_html_est_ignoree(monkeypatch):
    """Parsé comme du HTML, un PDF lié depuis Hacker News passerait le seuil
    de longueur et partirait au LLM comme un résumé."""
    class Pdf:
        headers = {"Content-Type": "application/pdf"}
        text = "%PDF-1.7 " + "x" * 5000
        def raise_for_status(self): pass

    monkeypatch.setattr(sources.requests, "get", lambda *_a, **_k: Pdf())
    assert sources._corps_distant("https://x.y/a.pdf", ("body",)) == ""


def test_les_sources_en_tendance_sont_au_registre():
    for nom in ("hf_papers", "huggingface_modeles", "hacker_news"):
        assert nom in SOURCES
