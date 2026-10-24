"""
Étape du script en graphe LangGraph : matière → rédaction → contrôles → corrections.

**Pourquoi un graphe ici, et nulle part ailleurs.** Airflow orchestre le
pipeline : une suite fixe de tâches, reprises une à une. Ce module orchestre ce
qui se passe *dans* la première, et c'est une boucle — rédiger, contrôler,
renvoyer au modèle ce qui ne va pas, contrôler encore — qu'un DAG ne sait pas
exprimer. Les étapes suivantes (voix, rendu, montage) n'ont rien à décider :
le rendu doit rejouer chaque image à l'identique, un agent n'y a pas sa place.

**Les nœuds sont des fonctions du projet**, pas des modèles LangChain : la
rédaction passe toujours par `content/llm.py`, donc par la chaîne de repli des
fournisseurs et le diagnostic de quota. LangGraph ne fait que l'aiguillage.

    START → matiere → rediger → verifier_faits ─┬─(écart, budget)──► rediger
                                                └─► verifier_mise_en_page ─┬─(défaut, budget)──► rediger
                                                                           └─► END

Usage :
    python -m automation.graphe_script --mermaid   # diagramme du graphe
"""

import argparse
import json
import logging
import re
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from automation.workflow import Sujet, sujet_multi_sources

logger = logging.getLogger(__name__)

# Corrections permises au-delà de la première rédaction, tous contrôles
# confondus. Chacune coûte un appel au LLM : avec 2, une vidéo en consomme au
# plus 3, contre 1 avant le graphe. Un budget commun plutôt qu'un par contrôle,
# pour que ce plafond-là soit le seul à retenir.
MAX_CORRECTIONS = 2

# Un sujet libre ne bascule sur le corpus que si celui-ci apporte au moins deux
# sources. Un seul article proche du seuil donnerait une vidéo titrée d'après le
# thème mais bâtie sur un papier voisin.
SOURCES_MIN = 2


class EtatScript(TypedDict, total=False):
    """
    État partagé par les nœuds. Chaque nœud en rend une partie, que LangGraph
    fusionne dans l'état courant.

    Attributes:
        sujet:              Matière et identité du sujet ; le nœud `matiere`
                            peut le remplacer (corpus, agent)
        provider:           Fournisseur LLM demandé
        figures:            Figures de l'article, choisies par indice par le modèle
        origine:            D'où vient la matière : source, corpus, agent, fabrique
        script:             Dernier script rédigé
        faits_non_traces:   Nombres et sigles absents de la matière
        defauts_mise_en_page: {slide_id: [défauts]} du dernier script
        corrections:        Rédactions faites au-delà de la première
    """

    sujet: Sujet
    provider: "str | None"
    figures: list
    origine: str
    script: dict
    faits_non_traces: list[str]
    defauts_mise_en_page: dict[str, list[str]]
    corrections: int


# ── Vérification des faits ────────────────────────────────────────────────────
#
# CLAUDE.md la décrit comme « le seul contrôle qui vaille », mais elle se faisait
# à la main. Principe : tout nombre et tout sigle du script doit se retrouver
# dans la matière fournie. Ce n'est pas une preuve d'exactitude — un chiffre
# présent peut être mal attribué — mais un chiffre absent est à coup sûr venu
# d'ailleurs que de la source.

# Champs vus ou entendus par le spectateur. Les autres (illustration,
# mots_cles_image, hashtags…) décrivent l'image ou le référencement.
_CHAMPS_TEXTE = ("titre_video", "hook", "introduction", "conclusion")

# Un nombre, groupes de milliers à l'espace compris (« 10 000 ») et partie
# décimale à la virgule ou au point.
_NOMBRE = re.compile(r"\d+(?:[   ]\d{3}(?!\d))*(?:[.,]\d+)?")

# Un sigle : au moins deux majuscules dans un même mot (GPT, LLaMA, CIFAR).
_SIGLE = re.compile(r"\b(?=[A-Za-z0-9]*[A-Z][A-Za-z0-9]*[A-Z])[A-Za-z][A-Za-z0-9]*\b")

# Sigles du français courant ou du réseau social, absents des sources anglaises
# sans être des faits.
_SIGLES_ADMIS = {"IA", "OK", "TIKTOK"}


