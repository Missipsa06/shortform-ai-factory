"""
Synthèse vocale locale : Qwen3-TTS piloté par Wan2GP, voix clonée.

**Pourquoi ce moteur.** Gemini TTS est une préversion limitée à 10 appels par
jour, qui refuse certains segments sans raison stable (400 INVALID_ARGUMENT,
reproductible sur une conclusion le 24/10/2026). Ce moteur tourne sur la machine,
sans quota, et **clone la voix Charon** à partir d'un extrait de 10 s : une vidéo
produite en local garde la voix des autres.

**Pourquoi Qwen3 et pas un autre.** Mesuré le 24/10/2026 sur une narration de 195
mots dense en jargon, transcrite par Whisper : Qwen3 retrouve 88 % du texte et
7 termes anglais sur 11, contre 83 % et 6 pour OmniVoice, 80 % et 5 pour
Chatterbox — lequel sautait aussi des passages avec sa voix par défaut. Piper
(84 %, 6) ne clone aucune voix.

**Pourquoi par Wan2GP et pas en bibliothèque.** Qwen3-TTS demande PyTorch et un
GPU, que CLAUDE.md écarte du projet. Wan2GP les porte dans son propre venv ; le
projet ne fait que lui confier une file de tâches (`wgp.py --process`). Contrepartie
assumée : **ce moteur n'existe pas dans l'image Docker**, qui n'a ni GPU ni Wan2GP.

**Un seul lancement par vidéo, jamais un par segment.** Le chargement du modèle
coûte une quarantaine de secondes ; neuf lancements en coûteraient six minutes.

Configuration (.env) :
    WAN2GP_DIR=D:\\Wan2GP        dossier de Wan2GP, avec son .venv
    QWEN3_TTS_VOICE=charon      nom d'une voix de media/assets/voix/
"""

import json
import logging
import os
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from media.ffmpeg_utils import ffmpeg as find_ffmpeg

logger = logging.getLogger(__name__)

WAN2GP_ENV = "WAN2GP_DIR"
VOIX_ENV = "QWEN3_TTS_VOICE"
VOIX_DEFAUT = "charon"

ASSETS = Path(__file__).parent / "assets"
# Une voix = deux fichiers de même nom : l'extrait (.mp3) et sa transcription
# exacte (.txt). Avec la transcription, Qwen3 imite le timbre de plus près ;
# sans elle, il n'en garde que le caractère général.
VOIX_DIR = ASSETS / "voix"
PRONONCIATIONS = ASSETS / "prononciations.json"

MODELE = "qwen3_tts_base"
LANGUE = "french"     # « auto » mesuré moins bon le 24/10/2026 (86 % contre 88 %)
GRAINE = 42           # même texte, même audio : une reprise ne change pas la voix
DEBIT_MP3 = "128k"

# Chargement du modèle, puis chaque segment. Mesuré sur RTX 4060 Laptop : ~45 s,
# puis ~9 s par segment ; les marges couvrent une machine chargée.
DELAI_CHARGEMENT_S = 600
DELAI_PAR_SEGMENT_S = 120


def voix_disponibles() -> list[str]:
    """Voix clonables : chaque extrait de media/assets/voix/ qui a sa transcription."""
    return sorted(p.stem for p in VOIX_DIR.glob("*.mp3") if p.with_suffix(".txt").exists())


def _installation() -> "tuple[Path, Path] | str":
    """
    (dossier Wan2GP, interpréteur de son venv), ou la raison de leur absence.

    Rendue en chaîne plutôt que levée : le contrôle préalable du pipeline veut
    une réponse, pas une exception, pour échouer avant l'appel au LLM.
    """
    brut = os.getenv(WAN2GP_ENV, "").strip()
    if not brut:
        return f"{WAN2GP_ENV} n'est pas défini dans .env"
    dossier = Path(brut)
    if not (dossier / "wgp.py").is_file():
        return f"wgp.py introuvable dans {dossier} ({WAN2GP_ENV})"
    for relatif in (".venv/Scripts/python.exe", ".venv/bin/python"):
        python = dossier / relatif
        if python.is_file():
            return dossier, python
    return f"aucun venv dans {dossier} (.venv/Scripts/python.exe ou .venv/bin/python)"


def absence() -> str:
    """Ce qui manque pour utiliser ce moteur ; chaîne vide s'il est prêt."""
    installation = _installation()
    return installation if isinstance(installation, str) else ""


@lru_cache(maxsize=1)
def _prononciations() -> "tuple[tuple[re.Pattern, str], ...]":
    """
    Motifs du dictionnaire, le plus long d'abord.

    L'ordre compte : « Claude Code » doit être remplacé avant « Claude », qui
    sinon ferait « Clôde Code » par accident et « Clôde Clôde » ailleurs.
    """
    entrees = json.loads(PRONONCIATIONS.read_text(encoding="utf-8"))
    entrees = {k: v for k, v in entrees.items() if not k.startswith("_")}
    # Bornes sur lettres et chiffres seulement, pas sur le tiret : « --tmux »
    # doit être remplacé, alors qu'un tiret en frontière l'aurait protégé.
    return tuple(
        (re.compile(rf"(?<!\w){re.escape(terme)}(?!\w)", re.IGNORECASE), graphie)
        for terme, graphie in sorted(entrees.items(), key=lambda e: -len(e[0]))
    )


