"""
Configuration et implémentation des scrapers par source.

Techniques   : Arxiv, HuggingFace (blog, modèles et Daily Papers en tendance),
               Hacker News — les quatre dernières portent une popularité.
Non techniques : Montreal AI Ethics (CC BY), NIST (domaine public) — voir la
section « Sources non techniques » pour le critère de licence qui les a
sélectionnées, et qui en a écarté une vingtaine d'autres.
"""

import html
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CreationContenuBot/1.0; educational use)"
}
TIMEOUT = 15  # secondes


# ---------------------------------------------------------------------------
# Arxiv
# ---------------------------------------------------------------------------

ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "stat.ML"]
ARXIV_API = "http://export.arxiv.org/api/query"


def scrape_arxiv(limit: int = 10) -> Iterator[dict]:
    """
    Scrape les derniers papiers Arxiv sur les catégories IA/ML.

    Yields:
        dict avec les champs : source_id, title, url, summary, published, tags
    """
    categories = " OR ".join(f"cat:{c}" for c in ARXIV_CATEGORIES)
    params = {
        "search_query": categories,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": limit,
    }

    logger.info("Scraping Arxiv (limit=%d)...", limit)
    try:
        resp = requests.get(ARXIV_API, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Erreur Arxiv : %s", e)
        return

    soup = BeautifulSoup(resp.text, "xml")
    entries = soup.find_all("entry")
    logger.info("Arxiv : %d entrées trouvées", len(entries))

    for entry in entries:
        arxiv_id = entry.find("id").text.strip().split("/abs/")[-1]
        title = entry.find("title").text.strip().replace("\n", " ")
        summary = entry.find("summary").text.strip().replace("\n", " ")
        published = entry.find("published").text.strip()[:10]
        url = f"https://arxiv.org/abs/{arxiv_id}"
        tags = [c.get("term", "") for c in entry.find_all("category")]

        yield {
            "source": "arxiv",
            "source_id": arxiv_id,
            "title": title,
            "url": url,
            "summary": summary,
            "published": published,
            "tags": tags,
            "raw": {"id": arxiv_id, "title": title, "summary": summary, "tags": tags},
        }


# ---------------------------------------------------------------------------
# HuggingFace Blog
# ---------------------------------------------------------------------------

# (adresse, clé de la liste dans la réponse) : les deux API ne nomment pas
# leur liste pareil. Le blog communautaire est celui du seul succès mesuré.
HF_API_BLOGS = (
    ("https://huggingface.co/api/blog", "allBlogs"),
    ("https://huggingface.co/api/blog/community", "posts"),
)

# Le corps de l'article, par ordre de préférence. Plusieurs sélecteurs plutôt
# qu'un seul : si HuggingFace change son gabarit, la source se dégrade au lieu
# de se taire.
HF_SELECTEURS_CORPS = ("div.blog-content", "div.prose", "main")

# De quoi nourrir le LLM (qui tronque à 3000) et un futur découpage en passages,
# sans stocker les pieds de page ni les sections de commentaires.
HF_EXTRAIT_MAX = 5000

# Une requête par article : on ralentit un peu pour rester correct vis-à-vis du site.
HF_DELAI = 0.4


_HF_MOIS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
_HF_DATE_RE = re.compile(r"Published\s+([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})")

# Chrome de page qui précède le texte : lien de retour et ligne de publication.
# Sans ce nettoyage, les 200 premiers caractères envoyés au LLM sont du bruit —
# et comme le prompt tronque à 3000, c'est autant de matière utile perdue.
_HF_ENTETE_RE = re.compile(
    r"^\s*(?:Back to Articles\s*)?(?:Community Article\s*)?"
    r"(?:Published\s+[A-Za-z]+\s+\d{1,2},\s*\d{4}\s*)?",
    re.IGNORECASE,
)


def _hf_date(bloc) -> "str | None":
    """Extrait la date de publication au format ISO, si elle est présente."""
    trouve = _HF_DATE_RE.search(bloc.get_text(" ", strip=True)[:400])
    if not trouve:
        return None
    mois = _HF_MOIS.get(trouve.group(1).lower())
    if not mois:
        return None
    return f"{trouve.group(3)}-{mois:02d}-{int(trouve.group(2)):02d}"


def _hf_contenu(url: str) -> "tuple[str, str | None]":
    """
    Récupère le corps d'un article du blog HuggingFace, et sa date.

    Une requête supplémentaire par article est nécessaire : la page de liste ne
    porte aucun résumé — mesuré, zéro balise `<p>` par carte — et les balises
    meta sont génériques (« A Blog post by X on Hugging Face »), identiques pour
    tous les articles. Le texte n'existe que sur la page de l'article, qui a le
    bon goût d'être rendue côté serveur.

    Args:
        url: Adresse de l'article

    Returns:
        (texte tronqué, date ISO ou None) ; texte vide si illisible
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Article HuggingFace illisible (%s) : %s", url, e)
        return "", None

    soup = BeautifulSoup(resp.text, "html.parser")
    for selecteur in HF_SELECTEURS_CORPS:
        bloc = soup.select_one(selecteur)
        if not bloc:
            continue

        publie = _hf_date(bloc)

        # Retirés avant extraction : compteurs de votes, cartes d'auteur et
        # boutons « Follow » portent la classe `not-prose`, et le `h1` répète un
        # titre déjà capturé depuis la page de liste.
        for parasite in bloc.select(".not-prose"):
            parasite.decompose()
        for titre in bloc.find_all("h1"):
            titre.decompose()

        texte = " ".join(bloc.get_text(" ", strip=True).split())
        texte = _HF_ENTETE_RE.sub("", texte, count=1).strip()

        # Un bloc trouvé mais quasi vide signale un mauvais sélecteur, pas un
        # article court : on essaie le suivant.
        if len(texte) >= 200:
            return texte[:HF_EXTRAIT_MAX], publie

    logger.warning(
        "Aucun corps exploitable sur %s — le gabarit du blog a peut-être changé", url
    )
    return "", None


def _json(url: str, params: "dict | None" = None) -> "dict | list | None":
    """Réponse JSON d'une API, None si elle est injoignable ou illisible."""
    try:
        reponse = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        reponse.raise_for_status()
        return reponse.json()
    except (requests.RequestException, ValueError) as e:
        logger.error("API illisible (%s) : %s", url, e)
        return None


def scrape_huggingface(limit: int = 10) -> Iterator[dict]:
    """
    Articles du blog HuggingFace **en tendance**, officiels et communautaires.

    Le classement par date les rendait tous égaux. Or le seul succès mesuré du
    projet — « Qwen3.8-27B-pi », 140 vues et 9 likes en deux heures — était 5ᵉ
    du classement « trending » du blog communautaire le jour où il a été
    relevé. Les votes des sept derniers jours (`upvotes7d`) deviennent la
    popularité de l'article ; c'est elle que `sujet_depuis_veille` classe.

    Le `source_id` reste le seul slug, comme avant : les articles déjà en base
    sont reconnus et ne reviennent pas en doublon.

    Yields:
        dict : source, source_id, title, url, summary, published, tags, popularite
    """
    logger.info("Scraping HuggingFace Blog en tendance (limit=%d)...", limit)
    billets = []
    for api, cle in HF_API_BLOGS:
        reponse = _json(api, {"sort": "trending", "limit": limit})
        billets += (reponse or {}).get(cle, [])
    billets.sort(key=lambda b: b.get("upvotes7d", 0), reverse=True)
    logger.info("HuggingFace Blog : %d articles en tendance", len(billets))

    for billet in billets[:limit]:
        url = f"https://huggingface.co{billet['url']}"
        titre = billet["title"]
        summary, publie = _hf_contenu(url)
        if not summary:
            # Sans contenu, l'article ferait inventer le LLM : mieux vaut ne pas
            # l'enregistrer que de polluer la file de sujets.
            logger.info("Article ignoré, sans contenu : %s", titre[:60])
            continue
        time.sleep(HF_DELAI)

        yield {
            "source": "huggingface_blog",
            "source_id": billet["slug"],
            "title": titre,
            "url": url,
            "summary": summary,
            "published": (billet.get("publishedAt") or "")[:10] or publie,
            "tags": ["huggingface", "nlp"],
            "popularite": billet.get("upvotes7d", 0),
            "raw": billet,
        }


# ---------------------------------------------------------------------------
# HuggingFace — modèles en tendance
# ---------------------------------------------------------------------------

HF_API_MODELES = "https://huggingface.co/api/models"

# Le haut du classement est régulièrement occupé par des variantes « sans
# censure » de modèles d'image : relevé le 07/10/2026, une sur six. Hors sujet
# pour une chaîne de vulgarisation grand public.
HF_MODELE_EXCLU = re.compile(r"uncensor|abliterat|nsfw|porn|erotic", re.IGNORECASE)


def _texte_fiche(markdown: str) -> str:
    """
    Texte lisible d'une fiche de modèle (README).

    Retire l'en-tête YAML, les images, le HTML et la syntaxe des liens, dont
    seul le libellé est gardé : le LLM tronque, chaque caractère de balisage
    est un caractère de matière en moins.
    """
    texte = re.sub(r"\A---.*?\n---\s*\n", "", markdown, flags=re.DOTALL)
    texte = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", texte)
    texte = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", texte)
    texte = re.sub(r"<[^>]+>", " ", texte)
    return " ".join(html.unescape(texte).split())[:HF_EXTRAIT_MAX]


def scrape_hf_modeles(limit: int = 10) -> Iterator[dict]:
    """
    Modèles en tendance sur HuggingFace — le genre « nouveauté qui fait le
    buzz », celui du seul succès mesuré.

    Le contenu est la fiche du modèle, écrite par ses auteurs : chiffres,
    usages, matériel requis. La licence est celle que déclare le dépôt ; une
    clause « ND » interdirait l'adaptation en vidéo, le modèle est alors écarté
    (même critère que pour les flux RSS, voir plus bas).

    Yields:
        dict : source, source_id, title, url, summary, published, tags,
        licence, popularite
    """
    logger.info("Scraping HuggingFace modèles en tendance (limit=%d)...", limit)
    # Le double : une part sera écartée par le filtre ou faute de fiche.
    modeles = _json(HF_API_MODELES, {
        "sort": "trendingScore", "limit": limit * 2, "cardData": "true",
    }) or []

    retenus = 0
    for modele in modeles:
        if retenus >= limit:
            break
        ident = modele["id"]
        licence = str((modele.get("cardData") or {}).get("license") or "")
        if (HF_MODELE_EXCLU.search(ident)
                or "not-for-all-audiences" in modele.get("tags", [])
                or "-nd" in licence.lower()):
            logger.info("Modèle écarté : %s", ident)
            continue

        try:
            fiche = requests.get(f"https://huggingface.co/{ident}/raw/main/README.md",
                                 headers=HEADERS, timeout=TIMEOUT)
            fiche.raise_for_status()
        except requests.RequestException as e:
            logger.info("Modèle ignoré, fiche illisible (%s) : %s", ident, e)
            continue
        summary = _texte_fiche(fiche.text)
        if len(summary) < 200:
            logger.info("Modèle ignoré, fiche trop courte : %s", ident)
            continue
        time.sleep(HF_DELAI)

        retenus += 1
        yield {
            "source": "huggingface_modeles",
            "source_id": "hf_" + ident.replace("/", "_"),
            "title": ident,
            "url": f"https://huggingface.co/{ident}",
            "summary": summary,
            "published": (modele.get("createdAt") or "")[:10] or None,
            "tags": ["huggingface", "modèle", modele.get("pipeline_tag") or ""],
            "licence": licence,
            "popularite": int(modele.get("trendingScore") or 0),
            "raw": {k: modele.get(k) for k in
                    ("id", "likes", "trendingScore", "createdAt", "pipeline_tag")},
        }


# ---------------------------------------------------------------------------
# HuggingFace Daily Papers — arXiv trié par votes
# ---------------------------------------------------------------------------

HF_API_PAPERS = "https://huggingface.co/api/daily_papers"


def scrape_hf_papers(limit: int = 10) -> Iterator[dict]:
    """
    Papiers arXiv sélectionnés et votés par la communauté HuggingFace.

    `scrape_arxiv` prend les dernières soumissions, sans aucun signal d'intérêt :
    sur quelques centaines par jour, rien ne distingue le papier dont on parle
    de celui que personne ne lira. Les Daily Papers en retiennent quelques
    dizaines et les font voter.

    Les articles sont enregistrés sous la source **arxiv** et non sous un nom
    propre : c'est un papier arXiv, avec sa licence, ses figures
    (`scraper/figures.py` ne connaît qu'`arxiv`) et son identifiant. HuggingFace
    n'apporte que le vote, qui devient la popularité.

    Yields:
        dict : source, source_id, title, url, summary, published, tags, popularite
    """
    logger.info("Scraping HuggingFace Daily Papers (limit=%d)...", limit)
    papiers = [p["paper"] for p in (_json(HF_API_PAPERS, {"limit": 100}) or [])]
    papiers.sort(key=lambda p: p.get("upvotes", 0), reverse=True)

    for papier in papiers[:limit]:
        yield {
            "source": "arxiv",
            "source_id": papier["id"],
            "title": " ".join(papier["title"].split()),
            "url": f"https://arxiv.org/abs/{papier['id']}",
            "summary": " ".join((papier.get("summary") or "").split()),
            "published": (papier.get("publishedAt") or "")[:10] or None,
            "tags": ["arxiv", "hf-daily-papers"],
            "popularite": papier.get("upvotes", 0),
            "raw": {k: papier.get(k) for k in ("id", "title", "upvotes", "publishedAt")},
        }


# ---------------------------------------------------------------------------
# Hacker News — discussions IA les plus votées de la semaine
# ---------------------------------------------------------------------------

HN_API = "https://hn.algolia.com/api/v1/search"
HN_POINTS_MIN = 100
HN_FENETRE = 7 * 86400  # secondes

# Hacker News couvre toute la tech : sur une semaine de discussions à plus de
# 200 points, une recherche « LLM » remontait aussi des pilotes de F1 et des
# lampes. Plus étroit que `NIST_MOTIF_IA` : « algorithm » y est du vocabulaire
# courant, et le motif nomme les modèles, qui font les titres ici.
HN_MOTIF_IA = re.compile(
    r"\b(AI|LLMs?|GPT[\w.-]*|Claude|Gemini|Qwen[\w.-]*|Llama[\w.-]*|Mistral|"
    r"DeepSeek|Anthropic|OpenAI|open[- ]weights?|machine learning|neural|"
    r"transformers?|diffusion|fine[- ]tun\w*|inference)\b",
    re.IGNORECASE,
)
HN_SELECTEURS = ("article", "main", "div.markdown-body", "body")


def scrape_hacker_news(limit: int = 10) -> Iterator[dict]:
    """
    Sujets IA les plus discutés de la semaine sur Hacker News.

    C'est le plus fort signal de buzz relevé : sorties de modèles et astuces
    (« faire tourner un modèle de 125 milliards de paramètres sur une RTX 4090 »)
    y dépassent les 900 points. Les points deviennent la popularité.

    Le contenu est la page liée, suivie comme pour les flux RSS. **Sa licence
    est inconnue** — Hacker News ne fait que pointer vers des sites tiers, dont
    les grands titres tous droits réservés que ce module écarte ailleurs. Elle
    reste donc vide, comme pour le blog HuggingFace : la vérification se fait à
    la validation humaine, avant publication. Une discussion sans lien (« Ask
    HN ») apporte son propre texte.

    Yields:
        dict : source, source_id, title, url, summary, published, tags, popularite
    """
    logger.info("Scraping Hacker News (limit=%d)...", limit)
    depuis = int(time.time()) - HN_FENETRE
    reponse = _json(HN_API, {
        "tags": "story", "hitsPerPage": 300,
        "numericFilters": f"points>={HN_POINTS_MIN},created_at_i>{depuis}",
    }) or {}
    discussions = [h for h in reponse.get("hits", [])
                   if HN_MOTIF_IA.search(h.get("title") or "")]
    discussions.sort(key=lambda h: h.get("points", 0), reverse=True)
    logger.info("Hacker News : %d discussions IA à %d points ou plus",
                len(discussions), HN_POINTS_MIN)

    retenus = 0
    for hit in discussions:
        if retenus >= limit:
            break
        fil = f"https://news.ycombinator.com/item?id={hit['objectID']}"
        lien = hit.get("url")
        if lien:
            corps = _corps_distant(lien, HN_SELECTEURS)
            time.sleep(RSS_DELAI)
        else:
            corps = " ".join(html.unescape(
                re.sub(r"<[^>]+>", " ", hit.get("story_text") or "")).split())
        if len(corps) < 200:
            logger.info("Discussion ignorée, sans contenu : %s", hit["title"][:60])
            continue

        retenus += 1
        yield {
            "source": "hacker_news",
            "source_id": f"hn_{hit['objectID']}",
            "title": hit["title"],
            "url": lien or fil,
            "summary": corps[:HF_EXTRAIT_MAX],
            "published": (hit.get("created_at") or "")[:10] or None,
            "tags": ["hacker-news"],
            "popularite": hit.get("points", 0),
            "raw": {"fil": fil, **{k: hit.get(k) for k in
                                   ("objectID", "title", "url", "points", "num_comments")}},
        }


# Papers With Code a été retiré : son API a fermé et ne renvoyait plus rien.
# Un article déjà en base sous ce nom retombe sur PROFIL_INCONNU, classé
# « technique » comme il l'était.


# ---------------------------------------------------------------------------
# Sources non techniques — flux RSS sous licence libre
# ---------------------------------------------------------------------------
#
# **Pourquoi seulement deux.** Vingt-trois flux ont été sondés ; le critère qui
# a éliminé les autres n'est pas la disponibilité mais la licence. Ce pipeline
# reformule, traduit et adapte en vidéo : il produit une œuvre dérivée. Toute
# licence portant « ND » (No Derivatives) l'interdit donc, aussi généreuse
# paraisse-t-elle — The Conversation, en CC BY-ND, proscrit explicitement la
# traduction et l'usage par un système d'IA. Numerama (CC BY-NC-ND) tombe pour
# la même raison, AlgorithmWatch (CC BY-NC-SA) a été écarté par choix : le
# « NC » interdirait la monétisation et le « SA » contaminerait la licence de
# la vidéo produite. Restent le CC BY et le domaine public.
#
# Les grands titres (MIT Tech Review, The Verge, Ars Technica) restent hors
# d'atteinte : tous droits réservés. Leurs *faits* ne sont pas protégeables,
# mais les collecter automatiquement engage au-delà de ce que ce module doit
# décider seul.

RSS_DELAI = 0.4  # politesse entre deux pages suivies


def _texte_flux(noeud, *balises: str) -> str:
    """
    Premier des `balises` qui porte du texte, nettoyé de son balisage.

    Les entités HTML sont converties : un flux RSS encode ses guillemets en
    `&quot;`, et le parseur XML ne les développe pas. Ce titre finit à l'écran
    dans la vidéo, où « Announcing the &quot;AI Agent Standards&quot; » se lit
    tel quel — observé sur le flux du NIST.
    """
    for balise in balises:
        trouve = noeud.find(balise)
        if trouve is not None and (trouve.text or "").strip():
            sans_balise = re.sub(r"<[^>]+>", " ", trouve.text)
            return " ".join(html.unescape(sans_balise).split())
    return ""


def _corps_distant(url: str, selecteurs: tuple[str, ...]) -> str:
    """
    Corps d'un article, suivi depuis son lien.

    Les flux RSS ne portent qu'un extrait — 128 caractères en médiane chez
    Montreal AI Ethics, 130 chez NIST — là où `RESUME_MINIMUM` en exige 200.
    Suivre le lien coûte une requête par article, comme `_hf_contenu` le fait
    déjà pour le blog HuggingFace, et rend l'article exploitable : mesuré à
    14 793 caractères sur le premier billet de Montreal AI Ethics.
    """
    try:
        reponse = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        reponse.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Page illisible (%s) : %s", url, e)
        return ""
    # Hacker News pointe aussi vers des PDF et des vidéos : parsé comme du
    # HTML, un PDF donne des milliers de caractères de bruit qui passent le
    # seuil de longueur et partiraient au LLM comme un résumé.
    if "html" not in reponse.headers.get("Content-Type", "text/html"):
        logger.info("Page ignorée, pas du HTML (%s) : %s",
                    reponse.headers.get("Content-Type"), url)
        return ""

    soup = BeautifulSoup(reponse.text, "html.parser")
    # Le chrome de page pollue les premiers caractères envoyés au LLM, et le
    # prompt tronque : c'est autant de matière utile perdue. Même nettoyage
    # que pour le blog HuggingFace.
    for parasite in soup.select("nav, header, footer, script, style, .not-prose"):
        parasite.decompose()
    for selecteur in selecteurs:
        noeud = soup.select_one(selecteur)
        if noeud:
            texte = " ".join(noeud.get_text(" ", strip=True).split())
            if len(texte) >= 200:
                return texte[:HF_EXTRAIT_MAX]
    return ""


def _articles_rss(url: str) -> list:
    """Entrées d'un flux RSS, liste vide si le flux est injoignable."""
    try:
        reponse = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        reponse.raise_for_status()
        return ET.fromstring(reponse.content).findall(".//item")
    except (requests.RequestException, ET.ParseError) as e:
        logger.error("Flux illisible (%s) : %s", url, e)
        return []


def _date_rss(brut: str) -> "str | None":
    """Date RFC-822 d'un flux → ISO, le format des autres sources."""
    for gabarit in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.strptime(brut.strip(), gabarit).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


MONTREAL_FLUX = "https://montrealethics.ai/feed/"
MONTREAL_LICENCE = "CC BY 4.0"
MONTREAL_SELECTEURS = ("div.entry-content", "article", "main")


def scrape_montreal_ai_ethics(limit: int = 10) -> Iterator[dict]:
    """
    Billets du Montreal AI Ethics Institute — gouvernance, société, critique.

    Comble le manque des trois sources historiques, toutes techniques : celle-ci
    traite de gouvernance de l'IA, d'armes autonomes ou de poids ouverts, sujets
    qu'aucun papier arXiv ne rend accessibles au grand public.

    Licence CC BY 4.0, vérifiée sur leur page « about » : la reformulation, la
    traduction et l'adaptation en vidéo sont permises, à charge de citer.

    Yields:
        dict : source, source_id, title, url, summary, published, tags, licence
    """
    logger.info("Scraping Montreal AI Ethics (limit=%d)...", limit)
    for item in _articles_rss(MONTREAL_FLUX)[:limit]:
        url = (item.findtext("link") or "").strip()
        titre = _texte_flux(item, "title")
        if not url or not titre:
            continue

        corps = _corps_distant(url, MONTREAL_SELECTEURS)
        if not corps:
            logger.info("Billet ignoré, sans contenu : %s", titre[:60])
            continue
        time.sleep(RSS_DELAI)

        identifiant = url.rstrip("/").split("/")[-1]
        yield {
            "source": "montreal_ai_ethics",
            "source_id": identifiant,
            "title": titre,
            "url": url,
            "summary": corps,
            "published": _date_rss(item.findtext("pubDate") or ""),
            "tags": ["éthique", "société", "gouvernance"],
            "licence": MONTREAL_LICENCE,
            "raw": {"slug": identifiant, "title": titre, "url": url},
        }


NIST_FLUX = "https://www.nist.gov/news-events/news/rss.xml"
NIST_LICENCE = "domaine public (œuvre du gouvernement fédéral américain)"
NIST_SELECTEURS = ("div.text-with-summary", "article", "main")

# Le flux couvre tout le NIST — métrologie, cybersécurité, feux de forêt.
# Mesuré : 10 articles sur 40 parlent d'IA. Sans ce filtre, la file de sujets
# se remplirait de normes sur les matériaux de construction.
NIST_MOTIF_IA = re.compile(
    r"\b(AI|artificial intelligence|machine learning|neural|algorithm\w*|"
    r"deep learning|LLM|chatbot)\b",
    re.IGNORECASE,
)


def scrape_nist(limit: int = 10) -> Iterator[dict]:
    """
    Actualités IA du NIST — normes, mesure, évaluation des risques.

    L'angle manque au projet : ni recherche, ni produit, mais la façon dont une
    institution publique tente d'encadrer et de mesurer ces systèmes.

    Les œuvres des agents fédéraux américains ne sont pas protégées par le droit
    d'auteur aux États-Unis (17 U.S.C. § 105) : c'est la source la plus libre du
    lot, et la seule sans obligation d'attribution — qui reste néanmoins faite.

    Yields:
        dict : source, source_id, title, url, summary, published, tags, licence
    """
    logger.info("Scraping NIST (limit=%d)...", limit)
    retenus = 0
    for item in _articles_rss(NIST_FLUX):
        if retenus >= limit:
            break
        titre = _texte_flux(item, "title")
        extrait = _texte_flux(item, "description")
        if not NIST_MOTIF_IA.search(f"{titre} {extrait}"):
            continue

        url = (item.findtext("link") or "").strip()
        if not url:
            continue
        corps = _corps_distant(url, NIST_SELECTEURS)
        if not corps:
            logger.info("Article ignoré, sans contenu : %s", titre[:60])
            continue
        time.sleep(RSS_DELAI)

        identifiant = url.rstrip("/").split("/")[-1]
        retenus += 1
        yield {
            "source": "nist",
            "source_id": f"nist_{identifiant}",
            "title": titre,
            "url": url,
            "summary": corps,
            "published": _date_rss(item.findtext("pubDate") or ""),
            "tags": ["normes", "institution", "évaluation"],
            "licence": NIST_LICENCE,
            "raw": {"slug": identifiant, "title": titre, "url": url},
        }


# ---------------------------------------------------------------------------
# Registre des sources disponibles
# ---------------------------------------------------------------------------

SOURCES: dict[str, callable] = {
    "arxiv": scrape_arxiv,
    "hf_papers": scrape_hf_papers,          # enregistre sous « arxiv »
    "huggingface_blog": scrape_huggingface,
    "huggingface_modeles": scrape_hf_modeles,
    "hacker_news": scrape_hacker_news,
    "montreal_ai_ethics": scrape_montreal_ai_ethics,
    "nist": scrape_nist,
}


@dataclass(frozen=True)
class Profil:
    """
    Comment une source se présente à l'écran.

    Vit ici et non dans le dashboard : la nature et la licence d'une source sont
    des propriétés de la source, pas de l'interface. Les dupliquer côté Streamlit
    les ferait diverger au premier ajout — c'est la leçon d'`ICONES`, dont la
    liste envoyée au LLM se régénère depuis le module qui dessine.

    Attributes:
        libelle:  Nom lisible, affiché tel quel
        icone:    Pictogramme de repérage dans une liste déroulante
        nature:   'technique', 'critique' ou 'institution' — ce que la source
                  apporte, la distinction qui a motivé l'élargissement du corpus
        licence:  Licence déclarée ; vide quand la source n'en annonce aucune,
                  auquel cas le contenu est « tous droits réservés » par défaut
    """

    libelle: str
    icone: str
    nature: str
    licence: str = ""


NATURES = ("technique", "critique", "institution")

PROFILS: dict[str, Profil] = {
    "arxiv": Profil("arXiv", "📄", "technique"),
    "huggingface_blog": Profil("HuggingFace Blog", "🤗", "technique"),
    "huggingface_modeles": Profil("HuggingFace Modèles", "🔥", "technique"),
    "hacker_news": Profil("Hacker News", "🗞️", "technique"),
    "montreal_ai_ethics": Profil("Montreal AI Ethics", "⚖️", "critique",
                                 MONTREAL_LICENCE),
    "nist": Profil("NIST", "🏛️", "institution", NIST_LICENCE),
}

# Sources composites : `veille` croise plusieurs articles, `manuel` n'en a
# aucun. Elles n'apparaissent pas dans SOURCES — rien ne les collecte — mais le
# dashboard en rencontre, puisqu'elles nomment des scripts déjà produits.
PROFIL_INCONNU = Profil("Source inconnue", "❔", "technique")


def profil(source: str) -> Profil:
    """Profil d'affichage d'une source, jamais absent."""
    return PROFILS.get(source, PROFIL_INCONNU)


# Deux familles, et c'est la distinction qui décide à l'usage : on choisit
# « aujourd'hui une vidéo technique » ou « aujourd'hui une vidéo de société »,
# presque jamais « institutionnelle plutôt que critique ». `nature` reste la
# granularité fine, affichée sur le badge ; `famille` est celle qui filtre.
FAMILLES: dict[str, str] = {
    "technique": "technique",
    "critique": "societe",
    "institution": "societe",
}

# Libellés, pour que l'interface et la ligne de commande nomment la même chose.
LIBELLES_FAMILLE: dict[str, str] = {
    "technique": "arXiv + HuggingFace + Hacker News",
    "societe": "Montreal AI Ethics + NIST",
}


def famille(source: str) -> str:
    """
    Famille d'une source : 'technique' ou 'societe'.

    Une source inconnue — `veille`, `manuel`, ou une source retirée du registre —
    est réputée technique : c'était le seul registre du corpus avant
    l'élargissement, donc c'est ce qu'un ancien script désigne.
    """
    return FAMILLES.get(profil(source).nature, "technique")


def sources_de(famille_voulue: str) -> tuple[str, ...]:
    """Noms des sources d'une famille, dans l'ordre du registre."""
    return tuple(s for s in PROFILS if famille(s) == famille_voulue)