def _textes_du_script(script: dict) -> list[str]:
    """Textes lus ou affichés : champs généraux, slides, données des schémas."""
    textes = [str(script.get(champ) or "") for champ in _CHAMPS_TEXTE]
    for slide in script.get("slides") or []:
        textes += [str(slide.get("titre") or ""), str(slide.get("contenu") or "")]
        donnees = (slide.get("graphique") or {}).get("donnees")
        if donnees:
            # Un « 92 » sur une barre se voit autant qu'il s'entend : les
            # valeurs et libellés des schémas sont des faits comme le texte lu.
            textes.append(json.dumps(donnees, ensure_ascii=False))
    return textes


def _valeur(nombre: str, decimale: str) -> float:
    """Valeur d'un nombre écrit, le séparateur décimal étant donné."""
    nombre = re.sub(r"[   ]", "", nombre)
    autre = "." if decimale == "," else ","
    return float(nombre.replace(autre, "").replace(decimale, "."))


def _nombres_de_la_source(texte: str) -> set[float]:
    """
    Valeurs des nombres de la matière, sous toutes leurs lectures possibles.

    La matière est souvent anglaise : « 7,500 » y vaut sept mille cinq cents,
    là où le français lirait sept et demi. Les deux lectures sont admises — être
    large côté source ne fait que réduire les fausses alertes, jamais en créer.
    """
    valeurs: set[float] = set()
    for brut in _NOMBRE.findall(texte):
        for decimale in (",", "."):
            try:
                valeurs.add(_valeur(brut, decimale))
            except ValueError:
                pass
    return valeurs


def faits_non_traces(script: dict, reference: str) -> list[str]:
    """
    Nombres et sigles du script introuvables dans la matière.

    Args:
        script:    Script JSON produit par le LLM
        reference: Ce que le modèle a reçu : titre et matière

    Returns:
        Les éléments en cause, dans leur ordre d'apparition et sans doublon ;
        vide si tout se retrouve dans la référence.

    Les entiers inférieurs à 10 sont ignorés : « 3 étapes » ou « 2 idées »
    décrivent la vidéo, pas la source.
    ponytail: heuristique sur l'écrit, sans compréhension — un mot en
    capitales d'insistance (« VRAIMENT ») passerait pour un sigle. Le budget de
    corrections borne le coût d'une fausse alerte à un appel.
    """
    texte = "\n".join(_textes_du_script(script))
    connus = _nombres_de_la_source(reference)
    reference_min = reference.lower()

    ecarts: list[str] = []
    for brut in _NOMBRE.findall(texte):
        # Le script est en français : la virgule y est décimale.
        valeur = _valeur(brut, ",")
        if valeur < 10 and valeur.is_integer():
            continue
        if not any(abs(valeur - c) < 1e-9 for c in connus):
            ecarts.append(brut.strip())

    for sigle in _SIGLE.findall(texte):
        if sigle.upper() in _SIGLES_ADMIS:
            continue
        # « GANs », « LLMs » : le pluriel français d'un sigle anglais.
        cherche = sigle[:-1] if re.fullmatch(r"[A-Z0-9]+s", sigle) else sigle
        if cherche.lower() not in reference_min:
            ecarts.append(sigle)

    return list(dict.fromkeys(ecarts))


# ── Nœuds ─────────────────────────────────────────────────────────────────────

def _documenter(sujet: Sujet, provider: "str | None") -> tuple[Sujet, str]:
    """
    Donne une vraie matière à un sujet libre : le corpus, puis l'agent MCP.

    Chaque recours échoue en repli et jamais en erreur, comme ses deux modules :
    sans base, sans vecteurs, sans `uvx` ou sans réseau, le sujet garde son
    résumé fabriqué et la vidéo se produit comme avant le graphe.
    """
    try:
        croise = sujet_multi_sources(sujet.titre)
        if len(croise.articles) >= SOURCES_MIN:
            return croise, "corpus"
        logger.info("Corpus trop mince sur « %s » (%d source) — essai de l'agent",
                    sujet.titre, len(croise.articles))
    except Exception as exc:
        logger.info("Corpus inexploitable sur « %s » (%s) — essai de l'agent",
                    sujet.titre, exc)

    from content.agent import rassembler_matiere

    matiere = rassembler_matiere(sujet.titre, provider)
    if matiere:
        return replace(sujet, resume=matiere), "agent"
    return sujet, "fabrique"


