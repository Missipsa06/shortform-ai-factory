"""
Illustrations des slides, avec deux fournisseurs interchangeables.

Choisi par IMAGE_PROVIDER dans .env :

    pexels      Photographies libres de droits. Justes mais génériques : la même
                banque sert des milliers de chaînes.
    cloudflare  Images générées par Workers AI (FLUX.1 schnell). Palier gratuit
                de 10 000 Neurons/jour, soit environ 150 images — largement
                au-dessus du plafond réel du pipeline, fixé par le quota TTS.

Usage :
    python -m content.images --query "neural network" --provider cloudflare

Prérequis dans .env :
    PEXELS_API_KEY=...                          (gratuit sur pexels.com/api)
    CLOUDFLARE_ACCOUNT_ID=... CLOUDFLARE_API_TOKEN=...
"""

import argparse
import base64
import logging
import os
import json
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

PEXELS_API  = "https://api.pexels.com/v1/search"

# Candidats demandés à chaque recherche, pour retenir le plus sombre. Une seule
# requête HTTP quel que soit ce nombre, et le quota Pexels se compte en
# requêtes — 25 000 par mois — non en résultats.
PEXELS_CANDIDATS = 15
IMAGES_DIR  = Path("data/images")
CACHE_FILE  = IMAGES_DIR / "cache.json"

FOURNISSEURS = ("pexels", "cloudflare")
FOURNISSEUR_DEFAUT = "pexels"

# ── Cloudflare Workers AI ─────────────────────────────────────────────────────
CF_URL = "https://api.cloudflare.com/client/v4/accounts/{compte}/ai/run/{modele}"
CF_MODELE_DEFAUT = "@cf/black-forest-labs/flux-1-schnell"

# 4 étapes est la valeur par défaut du modèle et son point d'équilibre : au-delà,
# le coût en Neurons croît linéairement pour un gain visuel marginal.
CF_ETAPES = 4

# Consigne de style ajoutée à chaque prompt. C'est elle qui tient la cohérence
# d'une slide à l'autre : sans elle, huit images générées indépendamment dérivent
# en style et la vidéo paraît décousue. « no text » est indispensable — les
# modèles de diffusion écrivent des caractères illisibles dès qu'on les laisse.
CF_STYLE = (
    "minimal editorial illustration, dark navy background, single cyan accent "
    "color, clean geometric shapes, generous negative space, flat vector style, "
    "no text, no letters, no words, no watermark"
)


_FAUX = frozenset({"0", "false", "non", "no", "off"})


def _repli_actif(explicite: "bool | None" = None) -> bool:
    """
    Indique si le repli Cloudflare → Pexels est autorisé.

    Actif par défaut, contrairement au repli vocal : une photo n'engage pas
    l'identité de la chaîne autant qu'une voix, et l'alternative n'est pas une
    autre illustration mais un fond nu. Le désactiver (IMAGE_FALLBACK=0) garantit
    en revanche que toutes les vidéos partagent le même registre visuel.
    """
    if explicite is not None:
        return explicite
    return os.getenv("IMAGE_FALLBACK", "1").strip().lower() not in _FAUX


def _sauter_schemas(explicite: "bool | None" = None) -> bool:
    """
    Indique s'il faut priver d'image les slides portant un schéma.

    Inactif par défaut : chaque slide reçoit son propre visuel. Le mesure faite
    sur une vidéo complète montrait pourtant qu'une illustration derrière un
    schéma le brouille ou disparaît sous le voile — c'est un compromis assumé,
    pas un oubli. `IMAGE_SLIDES_SCHEMA=0` rétablit la restriction si le résultat
    ne convainc pas.
    """
    if explicite is not None:
        return not explicite
    return os.getenv("IMAGE_SLIDES_SCHEMA", "1").strip().lower() in _FAUX


