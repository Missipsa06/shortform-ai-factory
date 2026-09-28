"""
Collecte de matière documentaire par un modèle outillé (MCP).

**Le problème visé est celui que CLAUDE.md documente comme le pire du projet.**
En sujet libre, le résumé envoyé au LLM est fabriqué — `automation/workflow.py`
écrit « Explique le concept suivant de manière pédagogique : X ». Le modèle
rédige alors de mémoire et bouche les trous avec du remplissage. Changer de
fournisseur n'y change rien : c'est la matière qui manque, pas le modèle.

Ce module donne au modèle de quoi aller la chercher. Il rend un texte, rien de
plus : la rédaction du script reste `content/llm.py`, avec sa chaîne de repli,
son diagnostic de quota et sa validation des champs requis. Un seul champ change
en amont, `summary`.

**Pourquoi ici et nulle part ailleurs.** Le pipeline est déterministe par
construction : `seek()` existe pour que deux rendus donnent deux fichiers
identiques. Un agent qui décide à l'exécution rend chaque passage différent.
L'étape du script est la seule où l'aléatoire est déjà admis, puisque c'est un
modèle qui l'écrit. Y ajouter des outils ne dégrade donc aucune garantie.

**Tout échoue en repli, jamais en erreur.** Sans `uvx`, sans réseau, avec un
modèle incapable d'appeler des outils ou un serveur MCP muet, la fonction rend
une chaîne vide et l'appelant reprend le comportement d'aujourd'hui. Ajouter ce
module ne peut pas faire échouer une vidéo. Même règle que pour les embeddings.

Usage :
    python -m content.agent --topic "les modèles de diffusion"
    python -m content.agent --topic "RAG" --provider mistral --tours 5
"""

import argparse
import json
import logging
import os
import sys
import time

from content.mcp_client import ErreurMCP, ServeurMCP
from content.quota import depuis_exception
from content.providers import (
    Provider,
    _api_key,
    resolve_base_url,
    resolve_model,
    resolve_provider,
)

logger = logging.getLogger(__name__)


# ── Serveurs disponibles ──────────────────────────────────────────────────────
#
# Même patron que `PROVIDERS` et les `FOURNISSEURS` des autres adaptateurs : une
# table déclarative, un choix par variable d'environnement.
#
# `mcp-server-fetch` est l'implémentation de référence du projet MCP lui-même :
# pas de clé, pas de compte, pas de quota, et `uvx` est déjà là puisque le projet
# tourne sous uv. Elle convertit la page en markdown et sait la lire par tranches
# via `start_index`, ce qui évite de saturer la fenêtre de contexte.
SERVEURS_MCP: dict[str, list[str]] = {
    "fetch": ["uvx", "mcp-server-fetch"],
}

SERVEURS_ENV = "AGENT_MCP"
MAX_TOURS_DEFAUT = 4
# Plafond dur sur le nombre total d'appels d'outils. Sans lui, un modèle qui
# boucle sur une page inaccessible consommerait le quota du fournisseur sans
# jamais rendre la main.
MAX_APPELS = 8

INSTRUCTIONS = """Tu prépares la documentation d'une vidéo de vulgarisation en français sur l'IA et la data science.

Ta seule tâche ici est de RASSEMBLER DE LA MATIÈRE FACTUELLE. Tu n'écris pas le script.

Méthode :
1. Utilise l'outil de récupération de page pour lire de vraies sources sur le sujet.
   Privilégie arxiv.org, huggingface.co/blog, les documentations officielles.
2. Note ce qui est vérifiable : la méthode employée, les jeux de données, les
   chiffres, les dates, les noms de modèles, les limites reconnues.
3. Deux à quatre sources suffisent. Ne relis pas la même page.

Quand tu as assez de matière, réponds par une note de synthèse en français, en
texte simple, sans balises ni JSON. Termine par la liste des URL consultées.

Règles :
- N'invente rien. Si une information ne vient pas d'une page que tu as lue, ne
  l'écris pas.
- Si aucune source exploitable ne se trouve, réponds exactement : AUCUNE SOURCE
"""


def _serveurs_demandes() -> list[str]:
    """Serveurs MCP à ouvrir, depuis l'environnement ou tous par défaut."""
    brut = os.getenv(SERVEURS_ENV, "").strip()
    if not brut:
        return list(SERVEURS_MCP)
    return [n.strip() for n in brut.split(",") if n.strip() in SERVEURS_MCP]


