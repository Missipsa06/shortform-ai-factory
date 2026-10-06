"""
Classification des échecs d'appel aux fournisseurs : quota, surcharge, autre.

Un code 429 n'a pas une seule cause, et la bonne réaction n'est pas la même :

    quota par minute   attendre le délai annoncé puis réessayer — même
                       fournisseur, donc même qualité de sortie
    quota par jour     attendre est inutile, il faut changer de fournisseur
    503 / 504          surcharge passagère du service, un réessai suffit
                       le plus souvent

Sans cette distinction, le pipeline traite les trois cas de la même façon : il
s'arrête. Un quota par minute lui coûte alors une vidéo entière là où trente
secondes d'attente auraient suffi.

Google publie l'information nécessaire dans le corps de l'erreur
(`details[].violations[].quotaId` et `RetryInfo.retryDelay`). Anthropic et
Mistral ne la publient pas : le diagnostic retombe alors sur « par minute »,
l'hypothèse la moins coûteuse — un réessai perdu valant mieux qu'une vidéo
perdue.

Ce module ne dépend d'aucun autre module du projet : il est importable depuis
`content/` comme depuis `media/` sans créer de cycle.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Les quotas du palier gratuit de Google se rechargent à minuit, heure du
# Pacifique — et non 24 h après le premier appel.
FUSEAU_QUOTA = "America/Los_Angeles"

# Un pipeline ne doit jamais rester bloqué sur un délai annoncé par un tiers.
DELAI_MAX = 60.0

# Repli quand le fournisseur n'annonce aucun délai.
DELAI_DEFAUT = 5.0

_STATUTS_SURCHARGE = frozenset({500, 502, 503, 504})
_DUREE_RE = re.compile(r"([\d.]+)\s*s", re.IGNORECASE)


@dataclass(frozen=True)
class Diagnostic:
    """
    Verdict porté sur un échec d'appel.

    Attributes:
        genre:   'minute', 'jour', 'surcharge' ou 'autre'
        delai:   Secondes à attendre avant de réessayer (0 si un réessai est vain)
        limite:  Plafond annoncé par le fournisseur, quand il l'indique
        message: Formulation courte, prête à être journalisée
    """

    genre: str
    delai: float
    limite: str | None
    message: str

    @property
    def reessayable(self) -> bool:
        """Un réessai sur le même fournisseur a des chances d'aboutir."""
        return self.genre in ("minute", "surcharge")

    @property
    def epuise(self) -> bool:
        """Le quota du jour est consommé : seul un changement de moteur aide."""
        return self.genre == "jour"


# ── Lecture du corps de l'erreur ──────────────────────────────────────────────

def _premier_dict(charge: object) -> dict:
    """
    Déballe un corps d'erreur, qu'il soit un objet ou un tableau à un élément.

    **L'endpoint compatible OpenAI de Google enveloppe son erreur dans un
    tableau JSON.** Sans ce déballage, tout le contenu — `quotaId`, `retryDelay`,
    `violations` — était perdu et un quota *journalier* de Gemini se présentait
    comme une simple limite par minute. Conséquence mesurée : le pipeline
    attendait puis réessayait le même fournisseur épuisé pour la journée, au lieu
    de basculer sur le suivant. C'est l'inverse exact du tri que ce module existe
    pour faire.
    """
    if isinstance(charge, dict):
        return charge
    if isinstance(charge, list):
        for element in charge:
            if isinstance(element, dict):
                return element
    return {}


def _en_dict(corps: "str | dict | list | None") -> dict:
    """Normalise un corps d'erreur en dictionnaire, sans jamais lever."""
    if isinstance(corps, (dict, list)):
        return _premier_dict(corps)
    if not corps:
        return {}
    try:
        charge = json.loads(corps)
    except (ValueError, TypeError):
        return {}
    return _premier_dict(charge)


def _details(corps: dict) -> list[dict]:
    """Extrait `error.details`, la liste où Google range quota et délai."""
    erreur = corps.get("error")
    if not isinstance(erreur, dict):
        return []
    details = erreur.get("details")
    return [d for d in details if isinstance(d, dict)] if isinstance(details, list) else []


def _message(corps: dict) -> str:
    """
    Extrait le message d'erreur, quelle que soit la forme du corps.

    Deux conventions coexistent. Google et Moonshot imbriquent le texte dans
    `error.message` ; Mistral le place à la racine, à côté de `object: "error"`.
    N'en lire qu'une faisait disparaître des explications décisives — un compte
    suspendu se présentait comme une simple limite de débit.
    """
    erreur = corps.get("error")
    if isinstance(erreur, dict) and isinstance(erreur.get("message"), str):
        return erreur["message"]
    if isinstance(corps.get("message"), str):
        return corps["message"]
    return ""


def _duree(valeur: "str | None") -> float:
    """Convertit un délai façon protobuf (« 27s », « 1.5s ») en secondes."""
    if not valeur:
        return 0.0
    trouve = _DUREE_RE.search(str(valeur))
    return float(trouve.group(1)) if trouve else 0.0


def _quota_annonce(details: list[dict]) -> tuple[str, str | None]:
    """
    Retourne (identifiant de quota, plafond) tel que déclaré par le fournisseur.

    Plusieurs violations peuvent coexister ; la journalière prime, car c'est
    elle qui rend tout réessai inutile.
    """
    identifiant, plafond = "", None
    for detail in details:
        if not detail.get("@type", "").endswith("QuotaFailure"):
            continue
        for violation in detail.get("violations", []):
            if not isinstance(violation, dict):
                continue
            courant = str(violation.get("quotaId", ""))
            if "PerDay" in courant:
                return courant, violation.get("quotaValue")
            if not identifiant:
                identifiant, plafond = courant, violation.get("quotaValue")
    return identifiant, plafond