def _resoudre_fournisseur(explicite: "str | None" = None) -> str:
    """
    Détermine le fournisseur d'images à utiliser.

    Raises:
        ValueError: Si le nom demandé est inconnu
    """
    nom = (explicite or os.getenv("IMAGE_PROVIDER") or FOURNISSEUR_DEFAUT).strip().lower()
    if nom not in FOURNISSEURS:
        raise ValueError(
            f"Fournisseur d'images inconnu : '{nom}'. "
            f"Valeurs possibles : {', '.join(FOURNISSEURS)}"
        )
    return nom


def _luminance(avg_color: str) -> float:
    """
    Luminance perçue d'une couleur `#RRGGBB`, entre 0 (noir) et 1 (blanc).

    Coefficients de la recommandation ITU-R BT.709 : l'œil est bien plus
    sensible au vert qu'au bleu, et une moyenne arithmétique des trois canaux
    classerait mal un bleu nuit face à un vert sombre.
    """
    try:
        r, g, b = (int(avg_color[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError):
        return 1.0  # couleur illisible : traitée comme claire, donc écartée
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def _api_key() -> str:
    """Récupère la clé API Pexels depuis les variables d'environnement."""
    key = os.getenv("PEXELS_API_KEY", "")
    if not key:
        raise EnvironmentError("PEXELS_API_KEY manquant dans .env (gratuit sur pexels.com/api)")
    return key


def _cf_identifiants() -> tuple[str, str]:
    """
    Récupère l'identifiant de compte et le jeton Cloudflare.

    Raises:
        EnvironmentError: Si l'un des deux manque
    """
    compte = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    jeton = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    manquants = [
        nom for nom, val in
        (("CLOUDFLARE_ACCOUNT_ID", compte), ("CLOUDFLARE_API_TOKEN", jeton))
        if not val
    ]
    if manquants:
        raise EnvironmentError(
            f"{' et '.join(manquants)} manquant(s) dans .env. "
            "Le jeton se crée sur dash.cloudflare.com avec les droits "
            "« Workers AI — Read » et « Edit » ; l'identifiant de compte "
            "s'affiche dans la section Workers AI du tableau de bord."
        )
    return compte, jeton


def _generer_cloudflare(
    query: str, output_dir: Path, filename: str, reessais: int = 2
) -> "Path | None":
    """
    Génère une illustration via Cloudflare Workers AI.

    Args:
        query:      Description du visuel attendu, en anglais
        output_dir: Dossier de destination
        filename:   Nom du fichier de sortie, sans extension
        reessais:   Tentatives restantes sur un échec passager

    Returns:
        Chemin de l'image, ou None en cas d'échec
    """
    compte, jeton = _cf_identifiants()
    modele = os.getenv("CLOUDFLARE_IMAGE_MODEL") or CF_MODELE_DEFAUT

    # Schéma vérifié contre l'API : `flux-1-schnell` n'accepte que `prompt` et
    # `steps`. `seed`, `width` et `height` sont rejetés par un 400, malgré ce que
    # laisse entendre la documentation. La génération n'est donc pas
    # reproductible côté serveur — c'est le cache disque qui joue ce rôle : une
    # image obtenue est conservée et réutilisée, si bien qu'un rendu rejoué
    # affiche exactement les mêmes visuels.
    corps = {"prompt": f"{query}. {CF_STYLE}", "steps": CF_ETAPES}

    try:
        reponse = requests.post(
            CF_URL.format(compte=compte, modele=modele),
            headers={"Authorization": f"Bearer {jeton}"},
            json=corps,
            timeout=120,
        )
    except requests.RequestException as e:
        logger.warning("Cloudflare injoignable pour '%s' : %s", query, e)
        return None

    if reponse.status_code != 200:
        # Cloudflare renvoie 429 aussi bien pour un quota épuisé que pour une
        # saturation momentanée de ses GPU (« Capacity temporarily exceeded »).
        # Le même diagnostic que pour le LLM et la voix distingue les deux.
        from content.quota import diagnostiquer

        diag = diagnostiquer(reponse.status_code, reponse.text)
        if diag.reessayable and reessais > 0:
            logger.info(
                "Cloudflare indisponible (%s) — nouvelle tentative dans %.0f s",
                diag.message, diag.delai,
            )
            time.sleep(diag.delai)
            return _generer_cloudflare(query, output_dir, filename, reessais - 1)

        logger.warning(
            "Cloudflare a échoué (%s) pour '%s' : %s",
            reponse.status_code, query, reponse.text[:200],
        )
        return None

    # FLUX renvoie l'image en base64 dans du JSON ; d'autres modèles renvoient
    # les octets bruts. On accepte les deux plutôt que de supposer.
    if reponse.headers.get("content-type", "").startswith("image/"):
        octets = reponse.content
    else:
        charge = reponse.json()
        encodee = (charge.get("result") or {}).get("image")
        if not encodee:
            logger.warning("Cloudflare n'a retourné aucune image pour '%s'", query)
            return None
        octets = base64.b64decode(encodee)

    output_dir.mkdir(parents=True, exist_ok=True)
    chemin = output_dir / f"{filename}.jpg"
    chemin.write_bytes(octets)
    logger.info("Image générée : %s -> %s (%d Ko)", query[:40], chemin.name, len(octets) // 1024)
    return chemin


def _load_cache() -> dict:
    """Charge le cache des images déjà téléchargées."""
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(cache: dict) -> None:
    """Sauvegarde le cache."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def attacher_figures_source(
    script: dict,
    output_dir: Path,
    source: str,
    source_id: str = "",
    url: str = "",
) -> int:
    """
    Télécharge les figures de l'article et les pose sur les slides qui en veulent.

    **Ce n'est pas une illustration de fond.** Les autres visuels du module
    passent sous un voile sombre et sont décrits au générateur avec la consigne
    « no text, no letters ». Une figure d'article est l'inverse : elle porte du
    texte, elle est le propos, et elle occupe le premier plan à la place du
    schéma. D'où une fonction distincte de `fetch_slide_images`.

    Le modèle a choisi ses figures **par indice**, d'après la liste de légendes
    qu'on lui a soumise ; il n'a jamais vu les images. Cette étape résout
    l'indice en chemin local, et reprend la légende et le crédit d'origine
    plutôt que de laisser le modèle les réécrire.

    Une slide dont l'indice ne correspond à rien voit son graphique retiré : le
    rendu retombe alors sur le dégradé, ce qui vaut mieux qu'une figure vide.

    Args:
        script:     Script JSON, **modifié sur place**
        output_dir: Dossier de destination, `data/images/<stem>/` en usage
        source:     Nom de la source ('arxiv', 'huggingface_blog', …)
        source_id:  Identifiant, nécessaire à arXiv
        url:        Adresse de l'article, nécessaire au blog

    Returns:
        Nombre de slides effectivement illustrées par une figure de la source
    """
    from scraper.figures import figures_de

    demandeuses = [
        slide for slide in script.get("slides", []) or []
        if ((slide.get("graphique") or {}).get("type")) == "figure_source"
    ]
    if not demandeuses:
        return 0

    disponibles = figures_de(source, source_id, url)
    if not disponibles:
        logger.warning(
            "%d slide(s) demandaient une figure de la source, aucune n'a été "
            "trouvée — retour au dégradé", len(demandeuses)
        )
        for slide in demandeuses:
            slide["graphique"] = None
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    posees = 0
    for slide in demandeuses:
        donnees = slide.get("graphique", {}).get("donnees") or {}
        try:
            indice = int(donnees.get("indice", -1))
        except (TypeError, ValueError):
            indice = -1

        if not 0 <= indice < len(disponibles):
            logger.warning("Indice de figure hors liste (%s) sur '%s' — dégradé",
                           donnees.get("indice"), slide.get("titre", "?"))
            slide["graphique"] = None
            continue

        figure = disponibles[indice]
        chemin = output_dir / f"source_{indice:02d}{_extension(figure.url)}"
        if not chemin.exists() and not _telecharger_figure(figure.url, chemin):
            slide["graphique"] = None
            continue

        slide["graphique"]["donnees"] = {
            "image": str(chemin),
            "legende": figure.legende,
            # Jamais réécrite par le modèle : le crédit est la contrepartie de
            # l'usage, il vient de l'extracteur et de lui seul.
            "attribution": figure.attribution,
        }
        posees += 1

    logger.info("%d figure(s) de la source posée(s) sur %d demande(s)",
                posees, len(demandeuses))
    return posees


def _extension(url: str) -> str:
    """Extension du fichier distant, `.png` par défaut."""
    from urllib.parse import urlparse

    suffixe = Path(urlparse(url).path).suffix.lower()
    return suffixe if suffixe in (".png", ".jpg", ".jpeg", ".webp", ".gif") else ".png"


def _telecharger_figure(url: str, cible: Path) -> bool:
    """Récupère une figure ; False sur échec, jamais d'exception."""
    try:
        reponse = requests.get(
            url, timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; shortform-ai-factory/1.0)"},
        )
        reponse.raise_for_status()
        cible.write_bytes(reponse.content)
    except Exception as e:                       # noqa: BLE001 — repli délibéré
        logger.warning("Figure non téléchargée (%s) : %s", url, e)
        return False
    return True


def fetch_image(
    query: str,
    output_dir: Path,
    filename: str,
    provider: "str | None" = None,
    fallback: "bool | None" = None,
) -> "Path | None":
    """
    Obtient une illustration pour le terme donné, depuis le fournisseur configuré.

    Args:
        query:      Mots-clés en anglais (recherche Pexels, ou description à générer)
        output_dir: Dossier de destination
        filename:   Nom du fichier de sortie (sans extension)
        provider:   Fournisseur ; None = IMAGE_PROVIDER dans .env puis défaut
        fallback:   Autoriser le repli Cloudflare → Pexels ; None = IMAGE_FALLBACK

    Returns:
        Chemin vers l'image, ou None si échec — la slide garde alors son dégradé
    """
    fournisseur = _resoudre_fournisseur(provider)
    cache = _load_cache()
    # Le fournisseur ET le modèle font partie de la clé. Sans le fournisseur,
    # basculer de Pexels vers Cloudflare rendrait l'ancienne photo. Sans le
    # modèle, changer CLOUDFLARE_IMAGE_MODEL ne changerait rien : le cache
    # servirait l'image de l'ancien modèle en la faisant passer pour la
    # nouvelle — constaté en comparant deux modèles, l'un renvoyant
    # silencieusement le rendu de l'autre.
    modele = (
        os.getenv("CLOUDFLARE_IMAGE_MODEL") or CF_MODELE_DEFAUT
        if fournisseur == "cloudflare" else fournisseur
    )
    # Le dossier de destination fait aussi partie de la clé. Sans lui, deux
    # sujets partageant une requête et un nom de slide — `slide_00` est commun à
    # toutes les vidéos — se renvoient mutuellement leurs images. Constaté : un
    # script d'expérimentation écrivant dans un dossier temporaire a fait servir
    # ses propres visuels au pipeline, jusqu'à ce que ce dossier disparaisse.
    cache_key = f"{fournisseur}::{modele}::{output_dir.as_posix()}::{query}::{filename}"

    if cache_key in cache:
        chemin_cache = Path(cache[cache_key])
        if chemin_cache.exists():
            logger.debug("Image en cache : %s", chemin_cache)
            return chemin_cache

    if fournisseur == "cloudflare":
        chemin = _generer_cloudflare(query, output_dir, filename)
        if chemin:
            cache[cache_key] = str(chemin)
            _save_cache(cache)
            return chemin

        if not _repli_actif(fallback):
            return None

        # Le quota de génération est journalier et se consomme vite ; une photo
        # vaut mieux qu'un fond nu. Le repli est annoncé en WARNING : la slide
        # titre changera visiblement de registre, et ça ne doit pas se découvrir
        # au montage.
        logger.warning(
            "Génération indisponible pour « %s » — repli sur Pexels", query[:50]
        )
        cle_repli = f"pexels::pexels::{output_dir.as_posix()}::{query}::{filename}"
        if cle_repli in cache and Path(cache[cle_repli]).exists():
            return Path(cache[cle_repli])
        try:
            return _recuperer_pexels(query, output_dir, filename, cache, cle_repli)
        except EnvironmentError as e:
            # Sans clé Pexels, il ne reste que le dégradé — ce n'est pas une
            # panne, c'est le dernier échelon de repli.
            logger.warning("Repli Pexels impossible (%s) — la slide gardera son dégradé", e)
            return None

    return _recuperer_pexels(query, output_dir, filename, cache, cache_key)


def _recuperer_pexels(
    query: str,
    output_dir: Path,
    filename: str,
    cache: dict,
    cache_key: str,
) -> "Path | None":
    """
    Télécharge une photographie portrait sombre depuis Pexels.

    Parmi les candidats retournés, retient **le plus sombre** plutôt que le
    premier. Le fond passe sous un voile et sert de support à du texte blanc :
    une photo claire écrase le titre et le bandeau. Mesuré sur trois requêtes,
    la luminance moyenne des candidats s'étale de 0,10 à 0,85 — prendre le
    premier revenait à tirer au sort dans cet intervalle.

    Une seule requête HTTP suffit : `per_page` ne coûte pas davantage.

    Returns:
        Chemin vers l'image téléchargée, ou None si échec
    """
    # Le cache a déjà été consulté par fetch_image : on ne le refait pas ici.
    try:
        resp = requests.get(
            PEXELS_API,
            headers={"Authorization": _api_key()},
            params={
                "query":       query,
                "per_page":    PEXELS_CANDIDATS,
                "orientation": "portrait",
                "size":        "large",
            },
            timeout=15,
        )
        data = resp.json()
        photos = data.get("photos", [])
        if not photos:
            logger.warning("Aucune image trouvée pour : %s", query)
            return None

        photo = min(photos, key=lambda p: _luminance(p.get("avg_color", "#FFFFFF")))
        logger.info(
            "Pexels : %d candidats, retenu L=%.2f (%s)",
            len(photos), _luminance(photo.get("avg_color", "#FFFFFF")),
            photo.get("avg_color", "?"),
        )
        img_url = photo["src"]["large"]  # 940px large

        img_resp = requests.get(img_url, timeout=30)
        img_resp.raise_for_status()

        output_dir.mkdir(parents=True, exist_ok=True)
        img_path = output_dir / f"{filename}.jpg"
        img_path.write_bytes(img_resp.content)

        cache[cache_key] = str(img_path)
        _save_cache(cache)

        logger.info("Image téléchargée : %s → %s", query, img_path.name)
        return img_path

    except EnvironmentError:
        raise
    except Exception as e:
        logger.warning("Erreur Pexels pour '%s' : %s", query, e)
        return None


def _requete(entree: dict, script: dict, fournisseur: str) -> str:
    """
    Choisit la description à envoyer, selon la nature du fournisseur.

    Les deux n'attendent pas la même chose : Pexels indexe des photographies et
    veut deux ou trois mots-clés, un modèle génératif veut une composition
    décrite. Le champ dédié prime, les mots-clés servent de repli pour les
    scripts produits avant son introduction.
    """
    donnees = entree["donnees"] or {}

    if entree["genre"] == "contenu":
        dedie, repli = donnees.get("illustration"), donnees.get("mots_cles_image")
    elif entree["genre"] == "titre":
        dedie, repli = (
            script.get("illustration_titre"), script.get("mots_cles_image_titre")
        )
    else:
        # L'accueil et la conclusion n'ont pas de champ propre : leur requête est
        # dérivée du titre de la vidéo, avec une nuance distincte. Reprendre tel
        # quel le prompt du titre donnerait trois fois le même visuel.
        titre = script.get("titre_video", "") or script.get("titre_original", "")
        nuance = {
            "intro": "opening atmosphere, wide establishing mood",
            "conclusion": "closing atmosphere, calm resolution",
        }[entree["genre"]]
        return f"{titre} {nuance}".strip()

    if fournisseur == "cloudflare" and (dedie or "").strip():
        return dedie.strip()
    return (repli or dedie or "").strip()


def fetch_slide_images(
    script: dict,
    output_dir: Path,
    provider: "str | None" = None,
    fallback: "bool | None" = None,
    schemas: "bool | None" = None,
) -> dict[str, Path]:
    """
    Obtient une illustration **distincte** pour chaque slide.

    Chaque slide reçoit un visuel tiré de **son propre** contenu : le champ
    `illustration` pour les slides de contenu, `illustration_titre` pour la
    slide d'ouverture, une variation du titre de la vidéo pour l'accueil et la
    conclusion. Deux slides ne partagent jamais la même image.

    **Les identifiants viennent de `plan_slides`, jamais du champ `numero`.**
    Les reconstruire décalait chaque illustration d'un cran dès qu'une slide
    d'introduction était présente : l'image de la première slide de contenu
    s'affichait sur l'accueil, et les deux dernières slides n'en recevaient
    aucune. Même piège que pour l'appariement des durées audio.

    Args:
        script:     Script JSON généré par llm.py
        output_dir: Dossier de destination des images
        provider:   Fournisseur ; None = IMAGE_PROVIDER dans .env puis défaut
        fallback:   Autoriser le repli Cloudflare → Pexels ; None = IMAGE_FALLBACK
        schemas:    Illustrer aussi les slides portant un schéma ;
                    None = IMAGE_SLIDES_SCHEMA, actif par défaut

    Returns:
        Dictionnaire {slide_id: image_path}
    """
    from content.slides import plan_slides

    fournisseur = _resoudre_fournisseur(provider)
    images: dict[str, Path] = {}
    vues: dict[str, str] = {}

    for entree in plan_slides(script):
        donnees = entree["donnees"] or {}
        if _sauter_schemas(schemas) and (donnees.get("graphique") or {}).get("type"):
            continue

        requete = _requete(entree, script, fournisseur)
        if not requete:
            logger.warning("Aucune requête pour %s — dégradé", entree["id"])
            continue

        # Deux slides ne doivent jamais partager un visuel. Le LLM répète parfois
        # une description d'une slide à l'autre ; on écarte le doublon plutôt que
        # d'afficher deux fois la même image dans une vidéo d'une minute.
        if requete in vues:
            logger.warning(
                "%s reprend la requête de %s — dégradé pour éviter le doublon",
                entree["id"], vues[requete],
            )
            continue
        vues[requete] = entree["id"]

        chemin = fetch_image(requete, output_dir, entree["id"], fournisseur, fallback)
        if chemin:
            images[entree["id"]] = chemin

    logger.info(
        "%d illustration(s) distinctes obtenues via %s", len(images), fournisseur
    )
    return images


def main() -> None:
    """Point d'entrée CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Illustrations des slides")
    parser.add_argument("--query", required=True, help="Mots-cles, en anglais")
    parser.add_argument("--output", default="data/images/", help="Dossier de sortie")
    parser.add_argument("--filename", default="image", help="Nom du fichier")
    parser.add_argument(
        "--provider", choices=FOURNISSEURS,
        help="Fournisseur (defaut : IMAGE_PROVIDER dans .env, sinon pexels)",
    )
    args = parser.parse_args()

    path = fetch_image(args.query, Path(args.output), args.filename, args.provider)
    if path:
        logger.info("Image : %s", path)
    else:
        logger.error("Échec du téléchargement")


if __name__ == "__main__":
    main()
