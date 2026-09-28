"""
Adaptateur multi-fournisseurs pour l'appel LLM.

Le pipeline n'a besoin que d'une chose du LLM : (system, prompt) → texte JSON.
Ce module isole cette opération pour permettre de comparer la qualité de
vulgarisation entre fournisseurs sans toucher au reste du code.

Fournisseur choisi via LLM_PROVIDER dans .env (défaut : mistral) :

    gemini      Google AI Studio    GEMINI_API_KEY
    mistral     Mistral             MISTRAL_API_KEY
    openrouter  OpenRouter          OPENROUTER_API_KEY  (~15 modèles gratuits)
    nvidia      NVIDIA NIM          NVIDIA_API_KEY      (même clé que la TTS nvidia)
    kimi        Moonshot AI         MOONSHOT_API_KEY    (payant, aucun palier gratuit)

Tous exposent un endpoint compatible OpenAI : le SDK `openai` les couvre, et
tout autre fournisseur compatible (Groq, Cerebras, DeepSeek) s'ajoute en
créant une entrée dans PROVIDERS — un nom, une variable de clé, une URL.

Le SDK est importé à l'usage, pas au chargement du module.
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from content.quota import depuis_exception, prochaine_reinitialisation

load_dotenv()
logger = logging.getLogger(__name__)

# Marge confortable : 8 slides + graphiques + métadonnées dépassent régulièrement
# 2048 tokens, et les modèles gratuits sont souvent plus verbeux.
MAX_TOKENS = 4096

DEFAULT_PROVIDER = "mistral"

# Le repli est actif par défaut : sur un script, l'écart de qualité entre
# fournisseurs est mineur, et le fournisseur retenu est inscrit dans le JSON
# produit. Le désactiver (LLM_FALLBACK=0) sert à comparer deux fournisseurs sur
# un même sujet, où une bascule silencieuse invaliderait la comparaison.
FALLBACK_ENV = "LLM_FALLBACK"
_FAUX = frozenset({"0", "false", "non", "no", "off"})

# Ordre des replis : les deux paliers gratuits éprouvés d'abord, OpenRouter
# ensuite — ses modèles gratuits changent au fil des semaines, donc moins sûr
# —, nvidia après (un seul modèle vérifié, contre une quinzaine chez
# OpenRouter), Kimi en dernier puisqu'il n'a aucun palier gratuit. Un repli
# automatique ne doit pas engager de dépense par surprise.
ORDRE_REPLI = ("gemini", "mistral", "openrouter", "nvidia", "kimi")


@dataclass(frozen=True)
class Provider:
    """Description d'un fournisseur LLM."""

    name: str
    key_env: str          # variable d'environnement contenant la clé API
    model_env: str        # variable permettant de surcharger le modèle
    default_model: str
    base_url: str         # peut contenir {compte}, résolu par resolve_base_url
    signup_url: str       # où obtenir une clé, affiché en cas d'erreur
    # Modèles du même fournisseur essayés après `default_model`. Vide par
    # défaut : n'y mettre que des identifiants vérifiés en direct sur le compte,
    # un 404 « Not found for account » étant indiscernable d'un modèle retiré.
    #
    # Chaque entrée rallonge le pire cas de `call_llm`, qui accorde deux
    # tentatives par candidat et attend jusqu'à DELAI_MAX entre les deux : la
    # chaîne complète est passée de 8 à 10 candidats, soit un plafond théorique
    # de 8 à 10 minutes d'attente. Ce plafond ne se matérialise que si tous les
    # échecs sont classés réessayables ; le 404 visé ici ne l'est pas, et ne
    # coûte qu'un aller-retour réseau par candidat. À surveiller malgré tout
    # avant d'en ajouter à un deuxième fournisseur, l'effet étant cumulatif.
    modeles_secours: tuple[str, ...] = ()