# ── Diagnostic ────────────────────────────────────────────────────────────────

def diagnostiquer(statut: "int | None", corps: "str | dict | None" = None) -> Diagnostic:
    """
    Qualifie un échec d'appel à partir de son code HTTP et de son corps.

    Args:
        statut: Code HTTP, ou None s'il n'a pas pu être déterminé
        corps:  Corps de la réponse (chaîne JSON ou dictionnaire déjà parsé)

    Returns:
        Le diagnostic correspondant
    """
    charge = _en_dict(corps)
    details = _details(charge)
    delai_annonce = 0.0
    for detail in details:
        if detail.get("@type", "").endswith("RetryInfo"):
            delai_annonce = _duree(detail.get("retryDelay"))
            break

    if statut in _STATUTS_SURCHARGE:
        return Diagnostic(
            genre="surcharge",
            delai=min(delai_annonce or DELAI_DEFAUT, DELAI_MAX),
            limite=None,
            message=f"service surchargé (HTTP {statut})",
        )

    if statut == 429:
        identifiant, plafond = _quota_annonce(details)
        texte = _message(charge)

        if "PerDay" in identifiant:
            plafond_txt = f" ({plafond} appels/jour)" if plafond else ""
            return Diagnostic(
                genre="jour",
                delai=0.0,
                limite=plafond,
                message=f"quota journalier épuisé{plafond_txt}",
            )

        # Tous les 429 ne sont pas des à-coups. Un compte sans crédit ou suspendu
        # répond 429 en permanence : réessayer y perd du temps à chaque appel.
        # Constaté sur Moonshot — « suspended due to insufficient balance » — et
        # sur Mistral, qui annonce une limite de zéro requête par minute.
        # Seulement quand le fournisseur ne nomme aucun quota : Google écrit
        # « check your plan and billing details » sur TOUS ses 429, limite par
        # minute comprise. Pris pour une suspension, ce texte arrêtait le pipeline
        # jusqu'au lendemain là où quelques secondes d'attente suffisaient.
        if not identifiant and any(
            marqueur in texte.lower()
            for marqueur in ("insufficient balance", "suspended", "recharge",
                             "exceeded_current_quota")
        ):
            return Diagnostic(
                genre="jour",
                delai=0.0,
                limite=None,
                message=f"compte non approvisionné ou suspendu : {texte[:150]}",
            )

        # Par minute, ou quota non identifié : on suppose le cas récupérable.
        # Se tromper coûte un réessai ; l'inverse coûterait la vidéo.
        plafond_txt = f" ({plafond}/min)" if plafond and "PerMinute" in identifiant else ""
        detail = f" : {texte[:120]}" if texte else ""
        return Diagnostic(
            genre="minute",
            delai=min(delai_annonce or DELAI_DEFAUT, DELAI_MAX),
            limite=plafond,
            message=f"limite de débit atteinte{plafond_txt}{detail}",
        )

    message = _message(charge)
    detail_txt = f" : {message[:200]}" if message else ""
    return Diagnostic(
        genre="autre",
        delai=0.0,
        limite=None,
        message=f"échec HTTP {statut}{detail_txt}" if statut else f"échec{detail_txt}",
    )


def depuis_exception(exc: BaseException) -> Diagnostic:
    """
    Qualifie l'exception d'un SDK sans dépendre du SDK concerné.

    Les clients Anthropic et OpenAI sont tous deux générés à partir d'un schéma
    et exposent la même forme : `status_code`, `body`, `response`. On la lit par
    duck typing plutôt qu'en important les deux paquets, dont un seul est
    forcément installé.

    Args:
        exc: Exception remontée par l'appel

    Returns:
        Le diagnostic correspondant
    """
    statut = getattr(exc, "status_code", None)
    if statut is None:
        reponse = getattr(exc, "response", None)
        statut = getattr(reponse, "status_code", None)

    corps = getattr(exc, "body", None)
    if corps is None:
        reponse = getattr(exc, "response", None)
        corps = getattr(reponse, "text", None)

    diag = diagnostiquer(statut, corps)

    # Aucun statut lisible : le message du SDK est la seule information dont on
    # dispose, et il ne doit pas être perdu en route.
    if statut is None and diag.genre == "autre":
        return Diagnostic(
            genre="autre", delai=0.0, limite=None, message=str(exc)[:250] or type(exc).__name__
        )
    return diag


# ── Réinitialisation des quotas journaliers ───────────────────────────────────

def prochaine_reinitialisation() -> str:
    """
    Heure locale à laquelle un quota journalier Google se recharge.

    À afficher de préférence au `retryDelay` renvoyé par l'API : celui-ci vaut
    quelques dizaines de secondes même sur un quota journalier, ce qui laisse
    croire qu'une courte attente suffirait.

    Returns:
        Formulation lisible, ou une mention générique si le fuseau est introuvable
    """
    try:
        from zoneinfo import ZoneInfo

        maintenant = datetime.now(ZoneInfo(FUSEAU_QUOTA))
    except Exception:  # tzdata absent : l'information reste utile sans l'heure locale
        logger.debug("Fuseau %s indisponible", FUSEAU_QUOTA)
        return "minuit, heure du Pacifique"

    minuit = (maintenant + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    locale = minuit.astimezone()
    restant = minuit - maintenant
    heures, minutes = divmod(int(restant.total_seconds()) // 60, 60)
    return f"{locale:%Hh%M} heure locale (dans {heures} h {minutes:02d})"
