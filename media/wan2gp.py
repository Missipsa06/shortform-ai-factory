"""
Modèles locaux pilotés par Wan2GP : la voix (Qwen3-TTS, voix clonée) et les
fonds de slides (Z-Image Turbo), lancés tous deux en lot par `_executer`.

Synthèse vocale locale : Qwen3-TTS piloté par Wan2GP, voix clonée.

**Pourquoi ce moteur.** Gemini TTS est une préversion limitée à 10 appels par
jour, qui refuse certains segments sans raison stable (400 INVALID_ARGUMENT,
reproductible sur une conclusion le 24/10/2026). Ce moteur tourne sur la machine,
sans quota, et **clone la voix de la chaîne** à partir d'un extrait de 12 s
(`animateur`, inventée par Qwen3 VoiceDesign ; Charon jusqu'au 28/10/2026).

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
    QWEN3_TTS_VOICE=animateur   nom d'une voix de media/assets/voix/
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from media.ffmpeg_utils import ffmpeg as find_ffmpeg

# Chargé ici et non chez l'appelant : `python -m content.scenes` n'importe aucun
# module qui le fasse, et WAN2GP_DIR restait vide — les scènes échouaient en
# repli, slide sans figure, avec un simple WARNING (constaté le 26/10/2026).
load_dotenv()

logger = logging.getLogger(__name__)

WAN2GP_ENV = "WAN2GP_DIR"
VOIX_ENV = "QWEN3_TTS_VOICE"
# Voix inventée par Qwen3 VoiceDesign (28/10/2026), et non plus Charon : cloner
# une sortie de Gemini TTS rapproche du « modèle concurrent » que ses conditions
# interdisent. Elle n'appartient à personne, les poids sont sous Apache 2.0.
VOIX_DEFAUT = "animateur"

ASSETS = Path(__file__).parent / "assets"
# Une voix = deux fichiers de même nom : l'extrait (.mp3) et sa transcription
# exacte (.txt). Avec la transcription, Qwen3 imite le timbre de plus près ;
# sans elle, il n'en garde que le caractère général.
VOIX_DIR = ASSETS / "voix"
PRONONCIATIONS = ASSETS / "prononciations.json"

MODELE = "qwen3_tts_base"
LANGUE = "french"     # « auto » mesuré moins bon le 24/10/2026 (86 % contre 88 %)
GRAINE = 42           # même texte, même audio : une reprise ne change pas la voix
# Température d'échantillonnage du clone ; Wan2GP prend 0,9 par défaut. Mesuré
# le 28/10/2026 sur trois segments chargés en chiffres : à 0,7, 77 % du texte
# retrouvé contre 73 %, et une part de parole voisée (55 à 61 %) proche de celle
# de Gemini (48 à 53 %) là où 0,9 enchaînait sans respirer (64 à 69 %).
TEMPERATURE = 0.7
DEBIT_MP3 = "128k"

# Silence ajouté à la fin de chaque segment. Qwen3 s'arrête presque sur son
# dernier mot (0,18 s mesuré) et la slide durant exactement son audio, la
# suivante enchaînait sans respiration. Le chemin Gemini n'avait pas ce défaut :
# il coupait au milieu du silence entre deux segments. La pause tombe sur la
# slide en cours, pendant sa sortie animée.
PAUSE_FIN_S = 0.45

# Silence avant la voix, après rognage de celui que le modèle a mis. Qwen3
# attaque tantôt après 0,3 s, tantôt au premier échantillon : mesuré le
# 28/10/2026, l'accroche d'une vidéo démarrait sur une consonne déjà entamée,
# entendue comme un début coupé. Le fondu de 10 ms efface le clic d'une onde qui
# ne part pas de zéro ; l'attaque fixe rend les enchaînements réguliers.
ATTAQUE_S = 0.20

# Anglicismes dont le s final se prononce aussi en français : invariables.
_S_PRONONCE = frozenset({
    "kubernetes", "pandas", "keras", "devops", "mlops", "analytics", "news",
    "series", "windows", "ios", "macos", "aws", "chaos", "canvas",
})

# Chargement du modèle, puis chaque tâche. Mesuré sur RTX 4060 Laptop : ~45 s,
# puis ~9 s par segment de voix ; les marges couvrent une machine chargée.
DELAI_CHARGEMENT_S = 600
DELAI_PAR_TACHE_S = 120

# Fonds de slides. Z-Image Turbo : 8 étapes, 6B, sa version int8 tient sur 8 Go
# grâce au déchargement de Wan2GP. Format 9:16 natif, en multiples de 16 —
# Cloudflare imposait du carré, recadré ensuite par le CSS.
MODELE_IMAGE = "z_image"
RESOLUTION_IMAGE = "1088x1920"

# Scènes animées. Flux 2 Klein 4B place la mascotte dans une situation à partir
# de son image de référence (mode « I » : personnes ou objets à reprendre) — le
# personnage reste le même d'une scène à l'autre, vérifié sur prototype. Wan 2.2
# TI2V 5B (FastWan, 3 étapes) l'anime : LTX-2 (19B) est exclu, 16 Go de RAM ne
# suffisant pas à le décharger. Carré : la scène occupe la zone graphique, pas
# l'écran. Mesuré sur RTX 4060 Laptop : ~3 min 45 par clip de 5 s, dont 2 min 32
# de décodage VAE — le poste à réduire si le budget serre (résolution, durée).
MODELE_SCENE = "flux2_klein_4b"
MODELE_ANIMATION = "ti2v_2_2_fastwan"
RESOLUTION_SCENE = "704x704"
IMAGES_ANIMATION = 121          # 5 s à 24 images/s
DELAI_PAR_CLIP_S = 600


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


def _pluriels_muets(texte: str, termes_anglais: "list[str] | tuple" = ()) -> str:
    """
    Retire le s du pluriel des anglicismes, muet en français.

    « des workflows » se dit « des workflow » ; Qwen3 lisait le mot à l'anglaise
    et prononçait le s (« workflowZ »). Ne touche qu'aux termes que le script
    déclare anglais (`termes_anglais`), au singulier comme au pluriel. Retirer le
    s d'un mot français resté dans la liste ne change rien à l'oreille, il est
    muet aussi.

    ponytail: heuristique — les terminaisons -us, -is, -ss, -os sont épargnées
    (« virus » ne doit pas devenir « viru »), plus une liste d'invariables ; un
    anglicisme invariable absent de `_S_PRONONCE` perdrait son s.
    """
    singuliers: set[str] = set()
    for terme in termes_anglais:
        for mot in str(terme).lower().split():
            if mot in _S_PRONONCE or mot.endswith(("us", "is", "ss", "os")):
                continue
            singuliers.add(mot[:-1] if mot.endswith("s") and len(mot) > 3 else mot)
    if not singuliers:
        return texte
    return re.sub(
        r"(?<!\w)([\w-]+?)s(?!\w)",
        lambda m: m.group(1) if m.group(1).lower() in singuliers else m.group(0),
        texte,
    )


_UNITES = ("zéro un deux trois quatre cinq six sept huit neuf dix onze douze treize "
           "quatorze quinze seize").split()
_DIZAINES = {2: "vingt", 3: "trente", 4: "quarante", 5: "cinquante", 6: "soixante"}


def _moins_de_cent(n: int) -> str:
    """0 à 99 en toutes lettres, avec soixante-dix et quatre-vingts."""
    if n < 17:
        return _UNITES[n]
    if n < 20:
        return "dix-" + _UNITES[n - 10]
    d, u = divmod(n, 10)
    if d == 7:
        return "soixante et onze" if u == 1 else "soixante-" + _moins_de_cent(10 + u)
    if d == 8:
        return "quatre-vingts" if u == 0 else "quatre-vingt-" + _UNITES[u]
    if d == 9:
        return "quatre-vingt-" + _moins_de_cent(10 + u)
    return _DIZAINES[d] + ("" if u == 0 else " et un" if u == 1 else "-" + _UNITES[u])


def _moins_de_mille(n: int) -> str:
    """0 à 999 en toutes lettres ; « cents » ne prend son s qu'en fin de nombre."""
    c, r = divmod(n, 100)
    if c == 0:
        return _moins_de_cent(r)
    tete = "cent" if c == 1 else f"{_UNITES[c]} cent"
    if r == 0:
        return tete + ("s" if c > 1 else "")
    return f"{tete} {_moins_de_cent(r)}"