PROVIDERS: dict[str, Provider] = {
    "kimi": Provider(
        name="kimi",
        key_env="MOONSHOT_API_KEY",
        model_env="KIMI_MODEL",
        # Alias `latest` comme pour les autres fournisseurs : les identifiants
        # figés finissent par disparaître. `moonshot-v1-32k` est le repli
        # documenté si cet alias venait à ne pas exister sur ton compte.
        default_model="kimi-latest",
        # Endpoint international. `api.moonshot.cn` est celui de Chine
        # continentale ; les deux répondent, celui-ci a la meilleure latence
        # depuis l'Europe.
        base_url="https://api.moonshot.ai/v1",
        signup_url="https://platform.moonshot.ai/",
    ),
    "gemini": Provider(
        name="gemini",
        key_env="GEMINI_API_KEY",
        model_env="GEMINI_MODEL",
        # Alias volontaire plutôt qu'une version figée : Google ferme les anciens
        # modèles aux nouveaux comptes (404), et un alias évite cette panne. En
        # contrepartie le modèle peut changer sous le pipeline — épingle via
        # GEMINI_MODEL (ex: gemini-3.6-flash) si tu veux des sorties stables.
        default_model="gemini-flash-latest",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        signup_url="https://aistudio.google.com/apikey",
    ),
    "openrouter": Provider(
        name="openrouter",
        key_env="OPENROUTER_API_KEY",
        model_env="OPENROUTER_MODEL",
        # Le suffixe `:free` fait partie de l'identifiant et désigne le palier
        # gratuit — l'omettre bascule sur la version facturée du même modèle.
        # Une clé donne accès à la quinzaine de modèles gratuits du catalogue,
        # dont la composition change au fil des semaines : vérifier sur
        # https://openrouter.ai/models?q=free avant d'épingler autre chose.
        default_model="google/gemma-4-31b-it:free",
        base_url="https://openrouter.ai/api/v1",
        signup_url="https://openrouter.ai/keys",
    ),
    "mistral": Provider(
        name="mistral",
        key_env="MISTRAL_API_KEY",
        model_env="MISTRAL_MODEL",
        # `mistral-large-latest` est sorti du palier gratuit : l'API répond
        # « This model is not available in your subscription tier » (403,
        # tier_not_allowed). `medium` fonctionne et reste proche en qualité ;
        # `mistral-small-latest` est le repli le plus sûr si le palier bouge
        # encore, il est accessible à tous les comptes.
        default_model="mistral-medium-latest",
        base_url="https://api.mistral.ai/v1",
        signup_url="https://console.mistral.ai/",
    ),
    "nvidia": Provider(
        name="nvidia",
        # Même variable que le moteur TTS "nvidia" (media/tts.py) : une clé
        # build.nvidia.com donne accès au catalogue entier, texte et voix.
        key_env="NVIDIA_API_KEY",
        model_env="NVIDIA_MODEL",
        # Le catalogue public (GET /v1/models) liste 82 modèles, mais un compte
        # gratuit n'y a pas tous accès — vérifié : la plupart des modèles tiers
        # (Llama, Mixtral, Gemma...) renvoient 404 "Function ... Not found for
        # account", alors que certains nvidia/nemotron répondent. Les candidats
        # ne se déduisent donc pas du catalogue, contrairement à OpenRouter :
        # chacun est essayé une fois et retenu s'il a répondu.
        default_model="nvidia/nemotron-3-ultra-550b-a55b",
        # Mesuré le 16/09/2026 sur les 17 modèles « nemotron » du catalogue :
        # ces deux-là répondent 200, tandis que nemotron-nano-3-30b-a3b,
        # llama-3.1-nemotron-70b/ultra-253b et nemotron-4-340b renvoient 404.
        # Sans eux, le 404 passager du seul modèle déclaré a fait perdre le
        # fournisseur entier — et avec lui l'exécution, les autres fournisseurs
        # étant limités au même instant.
        modeles_secours=(
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia/nemotron-3.5-lightning-30b-a3b",
        ),
        base_url="https://integrate.api.nvidia.com/v1",
        signup_url="https://build.nvidia.com/",
    ),
}


# ── Sélection du fournisseur ──────────────────────────────────────────────────

