"""
Tests de content/providers.py : extraction JSON et sélection de fournisseur LLM.

Pas d'appel réseau ici — call_llm/call_llm_json (qui parlent aux API) restent
hors de cette suite ; ce fichier couvre la logique pure autour d'eux.
"""

import pytest

from content.providers import (
    FALLBACK_ENV,
    PROVIDERS,
    _api_key,
    _chaine,
    _taille_milliards,
    extract_json,
    modeles_candidats,
    resolve_base_url,
    resolve_model,
    resolve_provider,
)


# ── extract_json ────────────────────────────────────────────────────────────

def test_json_pur():
    assert extract_json('{"a": 1}') == '{"a": 1}'


def test_json_dans_balises_markdown():
    brut = '```json\n{"a": 1}\n```'
    assert extract_json(brut) == '{"a": 1}'


def test_json_precede_dune_phrase():
    brut = 'Voici le script demandé :\n{"a": 1}'
    assert extract_json(brut) == '{"a": 1}'


def test_raisonnement_avant_le_json():
    """
    Cas documenté dans le module : un modèle gratuit d'OpenRouter écrit sa
    réflexion dans le contenu, laquelle peut elle-même citer des fragments du
    schéma JSON demandé. On doit retenir l'objet réellement équilibré le plus
    long, pas le premier '{' au dernier '}'.
    """
    brut = (
        'We need to produce JSON exactly as specified: {"items": ["item1"]} '
        'is just an example from the schema, not the real answer.\n'
        '{"titre_video": "Les transformers", "slides": [1, 2, 3]}'
    )
    resultat = extract_json(brut)
    assert resultat == '{"titre_video": "Les transformers", "slides": [1, 2, 3]}'


def test_accolade_dans_une_chaine_ne_fausse_pas_le_comptage():
    brut = '{"contenu": "un exemple de code : { et }", "titre": "x"}'
    resultat = extract_json(brut)
    import json
    assert json.loads(resultat) == {"contenu": "un exemple de code : { et }", "titre": "x"}


def test_rien_de_parsable_renvoie_le_texte_brut():
    brut = "je ne sais pas répondre"
    assert extract_json(brut) == brut


def test_guillemets_echappes_dans_la_chaine():
    brut = '{"texte": "il a dit \\"bonjour\\""}'
    import json
    assert json.loads(extract_json(brut))["texte"] == 'il a dit "bonjour"'


# ── _taille_milliards ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("identifiant,attendu", [
    ("google/gemma-4-31b-it:free", 31.0),
    ("nvidia/nemotron-3-super-120b-a12b", 120.0),  # le plus grand des deux nombres
    ("mistralai/mistral-7b-instruct-v0.3", 7.0),
    ("nvidia/nemotron-3-ultra-550b-a55b", 550.0),
    ("un-nom-sans-taille", 0.0),
])
def test_taille_milliards(identifiant, attendu):
    assert _taille_milliards(identifiant) == attendu


# ── resolve_provider / resolve_model ─────────────────────────────────────────

def test_resolve_provider_connu():
    assert resolve_provider("mistral").name == "mistral"


def test_resolve_provider_insensible_a_la_casse():
    assert resolve_provider("MISTRAL").name == "mistral"


def test_resolve_provider_inconnu_leve():
    with pytest.raises(ValueError, match="inconnu"):
        resolve_provider("openai-direct")