def noeud_matiere(etat: EtatScript) -> EtatScript:
    """
    Fixe la matière du sujet et relève les figures de l'article.

    Seul un sujet libre (`source == "manuel"`) est documenté ici : un article de
    veille ou un croisement multi-sources a déjà sa matière, choisie par
    l'appelant. Les figures sont relevées *après*, la provenance pouvant changer
    quand le sujet bascule sur le corpus.
    """
    from scraper.figures import figures_de

    sujet = etat["sujet"]
    origine = "source"
    if sujet.source == "manuel":
        sujet, origine = _documenter(sujet, etat.get("provider"))
    logger.info("Matière : %s (%d caractères)", origine, len(sujet.resume))
    return {"sujet": sujet, "origine": origine,
            "figures": figures_de(*sujet.provenance_figures())}


# Champs ajoutés par `reformulate_article` après la réponse du modèle : ils ne
# lui sont pas renvoyés, le modèle n'a pas à les reproduire.
_CHAMPS_AJOUTES = {"source_id", "source", "titre_original",
                   "llm_provider", "llm_model", "llm_replis"}


def _consignes(etat: EtatScript) -> str:
    """
    Consignes de correction pour la rédaction suivante ; vide au premier passage.

    Le script précédent est renvoyé en entier : régénérer à partir de rien
    corrigerait un défaut en en créant un autre. Le modèle reprend son texte et
    ne touche qu'à ce qui est refusé.
    """
    problemes = []
    if etat.get("faits_non_traces"):
        problemes.append(
            "Ces éléments n'apparaissent pas dans le résumé : "
            + ", ".join(f"« {f} »" for f in etat["faits_non_traces"])
            + ". Retire-les, ou remplace-les par ce que dit réellement le résumé."
        )
    for slide, fautes in (etat.get("defauts_mise_en_page") or {}).items():
        problemes.append(
            f"La slide {slide} déborde de l'écran ({' ; '.join(fautes)}). "
            "Raccourcis son texte ou les libellés de son schéma."
        )
    if not problemes:
        return ""

    precedent = {k: v for k, v in etat["script"].items() if k not in _CHAMPS_AJOUTES}
    return (
        "CORRECTION — voici le script que tu as produit au passage précédent :\n"
        + json.dumps(precedent, ensure_ascii=False, indent=1)
        + "\n\nIl est refusé pour ces raisons :\n- " + "\n- ".join(problemes)
        + "\n\nRends le script complet corrigé, au même format JSON. Ne change "
          "que ce qui est refusé : le reste a été validé."
    )


def noeud_rediger(etat: EtatScript) -> EtatScript:
    """
    Rédige le script, ou le corrige s'il a été refusé.

    Remet les deux contrôles à zéro : ils repassent sur chaque nouvelle version,
    et un défaut constaté sur la précédente ne doit pas être renvoyé au modèle
    alors qu'il l'a peut-être déjà corrigé.

    **Une correction qui échoue ne coûte pas la vidéo.** Observé le 20/10/2026 :
    les neuf candidats de la chaîne de repli ont échoué d'affilée — Mistral
    limité à la minute, Gemini expiré à 180 s, les gratuits hors contrat — et
    le premier jet, valide, partait avec l'exception. Une correction appelle le
    modèle quelques secondes après la rédaction, avec un prompt alourdi du
    script précédent : c'est précisément l'appel le plus exposé aux limites par
    minute. Le script précédent est donc conservé et le budget clos ; les
    contrôles repassent dessus, et ce qui reste est consigné comme d'habitude.
    Un échec de la *première* rédaction, lui, remonte : il n'y a rien à garder.
    """
    from content.llm import reformulate_article

    sujet = etat["sujet"]
    consignes = _consignes(etat)
    corrections = etat.get("corrections", 0) + (1 if consignes else 0)
    if consignes:
        logger.info("Correction %d/%d du script", corrections, MAX_CORRECTIONS)

    try:
        script = reformulate_article(
            title=sujet.titre,
            summary=sujet.resume,
            source_id=sujet.source_id,
            source=sujet.source,
            provider=etat.get("provider"),
            figures=etat.get("figures"),
            consignes=consignes,
        )
    except (RuntimeError, ValueError) as exc:
        if not consignes:
            raise
        logger.warning(
            "Correction %d impossible — le script précédent est conservé, sans "
            "nouvelle tentative : %s", corrections, exc,
        )
        return {"corrections": MAX_CORRECTIONS}
    return {"script": script, "corrections": corrections,
            "faits_non_traces": [], "defauts_mise_en_page": {}}