def outils_openai(
    serveurs: "dict[str, ServeurMCP]",
) -> "tuple[list[dict], dict[str, tuple]]":
    """
    Traduit les schémas MCP en déclarations d'outils compatibles OpenAI.

    Les noms sont préfixés du serveur : deux serveurs peuvent exposer un `search`
    sans que le modèle puisse les confondre, et l'acheminement redevient trivial.

    Returns:
        (déclarations pour l'API, table nom → (serveur, outil d'origine))
    """
    declarations: list[dict] = []
    routage: dict[str, tuple] = {}
    for nom_serveur, serveur in serveurs.items():
        for outil in serveur.outils():
            nom = f"{nom_serveur}__{outil['name']}"
            declarations.append({
                "type": "function",
                "function": {
                    "name": nom,
                    "description": (outil.get("description") or "")[:1000],
                    "parameters": outil.get("inputSchema") or {"type": "object"},
                },
            })
            routage[nom] = (serveur, outil["name"])
    return declarations, routage


def _boucle(
    prov: Provider, modele: str, sujet: str,
    declarations: list[dict], routage: dict[str, tuple], max_tours: int,
) -> str:
    """
    Boucle d'appel d'outils, bornée en tours et en appels.

    Pas de mode JSON ici, contrairement au reste du projet : il force une réponse
    JSON et empêche donc le modèle d'émettre des appels d'outils. La rédaction
    finale, elle, garde le mode JSON — c'est `content/llm.py` qui s'en charge.
    """
    from openai import OpenAI

    client = OpenAI(api_key=_api_key(prov), base_url=resolve_base_url(prov))

    def completion(**extra):
        """
        Un appel, avec l'attente qu'impose le quota du fournisseur.

        **Une boucle d'agent multiplie les appels au modèle**, là où le reste du
        projet en fait un seul par vidéo. Le palier gratuit de Gemini plafonne à
        5 requêtes par minute : mesuré, une collecte de quatre tours l'atteint
        exactement. `content/quota.py` sait déjà lire le `retryDelay` annoncé et
        distinguer une limite par minute, qu'il suffit d'attendre, d'un quota
        journalier, où attendre ne sert à rien.
        """
        for essai in range(3):
            try:
                return client.chat.completions.create(
                    model=modele, messages=messages, max_tokens=4000, **extra
                )
            except Exception as exc:
                diag = depuis_exception(exc)
                if diag.genre not in ("minute", "surcharge") or essai == 2:
                    raise
                attente = max(diag.delai, 5.0)
                logger.warning("%s — attente de %.0f s puis réessai",
                               diag.message, attente)
                time.sleep(attente)
        raise RuntimeError("inatteignable")

    messages: list[dict] = [
        {"role": "system", "content": INSTRUCTIONS},
        {"role": "user", "content": f"Sujet : {sujet}"},
    ]
    appels = 0

    for tour in range(max_tours):
        reponse = completion(tools=declarations, tool_choice="auto")
        if not reponse.choices:
            raise RuntimeError(f"{prov.name}/{modele} n'a retourné aucune réponse")

        message = reponse.choices[0].message
        demandes = message.tool_calls or []
        if not demandes:
            return (message.content or "").strip()

        # Le message est renvoyé **tel que le fournisseur l'a produit**, et non
        # reconstruit champ par champ. Une reconstruction perd les extensions
        # propriétaires : Gemini exige de retrouver le `thought_signature` qu'il
        # a joint à chaque appel d'outil, et refuse la requête suivante par un
        # 400 sans lui. Vérifié en direct sur gemini-flash-latest.
        messages.append(message.model_dump(exclude_none=True))

        for demande in demandes:
            appels += 1
            nom = demande.function.name
            if appels > MAX_APPELS:
                contenu = "ERREUR : plafond d'appels atteint, conclus maintenant."
            elif nom not in routage:
                contenu = f"ERREUR : outil inconnu {nom}"
            else:
                serveur, outil = routage[nom]
                try:
                    arguments = json.loads(demande.function.arguments or "{}")
                    logger.info("Tour %d — %s(%s)", tour + 1, nom,
                                str(arguments)[:120])
                    contenu = serveur.appeler(outil, arguments)
                except (ErreurMCP, json.JSONDecodeError) as exc:
                    # Une source injoignable n'interrompt pas la collecte : le
                    # modèle en est informé et peut en essayer une autre.
                    logger.warning("Outil %s en échec : %s", nom, exc)
                    contenu = f"ERREUR : {exc}"
            messages.append({"role": "tool", "tool_call_id": demande.id,
                             "content": contenu})

        if appels > MAX_APPELS:
            break

    # Tours épuisés sans conclusion : on demande la synthèse, sans outils cette
    # fois, plutôt que de perdre ce qui a déjà été lu.
    logger.warning("Tours épuisés (%d) — demande de synthèse immédiate", max_tours)
    messages.append({"role": "user",
                     "content": "Conclus maintenant par ta note de synthèse."})
    finale = completion()
    return (finale.choices[0].message.content or "").strip() if finale.choices else ""


