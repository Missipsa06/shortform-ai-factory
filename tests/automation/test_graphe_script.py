"""
Tests du graphe de l'étape du script.

Aucun LLM, aucun navigateur, aucune base : le rédacteur, la mesure de mise en
page, le relevé des figures et la recherche dans le corpus sont remplacés par
des imitations qui jouent un scénario. Ce qu'on éprouve est l'aiguillage — qui
corrige quoi, et surtout que la boucle s'arrête.
"""

import pytest

import automation.graphe_script as graphe
import content.agent as agent
import content.llm as llm
import content.render as render
import scraper.figures as figures
from automation.graphe_script import (MAX_CORRECTIONS, faits_non_traces, figures_creuses,
                                      produire_script)
from automation.workflow import Sujet
from media import wan2gp

SOURCE = ("Diffusion models reach an FID of 3.17 on CIFAR-10, trained on 7,500 "
          "images with 10,000 steps. GANs were the baseline. Released in 2020.")

SUJET = Sujet(titre="Les modèles de diffusion", resume=SOURCE,
              source="arxiv", source_id="2006.11239")


def _script(contenu: str) -> dict:
    return {"titre_video": "Diffusion", "hook": "", "slides": [
        {"titre": "Résultat", "contenu": contenu}], "conclusion": ""}


# ── Vérification des faits ────────────────────────────────────────────────────

def test_les_ecritures_francaises_retrouvent_les_nombres_anglais():
    """3,17 ↔ 3.17, 7 500 ↔ 7,500, 10 000 ↔ 10,000 : même valeur, autre graphie."""
    s = _script("Un FID de 3,17 avec 7 500 images et 10 000 étapes, depuis 2020.")
    assert faits_non_traces(s, SOURCE) == []


def test_un_chiffre_et_un_sigle_inventes_sont_releves():
    s = _script("Meilleur que GPT-4 sur CIFAR-10.")
    s["slides"][0]["graphique"] = {"type": "comparaison",
                                   "donnees": {"items": [{"label": "Diffusion", "valeur": 92}]}}
    assert faits_non_traces(s, SOURCE) == ["92", "GPT"]


def test_petits_entiers_pluriels_et_sigles_admis_ne_sont_pas_des_faits():
    """« 3 étapes » décrit la vidéo ; « GANs » est le pluriel de GAN ; l'IA et
    TikTok n'apparaissent dans aucune source anglaise sans être inventés."""
    s = _script("Les GANs en 3 étapes : l'IA expliquée sur TikTok.")
    assert faits_non_traces(s, SOURCE) == []


# ── Figures creuses ───────────────────────────────────────────────────────────

def _avec(*graphiques: dict) -> dict:
    return {"slides": [{"titre": f"S{i}", "graphique": g} for i, g in enumerate(graphiques)]}


def test_un_stat_sans_chiffre_est_refuse():
    """Défaut du script « claude » du 26/10/2026."""
    s = _avec({"type": "stat", "donnees": {"valeur": "Contexte étendu"}})
    defauts = figures_creuses(s)
    assert len(defauts) == 1 and "S0" in defauts[0]


@pytest.mark.parametrize("valeurs", [(1, 0), (None, None), (5, 5)])
def test_une_comparaison_sans_ecart_devient_une_liste_de_ses_libelles(valeurs):
    """[1, 0] puis [None, None] : Gemma ne convergeait pas, le code répare."""
    s = _avec({"type": "comparaison", "donnees": {"items": [
        {"label": "Standard", "valeur": valeurs[0]}, {"label": "Refus", "valeur": valeurs[1]}]}})
    assert graphe.reparer_figures(s) == ["S0"]
    assert s["slides"][0]["graphique"] == {"type": "liste", "donnees": {"items": ["Standard", "Refus"]}}


def test_de_vraies_mesures_passent():
    s = _avec({"type": "stat", "donnees": {"valeur": "76,8%"}},
              {"type": "comparaison", "donnees": {"items": [
                  {"label": "A", "valeur": 76.8}, {"label": "B", "valeur": "94"}]}})
    assert graphe.reparer_figures(s) == []
    assert figures_creuses(s) == []


def test_la_scene_n_est_conseillee_que_si_wan2gp_est_la():
    s = _avec({"type": "liste", "donnees": {"items": ["a", "b"]}})
    assert graphe.conseil_scene(s, scenes_offertes=False) == []
    assert len(graphe.conseil_scene(s, scenes_offertes=True)) == 1
    s["slides"].append({"titre": "Robot", "graphique": {"type": "scene", "donnees": {}}})
    assert graphe.conseil_scene(s, scenes_offertes=True) == []


