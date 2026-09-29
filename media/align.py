"""
Calage du texte connu sur l'audio, mot par mot.

Le pipeline connaît exactement le texte envoyé à la synthèse vocale. Le
retranscrire pour fabriquer des sous-titres réintroduit des erreurs dans un texte
pourtant certain — c'est ce que faisait media/subtitles.py.

Ici, la transcription ne sert qu'à **dater** : on demande à faster-whisper les
horodatages mot à mot, puis on recale la séquence reconnue sur la séquence
attendue. Le texte affiché reste toujours le texte d'origine ; une erreur de
reconnaissance ne peut donc dégrader que le placement temporel, jamais le
contenu.

Ce n'est pas de l'alignement forcé au sens strict (qui supposerait un modèle
acoustique dédié, et donc PyTorch) : c'est un recalage de séquences, suffisant
sur une voix de synthèse très articulée.
"""

import difflib
import gc
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_modele_cache: dict[str, object] = {}


@dataclass(frozen=True)
class Mot:
    """Un mot du texte d'origine, daté par rapport au début du segment audio."""

    texte: str
    debut: float
    fin: float


def _charger_modele(taille: str):
    """Charge le modèle de transcription une seule fois par taille demandée."""
    if taille not in _modele_cache:
        from faster_whisper import WhisperModel

        logger.info("Chargement du modèle de transcription (%s)", taille)
        _modele_cache[taille] = WhisperModel(taille, device="cpu", compute_type="int8")
    return _modele_cache[taille]


def liberer_modeles() -> None:
    """
    Décharge les modèles de transcription et rend la mémoire au système.

    Le cache est une bonne chose tant qu'on transcrit ; il devient nuisible
    ensuite. Dans `content/render.py`, le calage karaoké précède le lancement de
    Chromium et de x264 : sans cette libération, le modèle reste résident pendant
    tout le rendu, au moment précis où la mémoire manque.

    Mesuré sur `base` en int8 : 214 Mo de mémoire de travail une fois le modèle
    chargé et un segment calé, dont 123 Mo réellement rendus au système ici — le
    reste est conservé par l'allocateur de CTranslate2 et ne se récupère qu'à la
    fin du processus.
    """
    if not _modele_cache:
        return
    tailles = ", ".join(sorted(_modele_cache))
    _modele_cache.clear()
    gc.collect()
    logger.info("Modèle de transcription déchargé (%s)", tailles)


def _normaliser(mot: str) -> str:
    """
    Réduit un mot à sa forme comparable : sans accent, sans ponctuation, en bas de casse.

    La comparaison sert uniquement à apparier deux séquences ; le mot affiché
    reste celui du texte d'origine.
    """
    sans_accent = "".join(
        c for c in unicodedata.normalize("NFD", mot) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^\w]", "", sans_accent).lower()


def _mots_reconnus(audio: Path, taille_modele: str) -> list[tuple[str, float, float]]:
    """Transcrit l'audio et retourne les mots reconnus avec leurs horodatages."""
    modele = _charger_modele(taille_modele)
    segments, _ = modele.transcribe(
        str(audio), language="fr", word_timestamps=True
    )
    return [
        (mot.word.strip(), mot.start, mot.end)
        for segment in segments
        for mot in (segment.words or [])
    ]


def _repartir(attendus: list[str], debut: float, fin: float) -> list[Mot]:
    """Répartit uniformément des mots sur un intervalle, faute de mieux."""
    if not attendus:
        return []
    pas = (fin - debut) / len(attendus)
    return [
        Mot(mot, debut + i * pas, debut + (i + 1) * pas)
        for i, mot in enumerate(attendus)
    ]


def caler_mots(
    audio: Path,
    texte: str,
    taille_modele: str = "base",
    duree_totale: float | None = None,
) -> list[Mot]:
    """
    Date chaque mot du texte connu à partir de l'audio correspondant.

    Args:
        audio:          Segment MP3 dont on connaît le texte
        texte:          Texte exact envoyé à la synthèse vocale
        taille_modele:  Taille du modèle de transcription (tiny, base, small…)
        duree_totale:   Durée du segment ; déduite du dernier mot reconnu si absente

    Returns:
        Un Mot par mot du texte d'origine, dans l'ordre, horodaté en secondes
        depuis le début du segment
    """
    attendus = texte.split()
    if not attendus:
        return []

    reconnus = _mots_reconnus(audio, taille_modele)
    if not reconnus:
        logger.warning(
            "Aucun mot reconnu dans %s : répartition uniforme", audio.name
        )
        return _repartir(attendus, 0.0, duree_totale or 1.0)

    fin_audio = duree_totale or reconnus[-1][2]

    # Appariement des deux séquences sur leur forme normalisée.
    norm_reconnus = [_normaliser(m[0]) for m in reconnus]
    norm_attendus = [_normaliser(m) for m in attendus]
    apparieur = difflib.SequenceMatcher(None, norm_reconnus, norm_attendus)

    dates: list[tuple[float, float] | None] = [None] * len(attendus)
    for tag, i1, i2, j1, j2 in apparieur.get_opcodes():
        if tag != "equal":
            continue  # les écarts sont comblés par interpolation ensuite
        for decalage in range(j2 - j1):
            _, debut, fin = reconnus[i1 + decalage]
            dates[j1 + decalage] = (debut, fin)

    apparies = sum(1 for d in dates if d is not None)
    logger.debug(
        "%s : %d/%d mots appariés directement", audio.name, apparies, len(attendus)
    )

    # Comblement des trous : on répartit le temps disponible entre les deux
    # ancrages connus qui encadrent chaque série de mots non appariés.
    resultat: list[Mot] = []
    i = 0
    while i < len(attendus):
        if dates[i] is not None:
            debut, fin = dates[i]
            resultat.append(Mot(attendus[i], debut, fin))
            i += 1
            continue

        j = i
        while j < len(attendus) and dates[j] is None:
            j += 1

        borne_debut = resultat[-1].fin if resultat else 0.0
        borne_fin = dates[j][0] if j < len(attendus) else fin_audio
        resultat.extend(_repartir(attendus[i:j], borne_debut, max(borne_fin, borne_debut)))
        i = j

    return resultat
