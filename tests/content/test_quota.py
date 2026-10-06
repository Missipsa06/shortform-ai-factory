"""
Tests de content/quota.py : classification des échecs d'appel LLM/TTS/images.

Module choisi en premier pour la suite de tests car il ne dépend d'aucun autre
module du projet ni du réseau — chaque cas rejoue un corps d'erreur réel
documenté dans quota.py (Google, Mistral, Moonshot).
"""

import json

from content.quota import Diagnostic, depuis_exception, diagnostiquer, prochaine_reinitialisation


# ── Corps enveloppé dans un tableau (endpoint OpenAI de Google) ──────────────

_QUOTA_JOUR_GOOGLE = {
    "error": {
        "code": 429,
        "message": "You exceeded your current quota",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{
                 "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                 "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                 "quotaValue": "20"}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo",
             "retryDelay": "14s"},
        ],
    }
}


def test_quota_journalier_enveloppe_dans_un_tableau():
    """
    Régression : l'endpoint compatible OpenAI de Google renvoie son erreur dans
    un tableau JSON. Le corps n'étant pas déballé, `quotaId` et `retryDelay`
    étaient perdus et un quota journalier passait pour une limite par minute —
    le pipeline réessayait alors le même fournisseur épuisé pour la journée au
    lieu de basculer. Mesuré sur gemini-flash-latest.
    """
    diag = diagnostiquer(429, [_QUOTA_JOUR_GOOGLE])
    assert diag.genre == "jour"
    assert diag.limite == "20"
    assert diag.delai == 0.0


def test_meme_verdict_avec_ou_sans_enveloppe():
    """L'enveloppe est un détail de transport : elle ne doit rien changer."""
    nu = diagnostiquer(429, _QUOTA_JOUR_GOOGLE)
    enveloppe = diagnostiquer(429, [_QUOTA_JOUR_GOOGLE])
    assert (nu.genre, nu.limite) == (enveloppe.genre, enveloppe.limite)


def test_tableau_serialise_en_chaine():
    """Le corps arrive parfois en texte brut plutôt qu'en objet déjà décodé."""
    diag = diagnostiquer(429, json.dumps([_QUOTA_JOUR_GOOGLE]))
    assert diag.genre == "jour"


def test_tableau_vide_ne_leve_pas():
    assert diagnostiquer(429, []).genre in {"minute", "autre"}


def test_tableau_sans_dictionnaire_ne_leve_pas():
    assert diagnostiquer(429, ["texte", 42]).genre in {"minute", "autre"}


def test_surcharge_503():
    diag = diagnostiquer(503, "")
    assert diag.genre == "surcharge"
    assert diag.reessayable
    assert not diag.epuise
    assert diag.delai > 0


def test_quota_par_minute_google():
    corps = {
        "error": {
            "message": "Resource exhausted",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {"quotaId": "GenerateContentPerMinutePerProject", "quotaValue": "15"}
                    ],
                },
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12s"},
            ],
        }
    }
    diag = diagnostiquer(429, corps)
    assert diag.genre == "minute"
    assert diag.reessayable
    assert not diag.epuise
    assert diag.delai == 12.0
    assert diag.limite == "15"


def test_quota_par_jour_google():
    """Un plafond journalier prime sur toute autre violation présente."""
    corps = {
        "error": {
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {"quotaId": "GenerateContentPerMinutePerProject", "quotaValue": "15"},
                        {"quotaId": "GenerateContentPerDayPerProject", "quotaValue": "1500"},
                    ],
                },
            ],
        }
    }
    diag = diagnostiquer(429, corps)
    assert diag.genre == "jour"
    assert diag.epuise
    assert not diag.reessayable
    assert diag.delai == 0.0
    assert "1500" in diag.message


def test_retry_delay_ne_depasse_jamais_le_plafond():
    """Un fournisseur qui annonce un délai extravagant ne doit pas geler le pipeline."""
    corps = {
        "error": {
            "details": [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "600s"},
            ],
        }
    }
    diag = diagnostiquer(429, corps)
    assert diag.delai == 60.0  # DELAI_MAX


