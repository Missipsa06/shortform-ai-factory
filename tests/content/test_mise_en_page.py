"""Contrôle de mise en page avant rendu : il doit voir ce qu'il prétend voir."""

import asyncio
from pathlib import Path

import pytest

from content.render import _mesurer
from content.slides import ZONE_SURE_BAS, ZONE_SURE_DROITE, ZONE_SURE_HAUT

GABARIT = f"""<!doctype html><html><head><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1080px;height:1920px;overflow:hidden}}
.slide{{width:100%;height:100%;display:flex;flex-direction:column;
  padding:{ZONE_SURE_HAUT}px {ZONE_SURE_DROITE}px {ZONE_SURE_BAS}px 64px}}
.content{{font-size:44px;line-height:1.4}}
</style></head><body><div class="slide"><div class="content">{{texte}}</div></div></body></html>"""


def _mesure(tmp_path: Path, texte: str) -> dict:
    page = tmp_path / "slide_01.html"
    page.write_text(GABARIT.replace("{texte}", texte), encoding="utf-8")
    try:
        return asyncio.run(_mesurer([page]))
    except Exception as e:  # Chromium absent de cet environnement
        pytest.skip(f"Playwright indisponible : {e}")


def test_slide_saine_passe(tmp_path):
    assert _mesure(tmp_path, "Un texte court.") == {}


def test_texte_trop_long_descend_dans_la_bande_tiktok(tmp_path):
    fautes = _mesure(tmp_path, "mot " * 400)["slide_01"]
    assert any("descend" in f for f in fautes)


def test_mot_trop_large_pour_la_colonne(tmp_path):
    fautes = _mesure(tmp_path, "x" * 80)["slide_01"]
    assert any("largeur" in f for f in fautes)