def en_lettres(n: int) -> str:
    """
    Entier en toutes lettres, jusqu'aux milliards.

    Qwen3 lit « 30 100 » comme deux nombres séparés d'une pause — entendu « 30
    fins 100 », « 30 cents », mesuré le 28/10/2026 — et « 30100 » sans repère.
    Devant « mille », vingts et cents perdent leur s (« quatre-vingt mille ») ;
    devant million et milliard, des noms, ils le gardent.
    """
    if n < 1000:
        return _moins_de_mille(n)
    parts = []
    for valeur, nom in ((10 ** 9, "milliard"), (10 ** 6, "million")):
        q, n = divmod(n, valeur)
        if q:
            parts.append(f"{en_lettres(q)} {nom}{'s' if q > 1 else ''}")
    q, n = divmod(n, 1000)
    if q:
        milliers = _moins_de_mille(q)
        if milliers.endswith(("cents", "vingts")):
            milliers = milliers[:-1]
        parts.append("mille" if q == 1 else f"{milliers} mille")
    if n:
        parts.append(_moins_de_mille(n))
    return " ".join(parts)


def _multiplier(m: re.Match) -> str:
    """« 30.1K » → « trente mille cent », « 1.5M » → « 1,5 million », « 7B » → « 7 milliards »."""
    valeur = float(m.group(1).replace(",", "."))
    suffixe = m.group(2).lower()
    if suffixe == "k":
        return en_lettres(round(valeur * 1000))
    mot = "million" if suffixe == "m" else "milliard"
    return f"{valeur:g}".replace(".", ",") + f" {mot}{'s' if valeur >= 2 else ''}"