def resolve_provider(provider: str | None = None) -> Provider:
    """
    Détermine le fournisseur à utiliser.

    Args:
        provider: Nom explicite ; si None, lit LLM_PROVIDER puis retombe sur le défaut

    Returns:
        La configuration du fournisseur

    Raises:
        ValueError: Si le nom demandé est inconnu
    """
    name = (provider or os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    if name not in PROVIDERS:
        raise ValueError(
            f"Fournisseur LLM inconnu : '{name}'. "
            f"Valeurs possibles : {', '.join(sorted(PROVIDERS))}"
        )
    return PROVIDERS[name]


def resolve_model(prov: Provider) -> str:
    """Retourne le modèle à utiliser pour ce fournisseur (surchargeable via .env)."""
    return os.getenv(prov.model_env) or prov.default_model


# Au-delà, on s'acharne : quatre modèles couvrent largement une indisponibilité
# passagère, et chacun coûte deux tentatives.
MODELES_MAX = 4

_catalogue_openrouter: "list[str] | None" = None


def _taille_milliards(identifiant: str) -> float:
    """
    Nombre de paramètres déduit du nom du modèle, en milliards.

    Heuristique assumée : le catalogue n'expose pas la taille, mais les noms la
    portent presque toujours (`...-31b-it`, `...-550b-a55b`). Sur les modèles
    à experts, le premier nombre est le total et le second les paramètres
    actifs — on retient le plus grand, qui reflète la capacité.

    Returns:
        0.0 si le nom ne dit rien, ce qui place le modèle en fin de classement
    """
    nombres = re.findall(r"(\d+(?:\.\d+)?)b(?![a-z0-9])", identifiant.lower())
    return max((float(n) for n in nombres), default=0.0)


def _catalogue_gratuit() -> list[str]:
    """
    Modèles gratuits d'OpenRouter, du plus capable au moins capable.

    Le catalogue est interrogé une fois par processus : la liste change au fil
    des semaines, l'épingler dans le code condamnerait à la corriger à chaque
    retrait. L'endpoint est public, aucune clé n'est nécessaire pour le lire.

    Returns:
        Identifiants complets, suffixe `:free` compris ; vide si l'appel échoue
    """
    global _catalogue_openrouter
    if _catalogue_openrouter is not None:
        return _catalogue_openrouter

    try:
        import requests

        reponse = requests.get("https://openrouter.ai/api/v1/models", timeout=20)
        reponse.raise_for_status()
        gratuits = [
            m for m in reponse.json().get("data", [])
            if str(m.get("id", "")).endswith(":free")
        ]
        _catalogue_openrouter = [
            m["id"] for m in sorted(
                gratuits,
                key=lambda m: (-_taille_milliards(m["id"]), -m.get("context_length", 0)),
            )
        ]
        logger.debug("OpenRouter : %d modèles gratuits", len(_catalogue_openrouter))
    except Exception as e:
        logger.warning("Catalogue OpenRouter illisible (%s) — modèle par défaut", e)
        _catalogue_openrouter = []

    return _catalogue_openrouter


def modeles_candidats(prov: Provider) -> list[str]:
    """
    Modèles à essayer pour ce fournisseur, du plus capable au moins.

    Un seul pour la plupart : leur nom est un alias `latest` que le fournisseur
    fait pointer où il veut. OpenRouter est le cas particulier — son palier
    gratuit réunit une quinzaine de modèles dont la disponibilité varie d'une
    heure à l'autre, et descendre la liste évite de perdre l'exécution parce que
    le plus gros est momentanément saturé.

    `modeles_secours` étend le même principe aux fournisseurs dont le catalogue
    ne se lit pas : un 404 ou une saturation sur le modèle par défaut ne doit pas
    coûter le fournisseur entier, surtout en dernier rang de `ORDRE_REPLI` où
    plus rien ne le rattrape.

    Une surcharge explicite via .env n'est jamais contournée : si l'utilisateur
    a désigné un modèle, basculer sur un autre trahirait son choix.
    """
    surcharge = os.getenv(prov.model_env, "").strip()
    if surcharge:
        return [surcharge]

    if prov.name == "openrouter":
        catalogue = _catalogue_gratuit()
        if catalogue:
            return catalogue[:MODELES_MAX]

    return [prov.default_model, *prov.modeles_secours]


def resolve_base_url(prov: Provider) -> str:
    """
    URL de l'API, identifiant de compte substitué si nécessaire.

    Aucun fournisseur actif n'utilise ce mécanisme aujourd'hui — il servait à
    Cloudflare, dont l'URL portait l'identifiant de compte. Conservé parce que
    d'autres API le pratiquent, et que le cas est facile à manquer.

    Raises:
        EnvironmentError: Identifiant de compte manquant alors qu'il est requis
    """
    if not prov.base_url or "{compte}" not in prov.base_url:
        return prov.base_url or ""

    compte = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not compte:
        raise EnvironmentError(
            "CLOUDFLARE_ACCOUNT_ID manquant dans .env — nécessaire pour le "
            "fournisseur 'cloudflare', en plus du jeton. Il s'affiche dans la "
            "section Workers AI du tableau de bord Cloudflare."
        )
    return prov.base_url.format(compte=compte)


def _api_key(prov: Provider) -> str:
    """
    Récupère la clé API du fournisseur.

    Raises:
        EnvironmentError: Si la clé est absente des variables d'environnement
    """
    key = os.getenv(prov.key_env, "").strip()
    if not key:
        raise EnvironmentError(
            f"{prov.key_env} manquant dans .env — nécessaire pour le fournisseur "
            f"'{prov.name}'. Obtiens une clé sur {prov.signup_url}"
        )
    return key


def _repli_actif() -> bool:
    """Indique si la bascule automatique de fournisseur est autorisée."""
    return os.getenv(FALLBACK_ENV, "1").strip().lower() not in _FAUX


def _chaine(demande: Provider) -> list[Provider]:
    """
    Ordre d'essai des fournisseurs : celui demandé, puis les autres utilisables.

    Un fournisseur sans clé est écarté d'emblée : l'essayer produirait un échec
    de configuration au milieu d'une séquence de replis, brouillant le
    diagnostic réel.

    Args:
        demande: Fournisseur explicitement retenu par l'appelant

    Returns:
        Liste ordonnée, réduite au seul fournisseur demandé si le repli est coupé
    """
    chaine = [demande]
    if not _repli_actif():
        return chaine

    chaine += [
        PROVIDERS[nom]
        for nom in ORDRE_REPLI
        if nom != demande.name
        and nom in PROVIDERS
        and os.getenv(PROVIDERS[nom].key_env, "").strip()
    ]
    return chaine


# ── Nettoyage de la réponse ───────────────────────────────────────────────────

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def _objets_equilibres(texte: str):
    """
    Parcourt les objets JSON candidats, du plus long au plus court.

    Le comptage d'accolades ignore celles qui se trouvent dans une chaîne, sans
    quoi une accolade citée dans un texte fausserait l'équilibre.

    Yields:
        Fragments commençant par '{' et refermés
    """
    candidats: list[str] = []
    for depart in (i for i, c in enumerate(texte) if c == "{"):
        profondeur, dans_chaine, echappe = 0, False, False
        for i in range(depart, len(texte)):
            c = texte[i]
            if echappe:
                echappe = False
                continue
            if c == "\\":
                echappe = True
            elif c == '"':
                dans_chaine = not dans_chaine
            elif not dans_chaine:
                if c == "{":
                    profondeur += 1
                elif c == "}":
                    profondeur -= 1
                    if profondeur == 0:
                        candidats.append(texte[depart : i + 1])
                        break
    yield from sorted(candidats, key=len, reverse=True)


def extract_json(raw: str) -> str:
    """
    Isole le JSON d'une réponse LLM.

    Trois obstacles, rencontrés en production :

      - des balises markdown autour de la réponse ;
      - une phrase d'introduction avant l'objet ;
      - **le raisonnement du modèle**, écrit dans le contenu. Certains modèles
        gratuits d'OpenRouter exposent leur réflexion : « We need to produce JSON
        exactly as specified… », en citant au passage des fragments du schéma
        demandé. Découper du premier `{` au dernier `}` attrapait alors un bout
        de schéma cité, et le parsing échouait sur « Extra data ».

    On énumère donc les objets réellement équilibrés et on retient **le plus long
    qui se parse** : le script complet fait plusieurs milliers de caractères, les
    fragments cités quelques dizaines.

    Args:
        raw: Réponse brute du modèle

    Returns:
        Une chaîne candidate au parsing JSON
    """
    text = raw.strip()

    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()

    if text.startswith("{"):
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

    for candidat in _objets_equilibres(text):
        try:
            json.loads(candidat)
        except json.JSONDecodeError:
            continue
        if len(candidat) < len(text) * 0.9:
            logger.debug(
                "Objet JSON isolé (%d caractères sur %d)", len(candidat), len(text)
            )
        return candidat

    return text


# ── Appels par fournisseur ────────────────────────────────────────────────────

def _call_openai_compatible(
    prov: Provider, modele: str, system: str, prompt: str, max_tokens: int
) -> str:
    """
    Appelle un fournisseur exposant une API compatible OpenAI.

    Le modèle est passé explicitement plutôt que déduit du fournisseur : sur
    OpenRouter, plusieurs modèles sont essayés au sein d'un même fournisseur.

    Le mode JSON natif est activé : le modèle ne peut pas encadrer sa réponse de
    texte parasite, ce qui fiabilise le parsing.
    """
    from openai import OpenAI

    extra: dict = {}
    if prov.name in ("openrouter", "nvidia"):
        # Les 18 modèles gratuits d'OpenRouter sont tous des modèles à
        # raisonnement, et ils écrivent leur réflexion **dans le contenu**.
        # Mesuré sur un échec réel : 12 073 caractères de « We need to produce
        # JSON exactly as specified… » avant le script, lequel s'est retrouvé
        # tronqué par le plafond de tokens. Ce paramètre sort le raisonnement de
        # la réponse ; il n'existe pas chez les autres fournisseurs, d'où l'envoi
        # conditionnel.
        #
        # nvidia/nemotron-3-ultra-550b-a55b est du même genre : sans ce
        # paramètre, vérifié en direct, il ouvre sa réponse par un
        # raisonnement en anglais avant le JSON demandé.
        extra["reasoning"] = {"exclude": True}

    client = OpenAI(api_key=_api_key(prov), base_url=resolve_base_url(prov))
    completion = client.chat.completions.create(
        model=modele,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        extra_body=extra or None,
    )

    # Une réponse sans `choices` arrive quand le fournisseur refuse la requête
    # tout en répondant 200 — modèle momentanément sans capacité, notamment.
    # Sans ce contrôle, l'erreur remontait en « 'NoneType' object is not
    # subscriptable », qui ne désigne ni la cause ni le fournisseur.
    if not completion.choices:
        raise RuntimeError(
            f"{prov.name}/{modele} n'a retourné aucune réponse "
            f"(champ 'choices' vide)"
        )

    choice = completion.choices[0]
    if choice.finish_reason == "length":
        logger.warning(
            "Réponse tronquée (max_tokens=%d atteint) — le JSON sera probablement "
            "invalide. Augmente MAX_TOKENS.", max_tokens
        )

    return choice.message.content or ""


# ── Point d'entrée ────────────────────────────────────────────────────────────

def _appel_brut(
    prov: Provider, modele: str, system: str, prompt: str, max_tokens: int
) -> str:
    """
    Un appel, sans réessai ni repli.

    Raises:
        RuntimeError: SDK manquant, ou réponse vide
    """
    try:
        raw = _call_openai_compatible(prov, modele, system, prompt, max_tokens)
    except ImportError as e:
        raise RuntimeError(
            f"SDK manquant pour le fournisseur '{prov.name}' : uv add openai"
        ) from e

    if not raw.strip():
        raise RuntimeError(f"Réponse vide renvoyée par {prov.name}/{modele}")

    return raw


def call_llm(
    system: str,
    prompt: str,
    provider: str | None = None,
    max_tokens: int = MAX_TOKENS,
    trace: dict | None = None,
    valider: "callable | None" = None,
) -> str:
    """
    Envoie un couple (system, prompt) au fournisseur configuré, avec repli.

    Trois échecs sont distingués (voir content/quota.py) : une limite de débit ou
    une surcharge donnent lieu à un réessai sur le même fournisseur, un quota
    journalier épuisé fait passer au suivant. Chaque bascule est journalisée en
    WARNING — une dégradation silencieuse serait pire que l'échec.

    Args:
        system:     Instructions système (rôle, ton, contraintes)
        prompt:     Requête utilisateur
        provider:   Fournisseur explicite ; None = LLM_PROVIDER puis défaut
        max_tokens: Plafond de tokens en sortie
        trace:      Dictionnaire renseigné par l'appel avec le fournisseur ayant
                    réellement répondu ('provider', 'model', 'replis'). Sans lui,
                    l'appelant ne peut pas savoir qui a produit le texte.

    Returns:
        Le texte brut renvoyé par le modèle (non parsé)

    Raises:
        EnvironmentError: Clé API du fournisseur demandé manquante
        ValueError:       Fournisseur inconnu
        RuntimeError:     Tous les fournisseurs de la chaîne ont échoué
    """
    demande = resolve_provider(provider)

    # Validée avant l'import du SDK : le message reste le même quel que soit
    # l'état de l'installation. Une clé absente n'est pas un cas de repli mais
    # une erreur de configuration, qu'il faut signaler telle quelle.
    _api_key(demande)

    chaine = _chaine(demande)
    echecs: list[str] = []
    replis: list[str] = []
    quota_epuise = False

    for prov in chaine:
        if prov.name != demande.name:
            logger.warning(
                "Repli sur le fournisseur '%s' — le script ne sera pas produit "
                "par '%s'", prov.name, demande.name
            )
            replis.append(prov.name)

        # Plusieurs modèles possibles par fournisseur, du plus capable au moins.
        # Un modèle retiré du catalogue ou momentanément saturé ne doit pas faire
        # renoncer à l'ensemble du fournisseur.
        candidats = modeles_candidats(prov)
        for rang, model in enumerate(candidats):
            if rang == 0:
                logger.info("Appel LLM — fournisseur=%s modèle=%s", prov.name, model)
            else:
                logger.warning(
                    "Modèle indisponible — repli sur %s (%d/%d du catalogue)",
                    model, rang + 1, len(candidats),
                )

            # Deux tentatives au plus : la seconde n'a de sens que si la première
            # a échoué sur une cause passagère.
            for tentative in (1, 2):
                try:
                    raw = _appel_brut(prov, model, system, prompt, max_tokens)
                    # Une réponse hors contrat est un échec, pas un succès. Sans
                    # ce contrôle, un modèle renvoyant `{"items": ["item1"]}` —
                    # un fragment du schéma d'exemple, observé sur les modèles
                    # gratuits d'OpenRouter — voyait son déchet écrit sur disque,
                    # et la panne n'apparaissait qu'à la synthèse vocale.
                    if valider is not None and not valider(raw):
                        raise RuntimeError(
                            "réponse hors contrat : champs attendus absents"
                        )
                except (EnvironmentError, ValueError):
                    raise
                except Exception as e:
                    diag = depuis_exception(e)

                    if tentative == 1 and diag.reessayable:
                        logger.warning(
                            "%s/%s : %s — nouvelle tentative dans %.0f s",
                            prov.name, model, diag.message, diag.delai,
                        )
                        time.sleep(diag.delai)
                        continue

                    quota_epuise = quota_epuise or diag.epuise
                    logger.warning("%s/%s : %s", prov.name, model, diag.message)
                    echecs.append(f"{prov.name}/{model} — {diag.message}")
                    break
                else:
                    if trace is not None:
                        trace.update({
                            "provider": prov.name, "model": model, "replis": replis
                        })
                    return raw

    detail = " ; ".join(echecs)
    conseil = (
        f" Quota journalier atteint : réinitialisation vers {prochaine_reinitialisation()}."
        if quota_epuise
        else ""
    )
    if len(chaine) == 1 and _repli_actif():
        conseil += (
            " Aucun autre fournisseur n'a de clé configurée dans .env — "
            "GEMINI_API_KEY ou MISTRAL_API_KEY offrent un repli gratuit."
        )
    raise RuntimeError(f"Appel LLM échoué ({detail}).{conseil}")


def call_llm_json(
    system: str,
    prompt: str,
    provider: str | None = None,
    max_tokens: int = MAX_TOKENS,
    trace: dict | None = None,
    champs_requis: "tuple[str, ...]" = (),
) -> dict:
    """
    Comme call_llm, mais retourne le JSON parsé.

    Args:
        trace:         Renseigné avec le fournisseur ayant réellement répondu
        champs_requis: Clés que l'objet doit contenir. Une réponse qui en manque
                       est traitée comme un échec du fournisseur, et la chaîne de
                       repli continue — plutôt que de laisser passer un JSON
                       valide mais hors sujet.

    Raises:
        ValueError: Si la réponse n'est pas un JSON valide
    """
    trace = {} if trace is None else trace

    def _conforme(brut: str) -> bool:
        """Le contrat est-il rempli ? Sert de critère d'échec dans call_llm."""
        if not champs_requis:
            return True
        try:
            charge = json.loads(extract_json(brut))
        except (json.JSONDecodeError, TypeError):
            return False
        return isinstance(charge, dict) and all(charge.get(c) for c in champs_requis)
    raw = call_llm(system, prompt, provider=provider, max_tokens=max_tokens,
                   trace=trace, valider=_conforme)
    candidate = extract_json(raw)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        # Le fournisseur fautif est celui qui a répondu, pas celui demandé : après
        # un repli, ce sont deux noms différents.
        nom = trace.get("provider") or resolve_provider(provider).name
        dump = Path("data/processed/_llm_last_error.txt")
        try:
            dump.parent.mkdir(parents=True, exist_ok=True)
            dump.write_text(raw, encoding="utf-8")
            logger.error("Réponse brute sauvegardée dans %s", dump)
        except OSError:
            logger.debug("Impossible de sauvegarder la réponse brute")
        raise ValueError(
            f"{nom} n'a pas retourné un JSON valide : {e}"
        ) from e
