"""
Corpus d'enregistrement d'une voix propre, puis jeu d'entraînement Qwen3-TTS.

Deux étapes, dans le venv du projet (ni PyTorch ni GPU) :

  corpus  tire des scripts déjà écrits les phrases à lire, dans le registre exact
          des vidéos — vocabulaire technique, chiffres, anglicismes compris ;
  jeu     convertit les prises (24 kHz mono, silences rognés, niveau normalisé),
          vérifie à la transcription que la phrase a bien été lue, et écrit le
          JSONL attendu par l'affinage (`outils/voix_lora/`).

L'enregistrement se fait entre les deux : `make voix-enregistrer`.

Pourquoi une voix propre et non Charon : les conditions de l'API Gemini
interdisent d'utiliser le service pour développer un modèle concurrent, et un
TTS entraîné sur des sorties de Gemini TTS en est un, qu'il reste local ou non.

Tout vit dans `data/voix/<nom>/` :
    corpus.tsv          id <TAB> phrase
    enregistrements/    prises brutes, <id>.wav (une par phrase)
    jeu/                prises converties, ref.wav et sa transcription ref.txt
    train_raw.jsonl     {audio, text, ref_audio, language}
    rejets.tsv          prises écartées et pourquoi
"""

import argparse
import difflib
import json
import logging
import random
import re
import subprocess
from pathlib import Path

from media.ffmpeg_utils import ffmpeg, ffprobe
from media.naming import PROCESSED_DIR
from media.tts import build_script_text

logger = logging.getLogger(__name__)

VOIX_DIR = Path("data/voix")

# Débit de lecture estimé pour viser une durée de corpus : 0,39 à 0,52 s par mot
# mesurés sur les voix de synthèse, une lecture humaine posée est du même ordre.
MOTS_PAR_SECONDE = 2.3
# Une phrase de 12 à 35 mots dure ~5 à 15 s : dans la fourchette conseillée
# pour l'affinage (5 à 30 s), et assez courte pour être lue sans accroc.
MOTS_MIN, MOTS_MAX = 12, 35
DUREE_MIN_S, DUREE_MAX_S = 2.0, 30.0
# Part des mots attendus retrouvés à la transcription. Whisper se trompe sur les
# anglicismes : le seuil tolère quelques mots ratés, pas une phrase sautée.
SEUIL_LECTURE = 0.80
GRAINE = 7


def dossier(voix: str) -> Path:
    """Dossier de travail d'une voix."""
    return VOIX_DIR / voix


def lire_corpus(voix: str) -> list[tuple[str, str]]:
    """Paires (id, phrase) du corpus, dans l'ordre de lecture."""
    chemin = dossier(voix) / "corpus.tsv"
    if not chemin.exists():
        raise FileNotFoundError(f"Pas de corpus pour « {voix} » : make voix-corpus VOIX={voix}")
    lignes = chemin.read_text(encoding="utf-8").splitlines()
    return [tuple(ligne.split("\t", 1)) for ligne in lignes if "\t" in ligne]


# ── Corpus ───────────────────────────────────────────────────────────────────

def _phrases(texte: str) -> list[str]:
    """Découpe un segment en phrases, sur la ponctuation finale."""
    return [p.strip() for p in re.split(r"(?<=[.!?…])\s+", texte) if p.strip()]


def _regrouper(phrases: list[str]) -> list[str]:
    """
    Réunit les phrases consécutives d'un segment jusqu'à `MOTS_MIN` mots.

    Une phrase isolée de quatre mots ne porte presque pas de prosodie ; au-delà
    de `MOTS_MAX`, la prise devient longue à lire sans faute. Une phrase seule
    trop longue est gardée telle quelle : la couper inventerait une ponctuation.
    """
    groupes, courant = [], []
    for phrase in phrases:
        if courant and len(" ".join(courant + [phrase]).split()) > MOTS_MAX:
            groupes.append(" ".join(courant))
            courant = []
        courant.append(phrase)
        if len(" ".join(courant).split()) >= MOTS_MIN:
            groupes.append(" ".join(courant))
            courant = []
    if courant:
        groupes.append(" ".join(courant))
    return [g for g in groupes if MOTS_MIN <= len(g.split()) <= MOTS_MAX * 1.3]