def noeud_verifier_faits(etat: EtatScript) -> EtatScript:
    """Relève les nombres et sigles du script absents de la matière."""
    sujet = etat["sujet"]
    ecarts = faits_non_traces(etat["script"], f"{sujet.titre}\n{sujet.resume}")
    if ecarts:
        logger.warning("Faits absents de la matière : %s", ", ".join(ecarts))
    else:
        logger.info("Faits vérifiés : tout se retrouve dans la matière")
    return {"faits_non_traces": ecarts}


def noeud_verifier_mise_en_page(etat: EtatScript) -> EtatScript:
    """
    Mesure la mise en page du brouillon dans Chromium.

    Le contrôle lit un fichier : le brouillon est écrit dans un dossier
    temporaire, avec les HTML qu'il engendre, et rien n'atteint `data/`.
    Une figure d'article n'y est encore qu'un indice, rendue vide : le
    contrôle en tête de `step_tts` reste donc le dernier filet.
    """
    from content.render import defauts_mise_en_page

    with tempfile.TemporaryDirectory(prefix="brouillon_") as dossier:
        chemin = Path(dossier) / "brouillon.json"
        chemin.write_text(json.dumps(etat["script"], ensure_ascii=False),
                          encoding="utf-8")
        defauts = defauts_mise_en_page(chemin)
    if defauts:
        logger.warning("Mise en page : %d slide(s) hors zone utile", len(defauts))
    return {"defauts_mise_en_page": defauts}


# ── Aiguillages ───────────────────────────────────────────────────────────────

def _budget_restant(etat: EtatScript) -> bool:
    return etat.get("corrections", 0) < MAX_CORRECTIONS


def route_apres_faits(etat: EtatScript) -> str:
    """
    Corrige si un fait n'est pas tracé et qu'il reste du budget.

    Budget épuisé : le script continue, avec les écarts consignés dedans pour la
    validation humaine. Un fait douteux se juge à la relecture ; bloquer la
    vidéo pour lui coûterait plus qu'il ne protège.
    """
    if etat.get("faits_non_traces") and _budget_restant(etat):
        return "rediger"
    return "verifier_mise_en_page"


def route_apres_mise_en_page(etat: EtatScript) -> str:
    """
    Corrige si une slide déborde et qu'il reste du budget.

    Budget épuisé : le graphe se termine quand même. Le script est enregistré,
    et le contrôle en tête de `step_tts` l'arrête avant la voix, avec le
    message qui dit quoi raccourcir — comportement d'avant le graphe.
    """
    if etat.get("defauts_mise_en_page") and _budget_restant(etat):
        return "rediger"
    return END


def construire_graphe():
    """Assemble et compile le graphe de l'étape du script."""
    graphe = StateGraph(EtatScript)
    graphe.add_node("matiere", noeud_matiere)
    graphe.add_node("rediger", noeud_rediger)
    graphe.add_node("verifier_faits", noeud_verifier_faits)
    graphe.add_node("verifier_mise_en_page", noeud_verifier_mise_en_page)

    graphe.add_edge(START, "matiere")
    graphe.add_edge("matiere", "rediger")
    graphe.add_edge("rediger", "verifier_faits")
    graphe.add_conditional_edges("verifier_faits", route_apres_faits,
                                 ["rediger", "verifier_mise_en_page"])
    graphe.add_conditional_edges("verifier_mise_en_page", route_apres_mise_en_page,
                                 ["rediger", END])
    return graphe.compile()


def produire_script(sujet: Sujet, provider: "str | None" = None) -> tuple[dict, Sujet]:
    """
    Exécute le graphe et rend le script, avec le sujet tel que le graphe l'a fixé.

    Returns:
        (script, sujet) — le sujet peut différer de celui reçu : un sujet libre
        documenté par le corpus devient un sujet multi-sources, dont les
        articles sont à marquer consommés et dont le stem change.
    """
    final = construire_graphe().invoke({"sujet": sujet, "provider": provider})

    script = final["script"]
    # Consignés pour la validation humaine et pour mesurer l'apport du graphe :
    # d'où venait la matière, combien de corrections, ce qui reste douteux.
    script["matiere_origine"] = final["origine"]
    script["corrections"] = final.get("corrections", 0)
    if final.get("faits_non_traces"):
        script["faits_non_traces"] = final["faits_non_traces"]
    return script, final["sujet"]


def main() -> None:
    parseur = argparse.ArgumentParser(description="Graphe de l'étape du script")
    parseur.add_argument("--mermaid", action="store_true",
                         help="Affiche le graphe au format Mermaid")
    args = parseur.parse_args()
    if args.mermaid:
        print(construire_graphe().get_graph().draw_mermaid())


if __name__ == "__main__":
    main()