def _nombres_parles(texte: str) -> str:
    """
    Écrit les nombres comme on les dit en français.

    Gemini normalisait seul ; Qwen3 lit ce qu'on lui donne. Mesuré le 28/10/2026
    sur une narration : « 30.1K heures » entendu « 30 points cas, heure ». Le
    point décimal ne devient virgule que sur un nombre isolé : « GPT-5.6 » et
    « Qwen3.5 » sont des noms, qui se disent avec « point ». Un signe collé à un
    mot (« GPT-5 ») ou à un chiffre (« 2024-2026 ») n'est pas un moins.
    """
    texte = re.sub(r"(?<![\w.,-])(\d+(?:[.,]\d+)?)\s?(k|K|M|Md|B)(?!\w)", _multiplier, texte)
    # Milliers séparés par une espace (« 30 100 »), ou entier nu de cinq chiffres
    # et plus ; les années, quatre chiffres collés, Qwen3 les lit bien.
    texte = re.sub(r"(?<![\w.,])(\d{1,3}(?:[   ]\d{3})+|\d{5,})(?![\w]|[.,]\d)",
                   lambda m: en_lettres(int(re.sub(r"\D", "", m.group(1)))), texte)
    # « 2.0 » est une version (« RoboTwin 2.0 »), pas une décimale : elle garde son point.
    texte = re.sub(r"(?<![\w.-])(\d+)\.(\d+)(?![\w.])",
                   lambda m: m.group(0) if m.group(2) == "0" else f"{m.group(1)},{m.group(2)}", texte)
    texte = re.sub(r"(?<!\w)[x×]\s?(\d)", r"fois \1", texte)
    texte = re.sub(r"(?<![\w\d])-(\d)", r"moins \1", texte)
    return re.sub(r"(?<![\w\d])\+(\d)", r"plus \1", texte)


def prononcer(texte: str, termes_anglais: "list[str] | tuple" = ()) -> str:
    """
    Adapte le texte à la lecture par Qwen3 : pluriels muets, graphie phonétique.

    Appliqué au seul texte envoyé au moteur : le karaoké et les slides gardent
    l'orthographe d'origine. Qwen3 lisait « Tmux » comme un mot inconnu —
    « TMUKISS », « TEMX » à la transcription — et « Ti-max » est entendu « T-Max »
    à chaque occurrence, mesuré le 24/10/2026. Les pluriels passent d'abord : le
    dictionnaire est écrit au singulier.
    """
    texte = _nombres_parles(_pluriels_muets(texte, termes_anglais))
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
            "temperature": TEMPERATURE,
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
    filtres = ("silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05,"
               f"afade=t=in:d=0.01,adelay={int(ATTAQUE_S * 1000)}:all=1,"
               f"apad=pad_dur={PAUSE_FIN_S}")
    resultat = subprocess.run(
        [find_ffmpeg(), "-y", "-i", str(source),
         "-af", filtres, "-b:a", DEBIT_MP3, str(sortie)],
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
    voix = voix or os.getenv(VOIX_ENV) or VOIX_DEFAUT
    taches = _taches(textes, voix)

    with tempfile.TemporaryDirectory(prefix="wan2gp_") as travail:
        travail = Path(travail)
        logger.info("Qwen3 via Wan2GP : %d segment(s), voix %s", len(textes), voix)
        journal = _executer(taches, travail)
        for rang, sortie in enumerate(sorties):
            _en_mp3(_produit(travail, rang, textes, "segment", journal), sortie)


def generer_images(prompts: list[str], sorties: list[Path],
                   modele: str = MODELE_IMAGE, resolution: str = RESOLUTION_IMAGE,
                   reference: "Path | None" = None) -> list[Path]:
    """
    Génère une image par description, en un lancement de Wan2GP.

    Args:
        prompts:    Descriptions complètes, consigne de style comprise
        sorties:    Chemins sans extension : Wan2GP choisit le format, le chemin
                    rendu porte le bon suffixe
        modele:     Z-Image pour les fonds, Flux 2 Klein pour les scènes
        resolution: « LxH », multiples de 16
        reference:  Personnage à reprendre dans chaque image (la mascotte)

    Returns:
        Les images produites, dans l'ordre des descriptions

    Raises:
        RuntimeError: Wan2GP absent, en échec, ou une image manquante
    """
    if len(prompts) != len(sorties):
        raise ValueError("Une sortie par description")
    taches = []
    for rang, prompt in enumerate(prompts):
        tache = {
            "model_type": modele,
            "prompt": prompt,
            "resolution": resolution,
            # Graine fixe : une description donne la même image, à l'imprécision
            # près des calculs GPU — c'est le cache disque qui garantit le rendu.
            "seed": GRAINE,
            "output_filename": f"{rang:03d}",
        }
        if reference is not None:
            tache |= {"video_prompt_type": "I", "image_refs": [str(reference.resolve())]}
        taches.append(tache)
    rendues: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="wan2gp_") as travail:
        travail = Path(travail)
        logger.info("%s via Wan2GP : %d image(s) en %s", modele, len(prompts), resolution)
        journal = _executer(taches, travail)
        for rang, sortie in enumerate(sorties):
            produit = _produit(travail, rang, prompts, "image", journal)
            cible = sortie.with_suffix(produit.suffix)
            cible.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(produit), cible)
            rendues.append(cible)
    return rendues


