"""
Tests de la sélection des sources en mode multi-sources.

Aucune base ni modèle : `get_articles` et `_scores_semantiques` sont remplacés,
ce dernier par des similarités brutes du même ordre que celles mesurées sur le
corpus réel le 18/10/2026 (bruit ≤ 0,25, pertinent ≥ 0,29).
"""

import pytest

import automation.workflow as workflow
import scraper.storage as storage

RESUME = "Résumé suffisamment long pour passer RESUME_MINIMUM. " * 10


def _article(ident: str, titre: str) -> dict:
    return {"source_id": ident, "title": titre, "summary": RESUME,
            "source": "arxiv", "url": ""}


def _brancher(monkeypatch, articles: list[dict], similarites: dict) -> None:
    monkeypatch.setattr(storage, "get_articles", lambda **_: articles)
    monkeypatch.setattr(workflow, "_scores_semantiques", lambda *_: similarites)


def test_un_theme_hors_corpus_ne_retient_rien(monkeypatch):
    """Normalisé par le maximum, le meilleur bruit valait 0,5 et passait :
    le seuil porte sur la similarité brute."""
    articles = [_article("a", "Coding agents"), _article("b", "Sovereign LLM")]
    _brancher(monkeypatch, articles, {"a": 0.198, "b": 0.196})
    with pytest.raises(RuntimeError, match="similarité"):
        workflow.sujet_multi_sources("la cuisine italienne")


def test_seuls_les_articles_proches_sont_retenus(monkeypatch):
    articles = [_article("a", "Diffusion A"), _article("b", "Diffusion B"),
                _article("c", "Robotique")]
    _brancher(monkeypatch, articles, {"a": 0.56, "b": 0.31, "c": 0.22})
    sujet = workflow.sujet_multi_sources("les modèles de diffusion")
    assert sujet.articles == ("a", "b")


def test_un_meme_papier_sous_deux_identifiants_ne_compte_qu_une_fois(monkeypatch):
    articles = [_article("2610.22341", "How to Loop MoE"),
                _article("2610.22341v1", "How to Loop MoE "),
                _article("autre", "Sparse attention")]
    _brancher(monkeypatch, articles,
              {"2610.22341": 0.40, "2610.22341v1": 0.40, "autre": 0.35})
    sujet = workflow.sujet_multi_sources("mixture of experts")
    assert len(sujet.articles) == 2
    assert "autre" in sujet.articles


def test_sans_vecteur_le_lexical_reste_seul_juge(monkeypatch):
    """Corpus non indexé : on retombe sur la règle d'avant les embeddings."""
    articles = [_article("a", "Diffusion models explained"), _article("b", "Robotics")]
    _brancher(monkeypatch, articles, {})
    sujet = workflow.sujet_multi_sources("diffusion")
    assert sujet.articles == ("a",)