def test_resolve_provider_defaut_sans_argument(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert resolve_provider(None).name == "mistral"


def test_resolve_provider_lit_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert resolve_provider(None).name == "gemini"


def test_resolve_model_defaut(monkeypatch):
    prov = PROVIDERS["mistral"]
    monkeypatch.delenv(prov.model_env, raising=False)
    assert resolve_model(prov) == prov.default_model


def test_resolve_model_surcharge_env(monkeypatch):
    prov = PROVIDERS["mistral"]
    monkeypatch.setenv(prov.model_env, "mistral-small-latest")
    assert resolve_model(prov) == "mistral-small-latest"


# ── Tous les fournisseurs déclarés sont cohérents ────────────────────────────

@pytest.mark.parametrize("nom", sorted(PROVIDERS))
def test_chaque_fournisseur_a_une_config_complete(nom):
    prov = PROVIDERS[nom]
    assert prov.name == nom
    assert prov.key_env
    assert prov.default_model
    assert prov.base_url.startswith("https://")
    assert prov.signup_url.startswith("https://")


def test_api_key_manquante_leve_avec_le_lien_dinscription(monkeypatch):
    prov = PROVIDERS["mistral"]
    monkeypatch.delenv(prov.key_env, raising=False)
    with pytest.raises(EnvironmentError, match=prov.signup_url):
        _api_key(prov)


def test_resolve_base_url_sans_gabarit_est_inchangee():
    assert resolve_base_url(PROVIDERS["mistral"]) == PROVIDERS["mistral"].base_url


# ── Ordre de repli (_chaine) ──────────────────────────────────────────────────

def test_chaine_reduite_au_demande_si_repli_coupe(monkeypatch):
    monkeypatch.setenv(FALLBACK_ENV, "0")
    demande = PROVIDERS["mistral"]
    assert _chaine(demande) == [demande]


def test_chaine_ecarte_les_fournisseurs_sans_cle(monkeypatch):
    monkeypatch.delenv(FALLBACK_ENV, raising=False)
    for prov in PROVIDERS.values():
        monkeypatch.delenv(prov.key_env, raising=False)
    monkeypatch.setenv(PROVIDERS["gemini"].key_env, "une-cle")

    chaine = _chaine(PROVIDERS["mistral"])

    # 'mistral' est toujours en tête (demandé explicitement), même sans clé —
    # c'est call_llm() qui vérifie sa clé séparément et lève avant d'appeler
    # _chaine. Les autres maillons sont filtrés à la clé disponible.
    assert chaine[0].name == "mistral"
    assert [p.name for p in chaine[1:]] == ["gemini"]


def test_chaine_ne_repete_pas_le_fournisseur_demande(monkeypatch):
    monkeypatch.delenv(FALLBACK_ENV, raising=False)
    for prov in PROVIDERS.values():
        monkeypatch.setenv(prov.key_env, "une-cle")

    chaine = _chaine(PROVIDERS["gemini"])
    noms = [p.name for p in chaine]
    assert noms.count("gemini") == 1
    assert noms[0] == "gemini"


# ── Modèles de secours ──────────────────────────────────────────────────────

def test_modeles_secours_suivent_le_defaut(monkeypatch):
    """
    Régression : le 404 passager du seul modèle NVIDIA déclaré a coûté le
    fournisseur entier, donc l'exécution — il est le dernier d'`ORDRE_REPLI`.
    """
    monkeypatch.delenv(PROVIDERS["nvidia"].model_env, raising=False)

    candidats = modeles_candidats(PROVIDERS["nvidia"])
    assert candidats[0] == PROVIDERS["nvidia"].default_model
    assert len(candidats) > 1
    assert candidats[1:] == list(PROVIDERS["nvidia"].modeles_secours)


def test_aucun_doublon_parmi_les_candidats(monkeypatch):
    """Essayer deux fois le même modèle doublerait l'attente sans rien gagner."""
    for prov in PROVIDERS.values():
        monkeypatch.delenv(prov.model_env, raising=False)
        if prov.name == "openrouter":
            continue          # son catalogue est distant, testé ailleurs
        candidats = modeles_candidats(prov)
        assert len(candidats) == len(set(candidats)), prov.name


def test_surcharge_env_ignore_les_secours(monkeypatch):
    """Un modèle désigné à la main ne se fait pas remplacer en douce."""
    prov = PROVIDERS["nvidia"]
    monkeypatch.setenv(prov.model_env, "nvidia/un-modele-precis")
    assert modeles_candidats(prov) == ["nvidia/un-modele-precis"]
