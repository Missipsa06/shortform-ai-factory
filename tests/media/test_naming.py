"""
Tests de media/naming.py : conventions de nommage reliant script/audio/vidéo.

Le module existe précisément parce que reconstruire ces noms à la main a déjà
cassé la liaison vidéo → script (voir sa docstring) — ces tests figent le
comportement attendu pour que ça ne se reproduise pas silencieusement.
"""

from pathlib import Path

import media.naming as naming
from media.naming import (
    find_video,
    list_video_stems,
    raw_video_path,
    script_path,
    stem_from_video,
    subtitled_video_path,
)


def test_stem_from_video_brute():
    assert stem_from_video(Path("data/videos/manuel_rag_raw.mp4")) == "manuel_rag"


def test_stem_from_video_sous_titree():
    assert stem_from_video(Path("data/videos/manuel_rag_raw_subtitled.mp4")) == "manuel_rag"


def test_stem_from_video_stem_avec_underscore():
    """Un stem qui contient lui-même des underscores ne doit pas être tronqué à tort."""
    chemin = Path("data/videos/arxiv_2401.12345_raw_subtitled.mp4")
    assert stem_from_video(chemin) == "arxiv_2401.12345"


def test_raw_et_subtitled_paths():
    assert raw_video_path("manuel_rag") == Path("data/videos/manuel_rag_raw.mp4")
    assert subtitled_video_path("manuel_rag") == Path(
        "data/videos/manuel_rag_raw_subtitled.mp4"
    )


def test_aller_retour_stem_chemin():
    """Round-trip : un stem reconstruit son chemin, dont on retrouve le même stem."""
    stem = "manuel_les_transformers"
    assert stem_from_video(raw_video_path(stem)) == stem
    assert stem_from_video(subtitled_video_path(stem)) == stem


def test_script_path():
    assert script_path("manuel_rag") == Path("data/processed/manuel_rag.json")


def test_find_video_prefere_la_version_sous_titree(tmp_path, monkeypatch):
    monkeypatch.setattr(naming, "VIDEOS_DIR", tmp_path)
    (tmp_path / "manuel_rag_raw.mp4").touch()
    (tmp_path / "manuel_rag_raw_subtitled.mp4").touch()

    trouve = find_video("manuel_rag")
    assert trouve == tmp_path / "manuel_rag_raw_subtitled.mp4"


def test_find_video_repli_sur_la_brute(tmp_path, monkeypatch):
    """--skip-subtitles ne produit qu'une vidéo brute : elle doit rester trouvable."""
    monkeypatch.setattr(naming, "VIDEOS_DIR", tmp_path)
    (tmp_path / "manuel_rag_raw.mp4").touch()

    assert find_video("manuel_rag") == tmp_path / "manuel_rag_raw.mp4"


def test_find_video_aucune_video():
    assert find_video("sujet-jamais-produit") is None


def test_list_video_stems(tmp_path, monkeypatch):
    monkeypatch.setattr(naming, "VIDEOS_DIR", tmp_path)
    (tmp_path / "manuel_rag_raw.mp4").touch()
    (tmp_path / "manuel_rag_raw_subtitled.mp4").touch()
    (tmp_path / "manuel_gan_raw.mp4").touch()

    # Un sujet avec ses deux stades ne doit apparaître qu'une fois.
    assert list_video_stems() == ["manuel_gan", "manuel_rag"]