def animer(images: list[Path], mouvements: list[str], sorties: list[Path]) -> list[Path]:
    """
    Anime chaque image selon sa description de mouvement, en un lancement.

    Args:
        images:     Images de départ (les scènes)
        mouvements: Ce qui se passe pendant le clip, en anglais
        sorties:    MP4 à écrire, dans le même ordre

    Raises:
        RuntimeError: Wan2GP absent, en échec, ou un clip manquant
    """
    if not len(images) == len(mouvements) == len(sorties):
        raise ValueError("Une image, un mouvement et une sortie par clip")
    taches = [
        {
            "model_type": MODELE_ANIMATION,
            "prompt": mouvement,
            "image_prompt_type": "S",
            "image_start": str(Path(image).resolve()),
            "resolution": RESOLUTION_SCENE,
            "video_length": IMAGES_ANIMATION,
            "seed": GRAINE,
            "output_filename": f"{rang:03d}",
        }
        for rang, (image, mouvement) in enumerate(zip(images, mouvements))
    ]
    with tempfile.TemporaryDirectory(prefix="wan2gp_") as travail:
        travail = Path(travail)
        logger.info("Animation via Wan2GP : %d clip(s) de %d images", len(images), IMAGES_ANIMATION)
        journal = _executer(taches, travail, DELAI_PAR_CLIP_S)
        for rang, sortie in enumerate(sorties):
            sortie.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(_produit(travail, rang, mouvements, "clip", journal)), sortie)
    return sorties


def _executer(taches: list[dict], travail: Path,
              delai_par_tache_s: int = DELAI_PAR_TACHE_S) -> str:
    """
    Lance Wan2GP une fois sur toute la file ; rend son journal.

    Raises:
        RuntimeError: Wan2GP absent, en échec ou trop long
    """
    installation = _installation()
    if isinstance(installation, str):
        raise RuntimeError(f"Wan2GP indisponible : {installation}")
    dossier, python = installation
    file = travail / "taches.json"
    file.write_text(json.dumps(taches, ensure_ascii=False), encoding="utf-8")
    # Les CUDA graphs du décodeur de Qwen3-TTS plantent en v18.84 (« subtalker
    # CUDA graph decode failed: unhashable type: 'list' », 28/10/2026) : toute
    # tâche de voix échouait. Coupés par la variable que Wan2GP prévoit, que
    # l'environnement peut toujours surcharger (vide pour les rétablir).
    env = {"WGP_LLM_DISABLE": "graphs", **os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        resultat = subprocess.run(
            [str(python), "wgp.py", "--process", str(file), "--output-dir", str(travail)],
            cwd=dossier, capture_output=True, encoding="utf-8", errors="replace",
            env=env,
            timeout=DELAI_CHARGEMENT_S + delai_par_tache_s * len(taches),
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Wan2GP n'a pas fini en {e.timeout:.0f} s") from e
    journal = (resultat.stdout or "") + (resultat.stderr or "")
    if resultat.returncode != 0:
        raise RuntimeError(f"Wan2GP a échoué (code {resultat.returncode}) : {journal[-800:]}")
    return journal


def _produit(travail: Path, rang: int, entrees: list[str], nature: str,
             journal: str) -> Path:
    """
    Fichier produit par la tâche `rang`, retrouvé par son nom imposé.

    Un manque interrompt tout : poursuivre livrerait une bande son amputée ou
    une slide sans son fond, défauts que seul le visionnage révélerait.
    """
    produits = sorted(travail.glob(f"{rang:03d}.*"))
    if not produits:
        raise RuntimeError(
            f"Wan2GP n'a rien produit pour le {nature} {rang + 1}/{len(entrees)} "
            f"(« {entrees[rang][:60]} ») : {journal[-800:]}"
        )
    return produits[0]