def test_une_insistance_en_capitales_n_est_pas_un_sigle():
    """« VRAIMENT » de Gemma le 28/10/2026 ; ROI, court, reste un sigle à tracer."""
    assert faits_non_traces(_script("C'est VRAIMENT rentable, un ROI record."), SOURCE) == ["ROI"]


# ── Règles de narration ───────────────────────────────────────────────────────

def _narration(hook: str = "Une IA bat Gemini en 3D.", **slide) -> dict:
    return {"hook": hook, "introduction": "Salut à tous !", "conclusion": "Abonne-toi !",
            "slides": [{"titre": "S1", "contenu": "Elle voit 53,2 % juste.",
                        "graphique": {"type": "stat", "donnees": {"valeur": "53.2%"}}, **slide}]}


def test_un_script_qui_tient_les_regles_passe():
    assert graphe.regles_non_tenues(_narration()) == []


def test_l_ouverture_garde_ses_deux_premieres_phrases():
    """Cas réel du 30/10/2026 : 75 mots, 30 s sur le même premier écran."""
    s = {"hook": "Ce modèle bat tout. Mais est-ce durable ? Reste jusqu'à la fin.",
         "introduction": "Salut à tous ! Aujourd'hui, Gemini 4 Carbon. Prépare-toi à voir 5.5 fois mieux."}
    assert graphe.reparer_ouverture(s) == ["hook", "introduction"]
    assert s == {"hook": "Ce modèle bat tout. Mais est-ce durable ?",
                 "introduction": "Salut à tous ! Aujourd'hui, Gemini 4 Carbon."}
    assert graphe.reparer_ouverture(s) == []


def test_un_premier_ecran_trop_long_est_renvoye_au_modele():
    s = _narration(" ".join(["mot"] * graphe.ECRAN_ACCUEIL_MAX_MOTS))
    assert any("premier écran" in d for d in graphe.regles_non_tenues(s))


@pytest.mark.parametrize("hook", ["Et si ton IA marchait sans internet ?",
                                  "Imaginez une IA hors ligne.", "Saviez-vous que l'IA…"])
def test_une_ouverture_bannie_est_refusee(hook):
    assert "accroche" in graphe.regles_non_tenues(_narration(hook))[0]


def test_tu_et_vous_dans_la_meme_video_sont_refuses():
    s = _narration(contenu="Vous voyez 53,2 % juste.")              # « Abonne-toi » plus loin
    assert any("tutoie" in d for d in graphe.regles_non_tenues(s))


def test_un_recap_sans_terminal_est_refuse():
    s = _narration(graphique={"type": "recap", "donnees": {"items": []}})
    assert any("recap" in d for d in graphe.regles_non_tenues(s))


def test_le_chiffre_du_schema_doit_etre_dit_et_pas_un_autre():
    """« Gemini 3.6 Flash » dans la narration laissait passer un anneau de 53,2 % muet."""
    s = _narration(contenu="Résultats bluffants, devant Gemini 3.6 Flash.")
    assert any("chiffre" in d for d in graphe.regles_non_tenues(s))


# ── Boucle de correction ──────────────────────────────────────────────────────

@pytest.fixture
def bac_a_sable(monkeypatch):
    """
    Remplace tout ce qui sort du processus. Rend le journal des rédactions :
    une entrée par appel au « LLM », avec les consignes reçues.
    """
    redactions: list[str] = []
    scenario: dict = {"scripts": [], "defauts": []}

    def rediger(**kwargs):
        redactions.append(kwargs["consignes"])
        scripts = scenario["scripts"]
        return dict(scripts[min(len(redactions), len(scripts)) - 1])

    def mesurer(_chemin):
        defauts = scenario["defauts"]
        return defauts[min(len(redactions), len(defauts)) - 1] if defauts else {}

    monkeypatch.setattr(llm, "reformulate_article", rediger)
    monkeypatch.setattr(render, "defauts_mise_en_page", mesurer)
    monkeypatch.setattr(figures, "figures_de", lambda *_: [])
    monkeypatch.setattr(wan2gp, "absence", lambda: "hors du bac à sable")
    return redactions, scenario


def test_un_fait_invente_est_corrige_au_passage_suivant(bac_a_sable):
    redactions, scenario = bac_a_sable
    scenario["scripts"] = [_script("Un FID de 2,5."), _script("Un FID de 3,17.")]

    script, _ = produire_script(SUJET)

    assert len(redactions) == 2
    assert redactions[0] == ""                       # premier passage : rien à corriger
    assert "« 2,5 »" in redactions[1]                # le fautif est nommé au modèle
    assert script["corrections"] == 1
    assert "faits_non_traces" not in script


