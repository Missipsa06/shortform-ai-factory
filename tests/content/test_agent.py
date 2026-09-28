"""
Tests de content/agent.py et content/mcp_client.py : logique pure.

Ni réseau, ni sous-processus, ni appel de modèle. La boucle `_boucle` et
`rassembler_matiere` restent hors de cette suite : elles parlent à un serveur MCP
réel et à un fournisseur LLM, donc au même titre que `call_llm` ou la synthèse
vocale, elles relèvent d'un essai manuel et non des tests unitaires.

Ce qui est couvert ici est ce qui casse en silence : la traduction des schémas
MCP vers le format d'outils d'OpenAI, la sélection des serveurs, et le
traitement des réponses d'outil.
"""

from content.agent import SERVEURS_MCP, _serveurs_demandes, outils_openai
from content.mcp_client import ServeurMCP


class ServeurFactice(ServeurMCP):
    """Un ServeurMCP qui ne lance aucun sous-processus."""

    def __init__(self, nom: str, outils: list[dict], reponse: "dict | None" = None):
        super().__init__(["faux"], nom=nom)
        self._outils = outils
        self._reponse = reponse or {}

    def _demander(self, methode: str, params: "dict | None" = None):
        return self._reponse


OUTIL_FETCH = {
    "name": "fetch",
    "description": "Récupère une URL et en extrait le contenu en markdown.",
    "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}},
                    "required": ["url"]},
}


# ── outils_openai ────────────────────────────────────────────────────────────

def test_traduit_un_schema_mcp_en_outil_openai():
    declarations, _ = outils_openai({"fetch": ServeurFactice("fetch", [OUTIL_FETCH])})
    assert len(declarations) == 1
    fonction = declarations[0]["function"]
    assert declarations[0]["type"] == "function"
    assert fonction["name"] == "fetch__fetch"
    assert fonction["parameters"] == OUTIL_FETCH["inputSchema"]
    assert "markdown" in fonction["description"]


def test_le_nom_est_prefixe_du_serveur():
    """Deux serveurs peuvent exposer un outil de même nom : sans préfixe, le
    modèle ne pourrait pas les distinguer et l'acheminement serait ambigu."""
    serveurs = {
        "a": ServeurFactice("a", [{"name": "search", "inputSchema": {}}]),
        "b": ServeurFactice("b", [{"name": "search", "inputSchema": {}}]),
    }
    declarations, routage = outils_openai(serveurs)
    noms = sorted(d["function"]["name"] for d in declarations)
    assert noms == ["a__search", "b__search"]
    assert len(routage) == 2


def test_le_routage_ramene_au_nom_dorigine():
    """Le serveur MCP ne connaît que le nom non préfixé."""
    serveur = ServeurFactice("fetch", [OUTIL_FETCH])
    _, routage = outils_openai({"fetch": serveur})
    cible, outil = routage["fetch__fetch"]
    assert cible is serveur
    assert outil == "fetch"


def test_schema_absent_donne_un_objet_vide():
    """Un outil sans `inputSchema` ferait rejeter la requête par l'API : le
    format d'OpenAI exige toujours un schéma de paramètres."""
    declarations, _ = outils_openai({"x": ServeurFactice("x", [{"name": "ping"}])})
    assert declarations[0]["function"]["parameters"] == {"type": "object"}


def test_description_tronquee():
    serveurs = {"x": ServeurFactice("x", [{"name": "t", "description": "a" * 3000,
                                           "inputSchema": {}}])}
    declarations, _ = outils_openai(serveurs)
    assert len(declarations[0]["function"]["description"]) == 1000


def test_aucun_serveur_ne_donne_aucune_declaration():
    declarations, routage = outils_openai({})
    assert declarations == [] and routage == {}


# ── Sélection des serveurs ───────────────────────────────────────────────────

def test_sans_variable_tous_les_serveurs_sont_retenus(monkeypatch):
    monkeypatch.delenv("AGENT_MCP", raising=False)
    assert _serveurs_demandes() == list(SERVEURS_MCP)


def test_selection_explicite(monkeypatch):
    monkeypatch.setenv("AGENT_MCP", "fetch")
    assert _serveurs_demandes() == ["fetch"]


def test_serveur_inconnu_est_ignore(monkeypatch):
    """Un nom fautif dans .env ne doit pas lancer une commande arbitraire."""
    monkeypatch.setenv("AGENT_MCP", "fetch,inexistant")
    assert _serveurs_demandes() == ["fetch"]


def test_selection_vide_desactive_la_collecte(monkeypatch):
    monkeypatch.setenv("AGENT_MCP", "inexistant")
    assert _serveurs_demandes() == []


def test_chaque_serveur_declare_une_commande_non_vide():
    for nom, commande in SERVEURS_MCP.items():
        assert commande and all(isinstance(a, str) for a in commande), nom


# ── Traitement des réponses d'outil ──────────────────────────────────────────

def test_appeler_concatene_les_blocs_texte():
    serveur = ServeurFactice("x", [], {"content": [{"type": "text", "text": "un"},
                                                   {"type": "text", "text": "deux"}]})
    assert serveur.appeler("t", {}) == "un\ndeux"


def test_appeler_ignore_les_blocs_non_textuels():
    """Une image renvoyée par un outil n'a pas sa place dans le contexte."""
    serveur = ServeurFactice("x", [], {"content": [{"type": "image", "data": "..."},
                                                   {"type": "text", "text": "ok"}]})
    assert serveur.appeler("t", {}) == "ok"


def test_appeler_tronque_les_reponses_longues():
    """Une page entière saturerait la fenêtre de contexte en un seul appel et
    ferait échouer la rédaction qui suit."""
    serveur = ServeurFactice("x", [], {"content": [{"type": "text", "text": "a" * 50_000}]})
    texte = serveur.appeler("t", {}, max_car=1000)
    assert len(texte) < 1100
    assert "tronqué" in texte


def test_appeler_signale_une_erreur_sans_lever():
    """Une source injoignable est rendue au modèle comme un texte d'erreur : il
    peut alors en essayer une autre, au lieu d'interrompre toute la collecte."""
    serveur = ServeurFactice("x", [], {"isError": True,
                                       "content": [{"type": "text", "text": "404"}]})
    assert serveur.appeler("t", {}).startswith("ERREUR")


def test_appeler_sur_reponse_vide():
    assert ServeurFactice("x", [], {"content": []}).appeler("t", {}) == ""


# ── Garde-fous de la boucle ──────────────────────────────────────────────────

def test_les_plafonds_sont_definis():
    """Sans borne, un modèle bouclant sur une page inaccessible consommerait le
    quota du fournisseur sans jamais rendre la main."""
    from content.agent import MAX_APPELS, MAX_TOURS_DEFAUT
    assert 0 < MAX_TOURS_DEFAUT <= 10
    assert 0 < MAX_APPELS <= 20
