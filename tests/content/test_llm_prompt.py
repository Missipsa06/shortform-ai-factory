"""Tests du prompt de rédaction : types « dev » et mise en valeur des titres."""

import json
import re

from content.llm import SLIDE_PROMPT, _bloc_figures


def _prompt() -> str:
    return SLIDE_PROMPT.format(title="T", summary="S", figures=_bloc_figures(None))


def test_format_ne_casse_pas():
    prompt = _prompt()
    assert "Titre du papier/article : T" in prompt
    assert "{{" not in prompt and "__" not in re.sub(r"\*\*", "", prompt)


def test_types_dev_proposes():
    prompt = _prompt()
    ligne = re.search(r'"type": "([^"]+)"', prompt).group(1).split("|")
    assert "terminal" in ligne and "recap" in ligne
    assert '- "terminal"' in prompt and '- "recap"' in prompt
    assert '"depuis"' in prompt and '"cmd"' in prompt


def test_nouveaux_champs_demandes():
    prompt = _prompt()
    for champ in ("sous_titre_video", "surtitre", "sous_titre", "titre_conclusion"):
        assert f'"{champ}"' in prompt
    assert "**" in prompt


def test_exemples_terminal_et_recap_sont_du_json_valide():
    prompt = _prompt()
    for debut in ('{"chemin"', '{"items": [\n                     {"code"'):
        i = prompt.index(debut)
        # Accolade fermante correspondante.
        profondeur = 0
        for j, c in enumerate(prompt[i:], start=i):
            profondeur += {"{": 1, "}": -1}.get(c, 0)
            if profondeur == 0:
                break
        json.loads(prompt[i:j + 1])