def test_la_boucle_s_arrete_au_budget_et_garde_la_trace(bac_a_sable):
    """Un modèle qui s'obstine coûte 1 + MAX_CORRECTIONS appels, pas un de plus,
    et ce qui reste douteux part en relecture humaine."""
    redactions, scenario = bac_a_sable
    scenario["scripts"] = [_script("Un FID de 2,5.")]

    script, _ = produire_script(SUJET)

    assert len(redactions) == 1 + MAX_CORRECTIONS
    assert script["faits_non_traces"] == ["2,5"]


def test_une_slide_qui_deborde_est_renvoyee_puis_le_graphe_se_termine(bac_a_sable):
    """Budget épuisé, le graphe ne lève pas : c'est step_tts qui arrêtera la
    vidéo avant la voix, comme avant le graphe."""
    redactions, scenario = bac_a_sable
    scenario["scripts"] = [_script("Un FID de 3,17.")]
    scenario["defauts"] = [{"slide_01": ["texte descend à 1712 px (limite 1670)"]}]

    produire_script(SUJET)

    assert len(redactions) == 1 + MAX_CORRECTIONS
    assert "slide_01" in redactions[1]


def test_un_conseil_seul_ne_coute_pas_de_correction(bac_a_sable, monkeypatch):
    """La scène voyage avec une correction due, elle n'en cause pas une."""
    redactions, scenario = bac_a_sable
    monkeypatch.setattr(wan2gp, "absence", lambda: "")       # Wan2GP présent
    liste = {"type": "liste", "donnees": {"items": ["a", "b"]}}
    juste, faux = _script("Un FID de 3,17."), _script("Un FID de 2,5.")
    juste["slides"][0]["graphique"] = faux["slides"][0]["graphique"] = liste

    scenario["scripts"] = [juste]
    produire_script(SUJET)
    assert len(redactions) == 1

    redactions.clear()
    scenario["scripts"] = [faux, juste]
    produire_script(SUJET)
    assert "scene" in redactions[1]                          # joint à la correction due


def test_une_correction_qui_echoue_garde_le_premier_jet(bac_a_sable, monkeypatch):
    """Régression du 20/10/2026 : toute la chaîne de repli échoue sur l'appel de
    correction, et le premier jet, valide, partait avec l'exception."""
    appels: list[str] = []

    def rediger(**kwargs):
        appels.append(kwargs["consignes"])
        if kwargs["consignes"]:
            raise RuntimeError("Appel LLM échoué (mistral — limite de débit atteinte)")
        return _script("Un FID de 2,5.")

    monkeypatch.setattr(llm, "reformulate_article", rediger)

    script, _ = produire_script(SUJET)

    assert len(appels) == 2                          # une tentative, pas d'acharnement
    assert script["slides"][0]["contenu"] == "Un FID de 2,5."
    assert script["faits_non_traces"] == ["2,5"]     # l'écart part en relecture


def test_un_echec_de_la_premiere_redaction_remonte(bac_a_sable, monkeypatch):
    """Sans premier jet, il n'y a rien à garder : l'erreur doit se voir."""
    def rediger(**_):
        raise RuntimeError("Appel LLM échoué")

    monkeypatch.setattr(llm, "reformulate_article", rediger)

    with pytest.raises(RuntimeError, match="Appel LLM échoué"):
        produire_script(SUJET)


# ── Matière d'un sujet libre ──────────────────────────────────────────────────

LIBRE = Sujet(titre="les modèles de diffusion", source="manuel",
              resume="Explique le concept suivant de manière pédagogique : …",
              source_id="les_modèles_de_diffusion")


def test_un_sujet_libre_couvert_par_le_corpus_devient_multi_sources(bac_a_sable, monkeypatch):
    _, scenario = bac_a_sable
    scenario["scripts"] = [_script("Le principe.")]
    croise = Sujet(titre=LIBRE.titre, resume=SOURCE, source="veille",
                   source_id="les_modèles_de_diffusion", articles=("a", "b"))
    monkeypatch.setattr(graphe, "sujet_multi_sources", lambda _t: croise)

    script, sujet = produire_script(LIBRE)

    assert script["matiere_origine"] == "corpus"
    assert sujet.articles == ("a", "b")              # à marquer consommés


def test_sans_corpus_ni_agent_le_sujet_libre_garde_son_resume(bac_a_sable, monkeypatch):
    """Chaque recours échoue en repli : la vidéo se produit comme avant le graphe."""
    _, scenario = bac_a_sable
    scenario["scripts"] = [_script("Le principe.")]

    def aucun_article(_t):
        raise RuntimeError("Aucun article exploitable")

    monkeypatch.setattr(graphe, "sujet_multi_sources", aucun_article)
    monkeypatch.setattr(agent, "rassembler_matiere", lambda *_: "")

    script, sujet = produire_script(LIBRE)

    assert script["matiere_origine"] == "fabrique"
    assert sujet == LIBRE
