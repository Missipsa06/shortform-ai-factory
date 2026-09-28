"""
Synthèse vocale française, avec deux moteurs interchangeables.

Choisi par TTS_PROVIDER dans .env :

    gemini  Gemini TTS — voix nettement plus naturelle, et surtout dirigeable :
            on décrit l'interprétation en français au lieu de régler trois
            molettes. Débit plus lent, donc vidéos plus longues à script égal.
    edge    edge-tts (Microsoft, gratuit, sans clé) — repli, et seule option
            hors ligne. Ses cinq voix françaises sont son plafond.

Usage :
    python -m media.tts --input data/processed/manuel_rag.json
    python -m media.tts --text "Bonjour." --output data/audio/test.mp3 --provider gemini
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import edge_tts
import requests
from dotenv import load_dotenv

from content.quota import Diagnostic, diagnostiquer, prochaine_reinitialisation
from media.ffmpeg_utils import ffmpeg as find_ffmpeg

load_dotenv()
logger = logging.getLogger(__name__)

FOURNISSEURS = ("edge", "gemini", "nvidia")
FOURNISSEUR_DEFAUT = "gemini"

# Contrairement au LLM, le repli est ici **désactivé par défaut**. La voix est un
# choix éditorial : basculer d'office sur un autre moteur fabriquerait des
# vidéos dans une voix qui n'a pas été retenue, et le défaut ne s'entendrait
# qu'à la lecture. À activer (TTS_FALLBACK=1) pendant le travail sur le visuel,
# où la voix importe peu.
#
# La valeur peut aussi nommer le moteur de repli : "1"/"true"/... retient edge
# (comportement historique, gratuit et hors ligne), "nvidia" bascule plutôt sur
# magpie-tts-multilingual — utile pour garder une voix hébergée plutôt que
# tomber sur les cinq voix d'edge quand Gemini est à quota.
FALLBACK_ENV = "TTS_FALLBACK"
_VRAI = frozenset({"1", "true", "oui", "yes", "on"})
_MOTEURS_REPLI = frozenset({"edge", "nvidia"})
MOTEUR_REPLI_DEFAUT = "edge"


class QuotaEpuise(RuntimeError):
    """
    Quota journalier du moteur épuisé : attendre est inutile, il faut en changer.

    Distinguée d'un RuntimeError ordinaire pour que l'appelant puisse décider du
    repli, ce que le moteur lui-même n'a pas à faire.
    """

    def __init__(self, message: str, diagnostic: Diagnostic) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic

# ── edge-tts ──────────────────────────────────────────────────────────────────
DEFAULT_VOICE = "fr-FR-VivienneMultilingualNeural"  # Voix naturelle et expressive
VOICES = {
    "vivienne": "fr-FR-VivienneMultilingualNeural",  # Féminine, très naturelle
    "remy":     "fr-FR-RemyMultilingualNeural",      # Masculine, moderne
    "denise":   "fr-FR-DeniseNeural",                # Féminine, standard
    "henri":    "fr-FR-HenriNeural",                 # Masculine, standard
}

# Voix de repli quand Gemini est indisponible : masculine comme Charon, et
# multilingue, donc capable de prononcer les termes anglais sans épellation.
VOIX_REPLI = VOICES["remy"]

# ── Gemini TTS ────────────────────────────────────────────────────────────────
GEMINI_VOIX_DEFAUT = "Charon"
GEMINI_MODELE_DEFAUT = "gemini-3.1-flash-tts-preview"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modele}:generateContent"

# Le modèle renvoie du PCM brut : ces valeurs servent à le convertir en MP3.
PCM_TAUX = 24000
PCM_CANAUX = 1
DEBIT_MP3 = "128k"

# ── NVIDIA NIM (Magpie TTS) ──────────────────────────────────────────────────
# Alternative gratuite quand le quota Gemini (10 appels/jour) est épuisé. Modèle
# hébergé par NVIDIA, mais accessible en gRPC via le client Riva — contrairement
# aux deux autres moteurs, qui parlent HTTP/JSON en clair. Le function-id
# identifie le modèle sur l'infrastructure NVIDIA Cloud Functions ; il n'y a pas
# d'URL REST équivalente pour l'API hébergée gratuite.
NVIDIA_URI = "grpc.nvcf.nvidia.com:443"
NVIDIA_FUNCTION_ID = "877104f7-e885-42b9-8de8-f6e4c6303969"  # magpie-tts-multilingual
# Le français n'a que deux voix (RivaSpeechSynthesisStub.GetRivaSynthesisConfig,
# non exposé par le client Python — interrogé directement en gRPC) : "Louise" et
# "Pascal" (ce dernier avec des variantes Neutral/Calm/Happy/Sad). Les autres
# noms du reste du catalogue (Sofia, Aria, Jason...) n'existent qu'en anglais.
NVIDIA_VOIX_DEFAUT = "Louise"
NVIDIA_LANGUE = "fr-FR"
NVIDIA_TAUX = 22050
# Les deux seules voix FR-FR confirmées (cf. commentaire ci-dessus).
NVIDIA_VOIX_DISPONIBLES = ["Louise", "Pascal"]

# Plafond d'entrée du serveur Magpie, non documenté. **Il ne se calcule pas
# depuis le client** : sur un texte de 1 925 caractères et 1 964 octets UTF-8, le
# serveur a répondu « 2040 > 2000 ». Il compte donc après sa propre normalisation
# de texte, celle qui développe nombres et abréviations avant la synthèse. Ce
# garde-fou n'est qu'un dernier filet, la vraie parade étant le lotissement
# ci-dessous, dont le budget garde 30 % de marge.
NVIDIA_MAX_CARACTERES = 2000


@dataclass(frozen=True)
class Lotissement:
    """
    Comment réunir plusieurs segments dans un même appel de synthèse.

    Les deux moteurs groupés échouent pour des raisons opposées, et chacun n'est
    contraint que par une des deux dimensions. NVIDIA déborde en **longueur**.
    Gemini refusait la narration entière à cause des **sauts de ligne multiples**,
    un par frontière de segment. Une seule mécanique, deux bornes, chaque moteur
    ne serrant que celle qui le concerne.

    Attributes:
        max_segments:   Segments réunis au plus dans un appel
        max_caracteres: Longueur cumulée au plus, séparateurs compris, **et
                        `surcharge` comprise** : le budget vaut ce que le
                        serveur verra, pas ce que le script contient
        surcharge:      Caractères ajoutés à la requête après le lotissement et
                        que le lotisseur ne voit donc pas. Chez Gemini, c'est
                        la consigne de jeu préfixée par `_synthetiser_gemini`,
                        266 à 299 caractères selon le type de segment. Les
                        ignorer faisait sous-compter le budget d'un tiers, et
                        c'est ainsi qu'un lot annoncé sous la limite la
                        dépassait réellement.
    """

    max_segments: int
    max_caracteres: int
    surcharge: int = 0


# Un appel unique pour toute la narration cassait chez les deux fournisseurs
# groupés ; un appel par segment consomme neuf requêtes sur les dix du palier
# gratuit quotidien de Gemini. Le lot est le milieu : deux à trois appels.
LOTISSEMENTS: dict[str, Lotissement] = {
    # 1 400 et non 2 000 : le serveur compte après normalisation, donc la marge
    # absorbe ce qu'on ne sait pas prévoir.
    "nvidia": Lotissement(max_segments=99, max_caracteres=1400),
    # Trois segments au plus, donc deux sauts de ligne par appel — la borne qui
    # avait motivé le lotissement chez Gemini.
    #
    # Le budget en caractères valait 6 000, donc n'était jamais atteint : seul
    # le nombre de segments mordait. Mesuré le 22/09/2026 sur
    # `gemini-3.1-flash-tts-preview` : trois segments font ~800 caractères et le
    # modèle répond alors 400 INVALID_ARGUMENT, de façon reproductible, alors
    # que chacun passe isolément et que 750 caractères passent. D'où 700, seule
    # borne qui compte désormais, consigne de jeu comprise.
    "gemini": Lotissement(max_segments=3, max_caracteres=700, surcharge=300),
}
LOTISSEMENT_DEFAUT = Lotissement(max_segments=99, max_caracteres=6000)


def _lots(segments: list[tuple], fournisseur: str) -> list[list[tuple]]:
    """
    Regroupe les segments en lots synthétisables chacun d'un seul appel.

    Un segment plus long que le budget part seul : le découper romprait la
    correspondance entre un segment de script et une tranche d'audio, sur
    laquelle repose toute la découpe qui suit.

    Args:
        segments:     [(identifiant, texte, type)] dans l'ordre de narration
        fournisseur:  Moteur retenu, qui choisit les bornes

    Returns:
        Les lots, dans l'ordre ; leur concaténation redonne `segments`
    """
    bornes = LOTISSEMENTS.get(fournisseur, LOTISSEMENT_DEFAUT)
    # Le budget est celui de la requête, pas celui du script : ce que le moteur
    # ajoutera après coup est décompté d'emblée.
    budget = max(1, bornes.max_caracteres - bornes.surcharge)
    lots: list[list[tuple]] = []
    courant: list[tuple] = []
    longueur = 0

    for segment in segments:
        taille = len(segment[1]) + (1 if courant else 0)   # +1 pour le séparateur
        trop_long = longueur + taille > budget
        trop_nombreux = len(courant) >= bornes.max_segments
        if courant and (trop_long or trop_nombreux):
            lots.append(courant)
            courant, longueur = [], 0
            taille = len(segment[1])
        courant.append(segment)
        longueur += taille

    if courant:
        lots.append(courant)
    return lots

# Liste des ~30 voix préréglées de Gemini TTS. Contrairement aux deux autres
# listes de ce module, non vérifiée en direct contre l'API (elle le serait au
# prix d'un appel par nom, soit tout le quota gratuit du jour) : reprise de la
# documentation Google, à jour au moment de l'écriture. Un nom devenu invalide
# se traduira par une erreur claire de l'API, pas par un échec silencieux.
GEMINI_VOIX_DISPONIBLES = [
    "Achernar", "Achird", "Algenib", "Algieba", "Alnilam", "Aoede", "Autonoe",
    "Callirrhoe", "Charon", "Despina", "Enceladus", "Erinome", "Fenrir",
    "Gacrux", "Iapetus", "Kore", "Laomedeia", "Leda", "Orus", "Puck",
    "Pulcherrima", "Rasalgethi", "Sadachbia", "Sadaltager", "Schedar",
    "Sulafat", "Umbriel", "Vindemiatrix", "Zephyr", "Zubenelgenubi",
]

AUDIO_DIR = Path("data/audio")


def voix_disponibles() -> dict[str, list[str]]:
    """
    Voix connues par moteur, pour les interfaces qui les proposent au choix
    (dashboard Streamlit, CLI). Source unique : modifier une liste ici suffit à
    la répercuter partout, plutôt que de la dupliquer dans chaque appelant.
    """
    return {
        "edge": sorted(VOICES),
        "gemini": GEMINI_VOIX_DISPONIBLES,
        "nvidia": NVIDIA_VOIX_DISPONIBLES,
    }


def resoudre_fournisseur(explicite: str | None = None) -> str:
    """
    Détermine le moteur de synthèse à utiliser.

    Raises:
        ValueError: Si le nom demandé est inconnu
    """
    nom = (explicite or os.getenv("TTS_PROVIDER") or FOURNISSEUR_DEFAUT).strip().lower()
    if nom not in FOURNISSEURS:
        raise ValueError(
            f"Moteur TTS inconnu : '{nom}'. Valeurs possibles : {', '.join(FOURNISSEURS)}"
        )
    return nom


# Module tiers chargé au dernier moment par un moteur, quand il y en a un.
# `edge` et `gemini` passent par des dépendances importées en tête de module :
# leur absence se voit à l'import, donc immédiatement. Seul NVIDIA charge son
# client gRPC au moment de synthétiser, d'où ce tableau — et la seule entrée
# qu'il contienne aujourd'hui. Un futur moteur à client lourd s'y ajoute sans
# rien changer d'autre.
MODULE_REQUIS: dict[str, str] = {"nvidia": "riva.client"}


def dependance_manquante(fournisseur: str) -> str:
    """
    Nom du module absent pour ce moteur ; chaîne vide si tout est en place.

    **À appeler avant de produire, pas au moment de synthétiser.** Sans contrôle
    préalable, l'absence ne se manifeste qu'à l'étape 3, après le scraping et un
    appel LLM facturé — c'est exactement ce qui est arrivé le 16/09/2026. Même
    leçon que `_whisper_available` d'`automation/workflow.py`, appliquée au même
    endroit du pipeline et non seulement au point d'usage.

    Args:
        fournisseur: Nom du moteur ('edge', 'gemini', 'nvidia')

    Returns:
        Le nom du module manquant, ou '' si le moteur est utilisable.
    """
    import importlib.util

    module = MODULE_REQUIS.get(fournisseur, "")
    if not module:
        return ""
    try:
        present = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        # `find_spec` sur un sous-module importe son paquet parent : quand
        # celui-ci manque, l'appel lève au lieu de rendre None. Ici l'échec de
        # la recherche est une réponse, pas une anomalie — d'où le filet plutôt
        # qu'un `if`. Sonder coûte une milliseconde ; importer `riva.client`
        # pour de bon en coûte 400.
        present = False
    return "" if present else module


def message_dependance(fournisseur: str, module: str) -> str:
    """
    Message unique pour un client de synthèse introuvable.

    Partagé par le contrôle préalable et par le garde-fou au point d'usage, pour
    que les deux disent la même chose. Le message insiste sur l'interpréteur :
    ces modules sont des dépendances déclarées et verrouillées, donc leur absence
    signale presque toujours un `python` hérité d'un autre environnement plutôt
    qu'une installation incomplète.
    """
    return (
        f"Le moteur TTS '{fournisseur}' a besoin de '{module}', introuvable dans "
        f"cet interpréteur ({sys.executable}). C'est pourtant une dépendance "
        "déclarée : l'exécution ne passe probablement pas par le venv du projet. "
        "Lancer par `uv run …` ou `make` ; `uv sync` réinstalle au besoin. "
        "Sortie immédiate sans rien installer : --tts-provider edge."
    )


def _consigne_jeu(segment_type: str) -> str:
    """
    Consigne d'interprétation envoyée au modèle, en langage naturel.

    C'est l'apport principal de Gemini sur edge-tts : on décrit le jeu attendu
    au lieu de régler une vitesse et une hauteur.
    """
    nuances = {
        "hook": "avec une accroche vive et intriguée qui donne envie d'écouter la suite",
        "conclusion": "sur un ton chaleureux qui invite à réagir",
    }
    nuance = nuances.get(segment_type, "posé et pédagogue, en articulant bien")
    # Le texte commence sur sa propre ligne, après une ligne vide. Collé à la
    # suite de « … cette consigne : », le modèle prenait sa première phrase pour
    # la fin de l'instruction et ne la prononçait pas — en synthèse groupée,
    # c'était l'accroche entière qui disparaissait de chaque vidéo, sans erreur
    # ni avertissement. Vérifié : 1 mot significatif du hook sur 6 subsistait.
    return (
        "Lis ce texte en français comme un vulgarisateur enthousiaste qui "
        f"s'adresse à un ami, {nuance}.\n"
        "Ne prononce pas cette consigne. Le texte à lire commence après la ligne "
        "vide ci-dessous, et doit être lu intégralement, dès son premier mot.\n\n"
    )


def _synthetiser_gemini(
    texte: str,
    sortie: Path,
    voix: str,
    segment_type: str = "content",
    reessais: int = 1,
) -> None:
    """
    Synthétise un texte via Gemini TTS et l'enregistre en MP3.

    Le modèle renvoie du PCM brut ; FFmpeg le convertit au format attendu par le
    reste du pipeline, qui concatène les segments sans les ré-encoder.

    Args:
        reessais: Nombre de réessais autorisés sur un échec passager (limite de
                  débit, surcharge). Un quota journalier n'est jamais réessayé.

    Raises:
        EnvironmentError: Clé API manquante
        QuotaEpuise:      Quota journalier consommé
        RuntimeError:     Échec de l'appel ou de la conversion
    """
    cle = os.getenv("GEMINI_API_KEY", "").strip()
    if not cle:
        raise EnvironmentError(
            "GEMINI_API_KEY manquant dans .env — nécessaire pour le moteur 'gemini'. "
            "Obtiens une clé sur https://aistudio.google.com/apikey"
        )

    modele = os.getenv("GEMINI_TTS_MODEL") or GEMINI_MODELE_DEFAUT
    corps = {
        "contents": [{"parts": [{"text": _consigne_jeu(segment_type) + texte}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voix}}
            },
        },
    }

    reponse = requests.post(
        GEMINI_URL.format(modele=modele),
        headers={"x-goog-api-key": cle},
        json=corps,
        timeout=180,
    )
    if reponse.status_code != 200:
        diag = diagnostiquer(reponse.status_code, reponse.text)

        if diag.reessayable and reessais > 0:
            logger.warning(
                "Gemini TTS : %s — nouvelle tentative dans %.0f s",
                diag.message, diag.delai,
            )
            time.sleep(diag.delai)
            return _synthetiser_gemini(texte, sortie, voix, segment_type, reessais - 1)

        if diag.epuise:
            # Le retryDelay renvoyé par l'API vaut quelques dizaines de secondes
            # même sur un quota journalier : l'afficher laisserait croire qu'une
            # courte attente suffit.
            raise QuotaEpuise(
                f"Gemini TTS : {diag.message}. "
                f"Réinitialisation vers {prochaine_reinitialisation()}. "
                f"Pour produire malgré tout avec edge-tts : TTS_FALLBACK=1",
                diag,
            )

        raise RuntimeError(
            f"Gemini TTS a échoué ({reponse.status_code}) : {reponse.text[:300]}"
        )

    part = reponse.json()["candidates"][0]["content"]["parts"][0]
    inline = part.get("inlineData") or part.get("inline_data")
    if not inline:
        raise RuntimeError("Gemini TTS n'a retourné aucun audio")

    pcm = base64.b64decode(inline["data"])
    sortie.parent.mkdir(parents=True, exist_ok=True)
    resultat = subprocess.run(
        [find_ffmpeg(), "-y", "-f", "s16le", "-ar", str(PCM_TAUX),
         "-ac", str(PCM_CANAUX), "-i", "pipe:0", "-b:a", DEBIT_MP3, str(sortie)],
        input=pcm, capture_output=True,
    )
    if resultat.returncode != 0:
        raise RuntimeError(
            f"Conversion PCM → MP3 échouée : {resultat.stderr.decode(errors='replace')[-300:]}"
        )


def _synthetiser_nvidia(texte: str, sortie: Path, voix: str) -> None:
    """
    Synthétise un texte via magpie-tts-multilingual (NVIDIA NIM hébergé).

    Contrairement à Gemini, ce moteur ne prend aucune consigne de jeu en langage
    naturel : c'est un TTS paramétrique classique — texte, voix, langue — sans
    direction d'interprétation possible.

    Casse à ne pas harmoniser : le nom de voix embarque le code langue en
    MAJUSCULES ("Magpie-Multilingual.FR-FR.Louise", format des "subvoices" du
    serveur), alors que le champ language_code de la requête veut la casse
    BCP-47 classique ("fr-FR"). Confirmé en interrogeant GetRivaSynthesisConfig
    en gRPC direct — cette RPC existe côté serveur mais n'est pas exposée par
    SpeechSynthesisService, qui ne propose que synthesize/synthesize_online.

    Raises:
        EnvironmentError: Clé API manquante
        RuntimeError:     Échec de l'appel ou de la conversion
    """
    # Dernier filet. `run_pipeline` interroge `dependance_manquante()` avant
    # l'étape 1, donc ce chemin n'est atteint que par les appelants qui sautent
    # le contrôle préalable — la CLI du module, le dashboard, un test. Le
    # `ModuleNotFoundError` nu qui passait ici ne nommait ni l'interpréteur
    # fautif, ni la sortie immédiate.
    try:
        import riva.client
    except ImportError as e:
        raise RuntimeError(message_dependance("nvidia", "riva.client")) from e

    cle = os.getenv("NVIDIA_API_KEY", "").strip()
    if not cle:
        raise EnvironmentError(
            "NVIDIA_API_KEY manquant dans .env — nécessaire pour le moteur 'nvidia'. "
            "Clé gratuite sur https://build.nvidia.com/nvidia/magpie-tts-multilingual"
        )

    # Le serveur refuse au-delà de 2000 caractères, et le dit par une erreur
    # gRPC brute noyée dans une trace de trente lignes. Le contrôle a lieu ici
    # pour nommer la cause et la sortie, avant de payer l'aller-retour réseau.
    if len(texte) > NVIDIA_MAX_CARACTERES:
        raise RuntimeError(
            f"NVIDIA TTS plafonne l'entrée à {NVIDIA_MAX_CARACTERES} caractères, "
            f"or le texte en fait {len(texte)}. La narration groupée d'un script "
            f"de 6 slides atteint déjà cette limite : utiliser --tts-provider edge, "
            f"qui synthétise segment par segment, ou raccourcir le script."
        )

    auth = riva.client.Auth(
        uri=NVIDIA_URI,
        use_ssl=True,
        metadata_args=[
            ["function-id", NVIDIA_FUNCTION_ID],
            ["authorization", f"Bearer {cle}"],
        ],
    )
    service = riva.client.SpeechSynthesisService(auth)

    try:
        reponse = service.synthesize(
            texte,
            voice_name=f"Magpie-Multilingual.{NVIDIA_LANGUE.upper()}.{voix}",
            language_code=NVIDIA_LANGUE,
            encoding=riva.client.AudioEncoding.LINEAR_PCM,
            sample_rate_hz=NVIDIA_TAUX,
        )
    except Exception as e:
        raise RuntimeError(f"NVIDIA TTS a échoué : {e}") from e

    sortie.parent.mkdir(parents=True, exist_ok=True)
    resultat = subprocess.run(
        [find_ffmpeg(), "-y", "-f", "s16le", "-ar", str(NVIDIA_TAUX),
         "-ac", "1", "-i", "pipe:0", "-b:a", DEBIT_MP3, str(sortie)],
        input=reponse.audio, capture_output=True,
    )
    if resultat.returncode != 0:
        raise RuntimeError(
            f"Conversion PCM → MP3 échouée : {resultat.stderr.decode(errors='replace')[-300:]}"
        )


def _get_prosody(segment_type: str) -> tuple[str, str, str]:
    """Retourne (rate, pitch, volume) selon le type de segment."""
    if segment_type == "hook":
        return "-5%", "+5Hz", "+10%"
    if segment_type == "conclusion":
        return "-5%", "+0Hz", "+0%"
    return "-10%", "+0Hz", "+0%"


def _clean_text(text: str) -> str:
    """
    Nettoie un texte avant synthèse vocale :
    supprime les emojis, hashtags, URLs et caractères non prononçables.
    """
    import unicodedata
    # Supprime les hashtags (#IA, #DataScience...)
    text = re.sub(r"#\S+", "", text)
    # Supprime les URLs
    text = re.sub(r"https?://\S+", "", text)
    # Supprime les emojis et symboles Unicode non-ASCII non prononçables
    cleaned = []
    for ch in text:
        cat = unicodedata.category(ch)
        # Garde lettres, chiffres, ponctuation, espaces
        if cat.startswith(("L", "N", "P", "Z")) or ch in "+-=%<>":
            cleaned.append(ch)
    text = "".join(cleaned)
    # Normalise les espaces multiples
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_script_text(script: dict) -> list[tuple[str, str, str]]:
    """
    Construit la liste des segments audio à générer depuis un script JSON.
    Chaque texte est nettoyé (sans emojis, hashtags, metadata).

    Returns:
        Liste de tuples (segment_id, texte, type) : hook + slides + conclusion
    """
    segments: list[tuple[str, str, str]] = []

    # L'ordre suit celui de content.slides.plan_slides : l'accroche ouvre la
    # vidéo, la présentation du sujet vient ensuite.
    hook = _clean_text(script.get("hook", ""))
    if hook:
        segments.append(("hook", hook, "hook"))

    # Facultative : les scripts antérieurs à son introduction n'en ont pas.
    introduction = _clean_text(script.get("introduction", ""))
    if introduction:
        segments.append(("intro", introduction, "intro"))

    for slide in script.get("slides", []):
        numero = slide.get("numero", len(segments))
        titre = _clean_text(slide.get("titre", ""))
        contenu = _clean_text(slide.get("contenu", ""))
        texte = f"{titre}. {contenu}".strip(" .")
        if texte:
            segments.append((f"slide_{numero:02d}", texte, "content"))

    conclusion = _clean_text(script.get("conclusion", ""))
    if conclusion:
        segments.append(("conclusion", conclusion, "conclusion"))

    return segments


def _fix_pronunciation(text: str, en_terms: list[str]) -> str:
    """
    Améliore la prononciation des termes techniques sans SSML — edge-tts seulement.

    Ce contournement n'est appliqué qu'au moteur edge : il s'entend (« B. E. R. T. »)
    et n'a de raison d'être que parce qu'edge-tts n'expose pas de balise de
    prononciation. Gemini reçoit le texte intact.

    - Les acronymes tout-majuscules (BERT, GPT, LLM) sont espacés lettre par lettre
    - Les autres termes anglais sont laissés tels quels (VivienneMultilingual les prononce bien)
    """
    if not en_terms:
        return text

    sorted_terms = sorted(en_terms, key=len, reverse=True)
    for term in sorted_terms:
        if term.isupper() and len(term) > 1:
            # Acronyme : "BERT" → "B. E. R. T." pour forcer l'épellation
            spelled = ". ".join(list(term)) + "."
            text = re.sub(r"\b" + re.escape(term) + r"\b", spelled, text)
    return text


async def _synthesize(
    text: str,
    output_path: Path,
    voice: str,
    segment_type: str = "content",
    en_terms: list[str] | None = None,
) -> None:
    """Synthétise un texte en MP3 via edge-tts (texte brut, prononciation corrigée)."""
    rate, pitch, volume = _get_prosody(segment_type)
    content = _fix_pronunciation(text, en_terms or [])
    communicate = edge_tts.Communicate(text=content, voice=voice, rate=rate, pitch=pitch, volume=volume)
    await communicate.save(str(output_path))


def _synthetiser(
    texte: str,
    sortie: Path,
    voix: str,
    segment_type: str,
    en_terms: list[str],
    fournisseur: str,
) -> None:
    """Aiguille vers le moteur choisi."""
    if fournisseur == "gemini":
        _synthetiser_gemini(texte, sortie, voix, segment_type)
    elif fournisseur == "nvidia":
        _synthetiser_nvidia(texte, sortie, voix)
    else:
        asyncio.run(_synthesize(texte, sortie, voix, segment_type, en_terms))


def _synthetiser_ou_scinder(
    lot: list[tuple],
    cible: Path,
    voix: str,
    termes: list[str],
    fournisseur: str,
    travail: Path,
    rang: int,
) -> None:
    """
    Synthétise un lot, en le coupant en deux si le serveur le refuse.

    **Pourquoi ce filet en plus d'un budget correct.** Le budget de
    `LOTISSEMENTS` est mesuré contre un modèle donné, à une date donnée — et
    celui de Gemini est une *préversion*, dont les bornes changent sans
    préavis. C'est exactement ce qui vient d'arriver : un budget établi sur un
    modèle antérieur laissait passer des lots que le suivant refuse. Sans ce
    filet, chaque révision du modèle coûte une vidéo et une session de débogage.

    La coupe préserve la règle qui compte : **on ne change pas de moteur**. Les
    deux moitiés sont dites par la même voix, et le mélange de voix dans une
    même bande son — défaut qui ne s'entend qu'à la lecture — reste impossible.

    Un lot d'un seul segment ne se coupe pas : le découper romprait la
    correspondance entre un segment de script et une tranche d'audio, sur
    laquelle repose toute la découpe qui suit. L'erreur remonte alors telle
    quelle.
    """
    texte = "\n".join(t for _, t, _ in lot)
    try:
        _synthetiser(texte, cible, voix, "content", termes, fournisseur)
        return
    except (QuotaEpuise, EnvironmentError):
        raise                       # traités par l'appelant, pas par une coupe
    except Exception as e:
        if len(lot) == 1:
            raise
        logger.warning(
            "Lot %d refusé (%d segments, %d caractères) : %s — nouvelle "
            "tentative en deux moitiés", rang + 1, len(lot), len(texte), e,
        )

    milieu = len(lot) // 2
    moities = [lot[:milieu], lot[milieu:]]
    morceaux: list[Path] = []
    for i, moitie in enumerate(moities):
        part = travail / f"lot_{rang:02d}_{i}.mp3"
        _synthetiser_ou_scinder(
            moitie, part, voix, termes, fournisseur, travail, rang
        )
        morceaux.append(part)
    concatenate_segments(morceaux, cible)


def _voix_par_defaut(fournisseur: str) -> str:
    """Voix retenue si l'appelant n'en impose pas."""
    if fournisseur == "gemini":
        return os.getenv("GEMINI_TTS_VOICE") or GEMINI_VOIX_DEFAUT
    if fournisseur == "nvidia":
        return os.getenv("NVIDIA_TTS_VOICE") or NVIDIA_VOIX_DEFAUT
    return DEFAULT_VOICE


def _repli_actif(explicite: "bool | None" = None) -> bool:
    """
    Indique si la bascule automatique vers un moteur de repli est autorisée.

    Args:
        explicite: Décision de l'appelant ; None = lit TTS_FALLBACK dans .env
    """
    if explicite is not None:
        return explicite
    valeur = os.getenv(FALLBACK_ENV, "").strip().lower()
    return valeur in _VRAI or valeur in _MOTEURS_REPLI


def _moteur_repli() -> str:
    """Moteur de repli désigné par TTS_FALLBACK ; edge par défaut si absent ou booléen."""
    valeur = os.getenv(FALLBACK_ENV, "").strip().lower()
    return valeur if valeur in _MOTEURS_REPLI else MOTEUR_REPLI_DEFAUT


def _basculer(cause: QuotaEpuise) -> tuple[str, str, list[str]]:
    """
    Journalise la dégradation et retourne le moteur de repli, sa voix, ses termes.

    Le message est en WARNING et nomme la voix effectivement utilisée : une
    bascule silencieuse produirait une vidéo qui paraît normale mais n'a pas la
    voix choisie, ce qui ne se découvre qu'à l'écoute.

    La liste de termes anglais retournée est vide à dessein : l'épellation des
    acronymes (« B. E. R. T. ») ne sert qu'aux voix edge monolingues ; les voix
    de repli (edge multilingue, nvidia) prononcent l'anglais nativement.

    La voix edge de repli (VOIX_REPLI) est délibérément fixe — masculine et
    multilingue, choisie pour ce rôle précis, indépendamment de la voix edge
    principale. Nvidia n'a pas cette distinction : sa voix de repli est celle
    configurée par NVIDIA_TTS_VOICE, pas une constante figée — l'ignorer ferait
    parler « Louise » à qui a choisi « Pascal ».

    Returns:
        (moteur, voix, termes anglais à épeler)
    """
    logger.warning("%s", cause)
    moteur = _moteur_repli()
    voix = _voix_par_defaut("nvidia") if moteur == "nvidia" else VOIX_REPLI
    logger.warning(
        "Repli sur %s / %s — la voix ne sera PAS celle demandée", moteur, voix
    )
    return moteur, voix, []


def generate_audio_segments(
    script: dict,
    output_dir: Path,
    voice: str = "",
    provider: str | None = None,
    fallback: "bool | None" = None,
) -> list[Path]:
    """
    Génère un fichier MP3 par segment (hook, slides, conclusion).

    Args:
        script:     Script JSON généré par llm.py
        output_dir: Dossier de destination des MP3
        voice:      Voix à utiliser ; vide = celle du moteur par défaut
        provider:   Moteur TTS ; None = TTS_PROVIDER dans .env puis défaut
        fallback:   Autoriser la bascule sur un moteur de repli ; None = TTS_FALLBACK

    Returns:
        Liste ordonnée des fichiers MP3 générés
    """
    fournisseur = resoudre_fournisseur(provider)
    voice = voice or _voix_par_defaut(fournisseur)
    # Les raccourcis (« vivienne ») n'ont de sens que pour edge : résolus ici,
    # les appelants n'ont pas à savoir quel moteur est actif.
    if fournisseur == "edge":
        voice = VOICES.get(voice, voice)
    output_dir.mkdir(parents=True, exist_ok=True)
    segments = build_script_text(script)
    en_terms: list[str] = script.get("termes_anglais", [])

    if not segments:
        logger.warning("Aucun segment audio à générer")
        return []

    # L'épellation des acronymes ne concerne qu'edge-tts : Gemini reçoit le texte
    # intact et prononce les termes anglais nativement.
    if en_terms and fournisseur == "edge":
        logger.info("Termes anglais épelés pour edge-tts : %s", en_terms)

    logger.info(
        "Génération de %d segments audio (moteur: %s, voix: %s)",
        len(segments), fournisseur, voice,
    )
    audio_files: list[Path] = []

    for i, (seg_id, text, seg_type) in enumerate(segments, start=1):
        output_path = output_dir / f"{seg_id}.mp3"
        try:
            try:
                _synthetiser(text, output_path, voice, seg_type, en_terms, fournisseur)
            except QuotaEpuise as e:
                # Bascule tolérée uniquement tant qu'aucun segment n'existe.
                # Changer de moteur en cours de route mélangerait deux voix dans
                # la même bande son — c'est arrivé, et la vidéo produite
                # paraissait normale.
                if audio_files or not _repli_actif(fallback):
                    raise
                fournisseur, voice, en_terms = _basculer(e)
                _synthetiser(text, output_path, voice, seg_type, en_terms, fournisseur)

            logger.info("  ✓ %s", output_path.name)
            audio_files.append(output_path)
        except Exception as e:
            # Interruption immédiate : poursuivre concaténerait une bande son
            # amputée, et la vidéo produite paraîtrait normale tout en ayant
            # perdu des slides. Sur un quota épuisé, les appels suivants
            # échoueraient de toute façon.
            raise RuntimeError(
                f"Synthèse interrompue au segment '{seg_id}' ({i}/{len(segments)}) : {e}\n"
                f"Les {len(audio_files)} segments déjà produits sont conservés dans "
                f"{output_dir}, mais la bande son est incomplète et inutilisable en l'état."
            ) from e

    return audio_files


def _decouper(source: Path, debut: float, fin: float, sortie: Path) -> Path:
    """Extrait un intervalle de la bande son dans un fichier MP3 distinct."""
    sortie.parent.mkdir(parents=True, exist_ok=True)
    cmd = [find_ffmpeg(), "-y", "-ss", f"{debut:.3f}"]
    if fin is not None:
        cmd += ["-to", f"{fin:.3f}"]
    cmd += ["-i", str(source), "-b:a", DEBIT_MP3, str(sortie)]

    resultat = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if resultat.returncode != 0:
        raise RuntimeError(
            f"Découpe audio échouée pour {sortie.name} : {(resultat.stderr or '')[-300:]}"
        )
    return sortie


def generer_audio_groupe(
    script: dict,
    output_dir: Path,
    final_path: Path,
    voice: str = "",
    provider: str | None = None,
    taille_modele: str = "base",
    fallback: "bool | None" = None,
) -> tuple[Path, list[Path]]:
    """
    Synthétise toute la narration en **un seul appel**, puis la redécoupe par slide.

    Motivation : les fournisseurs facturent et limitent à la requête. Dix segments
    consommaient dix requêtes par vidéo, soit la totalité d'un quota gratuit
    quotidien. Ici, une requête suffit.

    Les frontières entre slides ne sont pas devinées : le texte de chaque segment
    est connu, donc son nombre de mots aussi. Le calage mot à mot (media/align.py)
    date chaque mot de la narration complète, et la coupe tombe au milieu du
    silence qui sépare deux segments.

    Args:
        script:        Script JSON du sujet
        output_dir:    Dossier des segments à produire
        final_path:    Chemin de la bande son complète
        voice:         Voix ; vide = celle du moteur
        provider:      Moteur TTS ; None = TTS_PROVIDER puis défaut
        taille_modele: Modèle de transcription utilisé pour le calage
        fallback:      Autoriser la bascule sur un moteur de repli ; None = TTS_FALLBACK

    Returns:
        (bande son complète, segments découpés dans l'ordre)
    """
    from media.align import caler_mots

    fournisseur = resoudre_fournisseur(provider)
    voice = voice or _voix_par_defaut(fournisseur)
    if fournisseur == "edge":
        voice = VOICES.get(voice, voice)

    segments = build_script_text(script)
    if not segments:
        raise ValueError("Aucun segment à synthétiser")

    # Un saut de ligne entre segments : le modèle y marque une pause, ce qui
    # créera le silence dans lequel on coupera ensuite.
    texte_complet = "\n".join(texte for _, texte, _ in segments)

    lots = _lots(segments, fournisseur)
    logger.info(
        "Synthèse groupée : %d segments en %d appel(s) (moteur: %s, voix: %s, "
        "%d caractères)",
        len(segments), len(lots), fournisseur, voice, len(texte_complet),
    )

    final_path.parent.mkdir(parents=True, exist_ok=True)
    termes = script.get("termes_anglais", [])

    with tempfile.TemporaryDirectory() as travail:
        morceaux: list[Path] = []
        for rang, lot in enumerate(lots):
            texte_lot = "\n".join(texte for _, texte, _ in lot)
            cible = (
                final_path if len(lots) == 1
                else Path(travail) / f"lot_{rang:02d}.mp3"
            )
            try:
                _synthetiser_ou_scinder(
                    lot, cible, voice, termes, fournisseur, Path(travail), rang
                )
            except QuotaEpuise as e:
                # **La bascule n'est tolérée que sur le premier lot.** Changer de
                # moteur après coup mélangerait deux voix dans la même bande son,
                # défaut qui ne s'entend qu'à la lecture — c'est précisément ce
                # que le regroupement était censé rendre impossible. Tant qu'aucun
                # octet d'audio n'existe, la décision reste sûre.
                if not _repli_actif(fallback) or rang > 0:
                    raise
                fournisseur, voice, termes = _basculer(e)
                _synthetiser_ou_scinder(
                    lot, cible, voice, termes, fournisseur, Path(travail), rang
                )
            morceaux.append(cible)
            if len(lots) > 1:
                logger.info("  lot %d/%d : %d segments, %d caractères",
                            rang + 1, len(lots), len(lot), len(texte_lot))

        if len(lots) > 1:
            concatenate_segments(morceaux, final_path)

    logger.info("Calage des mots pour retrouver les frontières de slides")
    mots = caler_mots(final_path, texte_complet, taille_modele)

    # Découpe : chaque segment couvre un nombre de mots connu d'avance.
    output_dir.mkdir(parents=True, exist_ok=True)
    fichiers: list[Path] = []
    curseur = 0
    for i, (seg_id, texte, _type) in enumerate(segments):
        nb = len(texte.split())
        tranche = mots[curseur : curseur + nb]
        if not tranche:
            raise RuntimeError(
                f"Calage insuffisant pour le segment '{seg_id}' : la narration "
                "n'a pas pu être redécoupée."
            )

        debut = 0.0 if i == 0 else tranche[0].debut
        if curseur + nb < len(mots):
            # Milieu du silence entre le dernier mot de ce segment et le premier
            # du suivant : la coupe ne tombe ainsi sur aucune syllabe.
            suivant = mots[curseur + nb]
            fin = (tranche[-1].fin + suivant.debut) / 2
        else:
            fin = None  # dernier segment : jusqu'à la fin du fichier

        # Le début reprend la fin de la coupe précédente pour ne rien perdre.
        if fichiers:
            debut = precedent_fin
        precedent_fin = fin if fin is not None else 0.0

        # Une frontière qui ne progresse pas signale que le calage a échoué sur
        # ce segment : ses mots portent tous le même horodatage. FFmpeg refuserait
        # la découpe par un « -to value smaller than -ss » qui ne dit rien de la
        # cause. Le plus fréquent : le texte n'a pas été prononcé, donc la
        # transcription n'a rien à quoi l'apparier.
        if fin is not None and fin <= debut:
            raise RuntimeError(
                f"Découpe impossible pour '{seg_id}' : intervalle vide "
                f"({debut:.3f} -> {fin:.3f}). Les mots de ce segment n'ont pas pu "
                f"être datés, ce qui arrive quand son texte est absent de la bande "
                f"son. Écoute {final_path.name} au début pour vérifier que le "
                f"segment a bien été prononcé."
            )

        fichiers.append(_decouper(final_path, debut, fin, output_dir / f"{seg_id}.mp3"))
        curseur += nb

    logger.info("Bande son découpée en %d segments", len(fichiers))
    return final_path, fichiers


def concatenate_segments(audio_files: list[Path], output_path: Path) -> Path:
    """
    Concatène tous les segments MP3 en un seul fichier via FFmpeg.

    Args:
        audio_files: Liste ordonnée des fichiers MP3
        output_path: Chemin du fichier MP3 final

    Returns:
        Chemin du fichier MP3 concaténé
    """
    import subprocess
    import tempfile

    if not audio_files:
        raise ValueError("Aucun fichier audio à concaténer")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Fichier liste FFmpeg. L'apostrophe est échappée en "'\''" (ferme, apostrophe
    # littérale, rouvre) : non échappée, elle fermerait la citation en cours et le
    # démuxeur concat basculerait en mode non cité pour le reste de la ligne,
    # concaténant silencieusement les segments — ce qu'un stem avec élision
    # ("l'entraînement", "d'un modèle") déclenche systématiquement.
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for p in audio_files:
            echappe = p.resolve().as_posix().replace("'", "'\\''")
            f.write(f"file '{echappe}'\n")
        list_path = Path(f.name)

    cmd = [
        find_ffmpeg(), "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        str(output_path),
    ]

    logger.info("Concaténation FFmpeg : %d fichiers → %s", len(audio_files), output_path.name)
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    list_path.unlink(missing_ok=True)

    if result.returncode != 0:
        err = result.stderr or result.stdout or "(aucun message)"
        logger.error("FFmpeg erreur (code %d) :\n%s", result.returncode, err)
        raise RuntimeError(f"FFmpeg a échoué (code {result.returncode}) : {err}")

    logger.info("Audio final : %s", output_path)
    return output_path


def process_script(
    script_path: Path,
    voice: str = "",
    provider: str | None = None,
    grouper: bool | None = None,
    fallback: "bool | None" = None,
) -> Path:
    """
    Pipeline complet : script JSON → bande son + segments par slide.

    Args:
        script_path: Chemin vers le script JSON
        voice:       Voix ; vide = celle du moteur retenu
        provider:    Moteur TTS ; None = TTS_PROVIDER dans .env puis défaut
        grouper:     Tout synthétiser en un appel ; None = selon le moteur
        fallback:    Autoriser la bascule sur un moteur de repli si le quota est épuisé ;
                     None = TTS_FALLBACK dans .env, désactivé par défaut

    Returns:
        Chemin du fichier MP3 final
    """
    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    stem = script_path.stem
    segments_dir = AUDIO_DIR / stem / "segments"
    final_path = AUDIO_DIR / stem / f"{stem}.mp3"

    fournisseur = resoudre_fournisseur(provider)
    if grouper is None:
        # edge-tts n'a aucun quota : on y garde un appel par segment, ce qui
        # permet une consigne de jeu propre à chaque type de segment. Les
        # fournisseurs facturés à la requête sont groupés.
        grouper = fournisseur != "edge"

    if grouper:
        final, _ = generer_audio_groupe(
            script, segments_dir, final_path, voice, fournisseur, fallback=fallback
        )
        return final

    audio_files = generate_audio_segments(
        script, segments_dir, voice, fournisseur, fallback=fallback
    )
    return concatenate_segments(audio_files, final_path)


def main() -> None:
    """Point d'entrée CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Synthese vocale francaise")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", help="Chemin vers un script JSON")
    group.add_argument("--text", help="Texte libre à synthétiser")
    parser.add_argument("--output", default="data/audio/", help="Dossier ou fichier de sortie")
    parser.add_argument(
        "--voice", default="",
        help=f"Voix. edge : {list(VOICES)} ; gemini : nom de voix (defaut {GEMINI_VOIX_DEFAUT})",
    )
    parser.add_argument(
        "--provider", choices=FOURNISSEURS,
        help="Moteur TTS (defaut : TTS_PROVIDER dans .env)",
    )
    parser.add_argument(
        "--fallback", action="store_true",
        help="Basculer sur edge-tts si le quota journalier est epuise, "
             "au lieu d'echouer (equivaut a TTS_FALLBACK=1)",
    )
    args = parser.parse_args()

    fournisseur = resoudre_fournisseur(args.provider)

    if args.text:
        output_path = Path(args.output)
        if output_path.is_dir() or not output_path.suffix:
            output_path = output_path / "tts_output.mp3"
        voix = args.voice or _voix_par_defaut(fournisseur)
        if fournisseur == "edge":
            voix = VOICES.get(voix, voix)
        _synthetiser(args.text, output_path, voix, "content", [], fournisseur)
        print(f"Audio généré : {output_path}  [{fournisseur}/{voix}]")

    else:
        script_path = Path(args.input)
        if not script_path.exists():
            logger.error("Fichier introuvable : %s", script_path)
            return
        # --fallback absent ne signifie pas « interdit » : None laisse la
        # décision à TTS_FALLBACK dans .env.
        final = process_script(
            script_path, voice=args.voice, provider=fournisseur,
            fallback=True if args.fallback else None,
        )
        print(f"Audio final : {final}  [{fournisseur}]")


if __name__ == "__main__":
    main()
