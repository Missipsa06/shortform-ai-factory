"""
Configuration et implémentation des scrapers par source.

Techniques   : Arxiv, HuggingFace Blog.
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

HF_BLOG_URL = "https://huggingface.co/blog"

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


def scrape_huggingface(limit: int = 10) -> Iterator[dict]:
    """
    Scrape les derniers articles du blog HuggingFace, contenu compris.

    Yields:
        dict avec les champs : source_id, title, url, summary, published, tags
    """
    logger.info("Scraping HuggingFace Blog (limit=%d)...", limit)
    try:
        resp = requests.get(HF_BLOG_URL, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Erreur HuggingFace Blog : %s", e)
        return

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = soup.select("article")[:limit]
    logger.info("HuggingFace Blog : %d articles trouvés", len(articles))

    for article in articles:
        link_tag = article.find("a", href=True)
        if not link_tag:
            continue

        href = link_tag["href"]
        url = f"https://huggingface.co{href}" if href.startswith("/") else href
        slug = href.strip("/").split("/")[-1]
        title_tag = article.find(["h2", "h3", "h4"])
        title = title_tag.text.strip() if title_tag else slug

        summary, publie = _hf_contenu(url)
        if not summary:
            # Sans contenu, l'article ferait inventer le LLM : mieux vaut ne pas
            # l'enregistrer que de polluer la file de sujets.
            logger.info("Article ignoré, sans contenu : %s", title[:60])
            continue
        time.sleep(HF_DELAI)

        yield {
            "source": "huggingface_blog",
            "source_id": slug,
            "title": title,
            "url": url,
            "summary": summary,
            "published": publie,
            "tags": ["huggingface", "nlp"],
            "raw": {"slug": slug, "title": title, "url": url},
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
    "huggingface_blog": scrape_huggingface,
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
    "technique": "arXiv + HuggingFace",
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