def prononcer(texte: str) -> str:
    """
    Remplace les termes anglais par leur graphie phonétique.

    Appliqué au seul texte envoyé au moteur : le karaoké et les slides gardent
    l'orthographe d'origine. Qwen3 lisait « Tmux » comme un mot inconnu —
    « TMUKISS », « TEMX » à la transcription — et « Ti-max » est entendu « T-Max »
    à chaque occurrence, mesuré le 24/10/2026.
    """
    for motif, graphie in _prononciations():
        texte = motif.sub(graphie, texte)
    return texte


def _taches(textes: list[str], voix: str) -> list[dict]:
    """File Wan2GP : une tâche par texte, nommée par son rang pour la retrouver."""
    extrait = VOIX_DIR / f"{voix}.mp3"
    transcription = VOIX_DIR / f"{voix}.txt"
    if not (extrait.is_file() and transcription.is_file()):
        raise ValueError(
            f"Voix '{voix}' inconnue : il faut {extrait.name} et {transcription.name} "
            f"dans {VOIX_DIR}. Disponibles : {', '.join(voix_disponibles()) or 'aucune'}"
        )
    reference = transcription.read_text(encoding="utf-8").strip()
    return [
        {
            "model_type": MODELE,
            "prompt": texte,
            "model_mode": LANGUE,
            "audio_prompt_type": "A",
            "audio_guide": str(extrait.resolve()),
            "alt_prompt": reference,
            "seed": GRAINE,
            "repeat_generation": 1,
            # Sans nom imposé, Wan2GP nomme d'après l'heure et le début du
            # texte : deux segments qui commencent pareil deviendraient
            # indiscernables, et l'ordre serait à deviner.
            "output_filename": f"{rang:03d}",
        }
        for rang, texte in enumerate(textes)
    ]


def _en_mp3(source: Path, sortie: Path) -> None:
    """Convertit une sortie Wan2GP au MP3 qu'attend le reste du pipeline."""
    sortie.parent.mkdir(parents=True, exist_ok=True)
    resultat = subprocess.run(
        [find_ffmpeg(), "-y", "-i", str(source), "-b:a", DEBIT_MP3, str(sortie)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if resultat.returncode != 0:
        raise RuntimeError(f"Conversion en MP3 échouée pour {sortie.name} : "
                           f"{(resultat.stderr or '')[-300:]}")


def synthetiser(textes: list[str], sorties: list[Path], voix: str = "") -> None:
    """
    Synthétise chaque texte dans le fichier de même rang, en un lancement.

    Args:
        textes:  Textes à dire, déjà passés par `prononcer`
        sorties: Un MP3 par texte, dans le même ordre
        voix:    Nom d'une voix de media/assets/voix/ ; vide = QWEN3_TTS_VOICE

    Raises:
        RuntimeError: Wan2GP absent, en échec, ou un segment sans audio. Un trou
                      interrompt tout : poursuivre livrerait une bande son
                      amputée, défaut que seule l'écoute révélerait.
    """
    if len(textes) != len(sorties):
        raise ValueError("Un fichier de sortie par texte")
    installation = _installation()
    if isinstance(installation, str):
        raise RuntimeError(f"Moteur qwen3 indisponible : {installation}")
    dossier, python = installation
    voix = voix or os.getenv(VOIX_ENV) or VOIX_DEFAUT

    with tempfile.TemporaryDirectory(prefix="wan2gp_") as travail:
        travail = Path(travail)
        file = travail / "taches.json"
        file.write_text(json.dumps(_taches(textes, voix), ensure_ascii=False),
                        encoding="utf-8")
        logger.info("Qwen3 via Wan2GP : %d segment(s), voix %s", len(textes), voix)
        try:
            resultat = subprocess.run(
                [str(python), "wgp.py", "--process", str(file),
                 "--output-dir", str(travail)],
                cwd=dossier, capture_output=True, encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                timeout=DELAI_CHARGEMENT_S + DELAI_PAR_SEGMENT_S * len(textes),
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"Wan2GP n'a pas fini en {e.timeout:.0f} s") from e
        journal = (resultat.stdout or "") + (resultat.stderr or "")
        if resultat.returncode != 0:
            raise RuntimeError(f"Wan2GP a échoué (code {resultat.returncode}) : "
                               f"{journal[-800:]}")

        for rang, sortie in enumerate(sorties):
            produits = sorted(travail.glob(f"{rang:03d}.*"))
            if not produits:
                raise RuntimeError(
                    f"Wan2GP n'a rien produit pour le segment {rang + 1}/{len(textes)} "
                    f"(« {textes[rang][:60]} ») : {journal[-800:]}"
                )
            _en_mp3(produits[0], sortie)