def _cle(texte: str) -> str:
    """Forme comparable d'une phrase, pour dédoublonner."""
    return re.sub(r"\W+", " ", texte.lower()).strip()


def construire_corpus(voix: str, minutes: float) -> Path:
    """
    Tire des scripts existants environ `minutes` de lecture et écrit corpus.tsv.

    Les phrases porteuses d'un anglicisme déclaré, d'un chiffre ou d'une
    question passent d'abord : ce sont elles que la voix clonée rate. La
    sélection est ensuite mélangée (graine fixe) pour qu'un enregistrement
    partiel reste représentatif. Un corpus existant n'est jamais écrasé : ses
    identifiants nomment des prises déjà faites.
    """
    sortie = dossier(voix) / "corpus.tsv"
    if sortie.exists():
        raise FileExistsError(f"{sortie} existe déjà : ses id nomment les prises enregistrées")

    candidats: dict[str, tuple[int, str]] = {}
    for chemin in sorted(PROCESSED_DIR.glob("*.json")):
        try:
            script = json.loads(chemin.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Script illisible ignoré : %s", chemin.name)
            continue
        if not isinstance(script, dict) or "slides" not in script:
            continue
        termes = [t.lower() for t in script.get("termes_anglais", []) if isinstance(t, str)]
        for _id, texte, _type in build_script_text(script):
            for phrase in _regrouper(_phrases(texte)):
                bas = phrase.lower()
                score = (2 * any(t in bas for t in termes)
                         + bool(re.search(r"\d", phrase)) + ("?" in phrase))
                candidats.setdefault(_cle(phrase), (score, phrase))

    hasard = random.Random(GRAINE)
    tries = sorted(candidats.values(), key=lambda c: (-c[0], hasard.random()))
    budget = minutes * 60 * MOTS_PAR_SECONDE
    choisies, mots = [], 0
    for _score, phrase in tries:
        if mots >= budget:
            break
        choisies.append(phrase)
        mots += len(phrase.split())
    hasard.shuffle(choisies)

    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(
        "".join(f"{i:04d}\t{p}\n" for i, p in enumerate(choisies, 1)), encoding="utf-8"
    )
    logger.info("%d phrases (~%.0f min de lecture) sur %d candidates → %s",
                len(choisies), mots / MOTS_PAR_SECONDE / 60, len(candidats), sortie)
    return sortie


# ── Jeu d'entraînement ───────────────────────────────────────────────────────

def _duree(audio: Path) -> float:
    """Durée d'un fichier audio, en secondes."""
    sortie = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio)],
        capture_output=True, text=True, check=True,
    )
    return float(sortie.stdout.strip())