def test_compte_suspendu_moonshot():
    """Un 429 permanent (solde épuisé) ne doit pas être traité comme récupérable."""
    corps = {"error": {"message": "Your account is suspended due to insufficient balance"}}
    diag = diagnostiquer(429, corps)
    assert diag.genre == "jour"
    assert diag.epuise
    assert not diag.reessayable


def test_mistral_zero_requete_par_minute():
    """Mistral place le message à la racine, sans objet 'error' imbriqué."""
    corps = {"object": "error", "message": "Requests rate limit exceeded, recharge your account"}
    diag = diagnostiquer(429, corps)
    assert diag.genre == "jour"
    assert diag.epuise


GOOGLE_GENERIQUE = ("You exceeded your current quota, please check your plan and "
                    "billing details. For more information on this error, head to: "
                    "https://ai.google.dev/gemini")


def test_message_generique_google_par_minute_reste_minute():
    """Google écrit « billing » sur tous ses 429 : c'est le quotaId qui tranche.
    Observé le 29/09/2026 sur Gemini TTS, pris pour un compte suspendu."""
    corps = {"error": {"message": GOOGLE_GENERIQUE, "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
                         "quotaValue": "3"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "20s"},
    ]}}
    diag = diagnostiquer(429, corps)
    assert diag.genre == "minute"
    assert diag.reessayable
    assert diag.delai == 20.0


def test_message_generique_google_sans_details_reste_minute():
    diag = diagnostiquer(429, {"error": {"message": GOOGLE_GENERIQUE}})
    assert diag.genre == "minute"


def test_429_sans_information_reste_minute():
    """Sans identifiant de quota ni mention de suspension : hypothèse la moins coûteuse."""
    diag = diagnostiquer(429, {})
    assert diag.genre == "minute"
    assert diag.reessayable


def test_echec_autre_code_http():
    diag = diagnostiquer(400, {"error": {"message": "bad request"}})
    assert diag.genre == "autre"
    assert not diag.reessayable
    assert not diag.epuise
    assert "bad request" in diag.message


def test_statut_none():
    """Un statut illisible (exception réseau, pas de réponse HTTP) ne doit pas planter."""
    diag = diagnostiquer(None, None)
    assert diag.genre == "autre"
    assert diag.delai == 0.0


def test_corps_chaine_json():
    """Le corps peut arriver en texte brut (reponse.text) plutôt qu'en dict déjà parsé."""
    corps = '{"error": {"message": "quota exceeded"}}'
    diag = diagnostiquer(429, corps)
    assert diag.genre == "minute"


def test_corps_illisible_ne_leve_pas():
    diag = diagnostiquer(429, "not json at all {{{")
    assert diag.genre == "minute"


def test_depuis_exception_duck_typing():
    """Les SDK OpenAI/Anthropic exposent status_code/body en attributs, pas en dict."""

    class FausseErreurAPI(Exception):
        status_code = 429
        body = {"error": {"message": "Resource exhausted"}}

    diag = depuis_exception(FausseErreurAPI("boom"))
    assert diag.genre == "minute"


def test_depuis_exception_via_response_attribute():
    class FausseReponse:
        status_code = 503
        text = ""

    class FausseErreurReseau(Exception):
        response = FausseReponse()

    diag = depuis_exception(FausseErreurReseau("boom"))
    assert diag.genre == "surcharge"


def test_depuis_exception_sans_statut_garde_le_message():
    """Sans code HTTP lisible, le message de l'exception est la seule information utile."""
    diag = depuis_exception(ConnectionError("DNS lookup failed"))
    assert diag.genre == "autre"
    assert "DNS lookup failed" in diag.message


def test_prochaine_reinitialisation_ne_leve_jamais():
    texte = prochaine_reinitialisation()
    assert isinstance(texte, str)
    assert texte


def test_diagnostic_est_immuable():
    diag = Diagnostic(genre="minute", delai=1.0, limite=None, message="x")
    try:
        diag.genre = "jour"
        assert False, "Diagnostic doit être frozen"
    except AttributeError:
        pass