def rassembler_matiere(
    sujet: str, provider: "str | None" = None, max_tours: int = MAX_TOURS_DEFAUT,
) -> str:
    """
    Rassemble une matière documentaire réelle sur un sujet libre.

    Args:
        sujet:     Thème demandé, en français
        provider:  Fournisseur LLM ; None = LLM_PROVIDER puis défaut
        max_tours: Nombre maximal d'allers-retours avec les outils

    Returns:
        La note de synthèse, prête à servir de `summary` à `reformulate_article`.
        Une chaîne vide en cas d'échec : l'appelant reprend alors le résumé
        fabriqué d'aujourd'hui, et la vidéo se produit comme avant.
    """
    noms = _serveurs_demandes()
    if not noms:
        logger.warning("Aucun serveur MCP retenu — collecte abandonnée")
        return ""

    prov = resolve_provider(provider)
    modele = resolve_model(prov)
    ouverts: dict[str, ServeurMCP] = {}
    try:
        for nom in noms:
            serveur = ServeurMCP(SERVEURS_MCP[nom], nom=nom)
            serveur.__enter__()
            ouverts[nom] = serveur

        declarations, routage = outils_openai(ouverts)
        if not declarations:
            logger.warning("Les serveurs MCP n'exposent aucun outil")
            return ""
        logger.info("Collecte sur « %s » via %s/%s, %d outil(s)",
                    sujet, prov.name, modele, len(declarations))

        matiere = _boucle(prov, modele, sujet, declarations, routage, max_tours)
    except Exception as exc:
        # Repli volontairement large : ce module est un bonus. Aucune de ses
        # défaillances — uvx absent, réseau coupé, modèle sans appel d'outils —
        # ne doit empêcher de produire une vidéo.
        logger.warning("Collecte documentaire abandonnée (%s) — retour au "
                       "résumé fabriqué", exc)
        return ""
    finally:
        for serveur in ouverts.values():
            serveur.__exit__()

    if not matiere or matiere.strip() == "AUCUNE SOURCE":
        logger.warning("Aucune source exploitable trouvée sur « %s »", sujet)
        return ""
    logger.info("Matière rassemblée : %d caractères", len(matiere))
    return matiere


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parseur = argparse.ArgumentParser(
        description="Rassemble de la matière documentaire via un serveur MCP"
    )
    parseur.add_argument("--topic", required=True, help="Sujet à documenter")
    parseur.add_argument("--provider", default=None, help="Fournisseur LLM")
    parseur.add_argument("--tours", type=int, default=MAX_TOURS_DEFAUT)
    args = parseur.parse_args()

    matiere = rassembler_matiere(args.topic, args.provider, args.tours)
    if not matiere:
        print("Aucune matière rassemblée — le pipeline retomberait sur le "
              "résumé fabriqué.")
        raise SystemExit(1)
    fabrique = f"Explique le concept suivant de manière pédagogique : {args.topic}"
    # La console Windows est en cp1252 : un filet Unicode y lève une
    # UnicodeEncodeError, et la sortie est réencodée pour la même raison.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n" + "-" * 70)
    print(matiere)
    print("-" * 70)
    print(f"\n{len(matiere)} caractères, contre {len(fabrique)} "
          f"pour le résumé fabriqué actuel.")


if __name__ == "__main__":
    main()