def _convertir(brut: Path, sortie: Path) -> None:
    """
    24 kHz mono — seule fréquence qu'accepte l'extracteur de Qwen3-TTS —,
    silences de tête et de queue rognés, niveau ramené à -19 LUFS. Une prise
    commencée par une seconde de silence apprendrait au modèle à hésiter.
    """
    rognage = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,"
               "areverse,"
               "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.30,"
               "areverse,loudnorm=I=-19:TP=-2:LRA=11")
    resultat = subprocess.run(
        [ffmpeg(), "-y", "-v", "error", "-i", str(brut), "-af", rognage,
         "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(sortie)],
        capture_output=True, text=True,
    )
    if resultat.returncode != 0:
        raise RuntimeError(f"Conversion de {brut.name} échouée : {resultat.stderr[-300:]}")


def _part_lue(audio: Path, texte: str) -> float:
    """Part des mots attendus retrouvés par la transcription, dans l'ordre."""
    from media.align import _mots_reconnus, _normaliser

    attendus = [m for m in (_normaliser(x) for x in texte.split()) if m]
    reconnus = [m for m in (_normaliser(x) for x, _d, _f in _mots_reconnus(audio, "small")) if m]
    if not attendus:
        return 0.0
    blocs = difflib.SequenceMatcher(a=attendus, b=reconnus, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocs) / len(attendus)


def construire_jeu(voix: str, verifier: bool = True, ref: str | None = None) -> Path:
    """
    Convertit les prises, écarte les ratées, choisit la référence, écrit le JSONL.

    La référence est la même pour toutes les lignes, comme le recommande le
    script officiel : c'est elle qui fixe l'empreinte de la voix. Par défaut, la
    première prise retenue de 8 à 15 s ; `ref` impose un id.
    """
    base = dossier(voix)
    jeu = base / "jeu"
    jeu.mkdir(parents=True, exist_ok=True)
    retenues, rejets = [], []

    for id_, texte in lire_corpus(voix):
        brut = base / "enregistrements" / f"{id_}.wav"
        if not brut.exists():
            continue
        propre = jeu / f"{id_}.wav"
        _convertir(brut, propre)
        duree = _duree(propre)
        if not DUREE_MIN_S <= duree <= DUREE_MAX_S:
            rejets.append((id_, f"durée {duree:.1f} s"))
            continue
        if verifier:
            part = _part_lue(propre, texte)
            if part < SEUIL_LECTURE:
                rejets.append((id_, f"lecture {part:.0%} — à réenregistrer"))
                continue
        retenues.append((id_, texte, propre, duree))

    if not retenues:
        raise RuntimeError(f"Aucune prise exploitable dans {base / 'enregistrements'}")

    if ref:
        choix = next((r for r in retenues if r[0] == ref), None)
        if choix is None:
            raise ValueError(f"Prise de référence {ref} absente ou écartée")
    else:
        choix = next((r for r in retenues if 8 <= r[3] <= 15), retenues[0])
    reference = jeu / "ref.wav"
    reference.write_bytes(choix[2].read_bytes())
    # Transcription de la référence : le clonage (`ecouter --clone`) la demande.
    (jeu / "ref.txt").write_text(choix[1], encoding="utf-8")

    sortie = base / "train_raw.jsonl"
    with sortie.open("w", encoding="utf-8") as f:
        for _id, texte, propre, _d in retenues:
            f.write(json.dumps({"audio": str(propre.resolve()), "text": texte,
                                "ref_audio": str(reference.resolve()), "language": "French"},
                               ensure_ascii=False) + "\n")
    (base / "rejets.tsv").write_text("".join(f"{i}\t{r}\n" for i, r in rejets), encoding="utf-8")

    total = sum(r[3] for r in retenues)
    logger.info("%d prises retenues (%.1f min), %d écartées (rejets.tsv), référence %s",
                len(retenues), total / 60, len(rejets), choix[0])
    if total < 20 * 60:
        logger.warning("Moins de 20 min de voix : l'affinage risque de surapprendre "
                       "le peu qu'il entend. Visé : 30 à 60 min.")
    return sortie


def main() -> None:
    """Point d'entrée CLI."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("etape", choices=["corpus", "jeu"])
    parser.add_argument("--voix", required=True, help="nom de la voix, ex. moi")
    parser.add_argument("--minutes", type=float, default=45, help="durée de lecture visée")
    parser.add_argument("--sans-verification", action="store_true",
                        help="ne pas transcrire les prises pour vérifier la lecture")
    parser.add_argument("--ref", help="id de la prise de référence")
    args = parser.parse_args()
    if args.etape == "corpus":
        construire_corpus(args.voix, args.minutes)
    else:
        construire_jeu(args.voix, verifier=not args.sans_verification, ref=args.ref)


if __name__ == "__main__":
    main()
