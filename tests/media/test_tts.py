"""
Tests de media/tts.py : nettoyage de texte, segmentation et repli de moteur.

Aucun appel réseau ni ffmpeg ici — _synthetiser_gemini/_synthetiser_nvidia et
la synthèse edge-tts elle-même restent hors de cette suite, qui couvre la
logique pure autour d'elles.
"""

import sys

import pytest

from content.quota import Diagnostic
from media.tts import (
    FOURNISSEURS,
    LOTISSEMENTS,
    NVIDIA_VOIX_DEFAUT,
    VOICES,
    VOIX_REPLI,
    QuotaEpuise,
    _basculer,
    _clean_text,
    _fix_pronunciation,
    _lots,
    _moteur_repli,
    _repli_actif,
    _voix_par_defaut,
    build_script_text,
    resoudre_fournisseur,
    voix_disponibles,
)


# ── _clean_text ───────────────────────────────────────────────────────────────

def test_supprime_les_hashtags():
    assert _clean_text("Un texte #IA et #DataScience") == "Un texte et"


def test_supprime_les_urls():
    assert _clean_text("Voir https://example.com/page pour plus") == "Voir pour plus"


def test_supprime_les_emojis():
    assert _clean_text("Salut 🚀 tout le monde 🎉 !") == "Salut tout le monde !"


def test_garde_la_ponctuation_et_les_accents():
    texte = "L'algo calcule l'écart entre sa réponse et la vérité : 95%."
    assert _clean_text(texte) == texte


def test_normalise_les_espaces_multiples():
    assert _clean_text("un    texte   avec   des   espaces") == "un texte avec des espaces"


def test_texte_vide():
    assert _clean_text("") == ""


# ── build_script_text ─────────────────────────────────────────────────────────

def _script_minimal():
    return {
        "hook": "Une accroche percutante ?",
        "introduction": "Salut à tous, on parle IA aujourd'hui.",
        "slides": [
            {"numero": 1, "titre": "Titre un", "contenu": "Contenu un."},
            {"numero": 2, "titre": "Titre deux", "contenu": "Contenu deux."},
        ],
        "conclusion": "Like et abonne-toi !",
    }


def test_build_script_text_ordre_des_segments():
    """hook, intro, puis les slides dans l'ordre, conclusion en dernier — jamais
    un tri alphabétique sur les identifiants, qui désynchroniserait la vidéo."""
    segments = build_script_text(_script_minimal())
    ids = [seg_id for seg_id, _, _ in segments]
    assert ids == ["hook", "intro", "slide_01", "slide_02", "conclusion"]


def test_build_script_text_titre_puis_contenu():
    """Le point final est retiré par le .strip(" .") de build_script_text."""
    segments = build_script_text(_script_minimal())
    _, texte, _ = next(s for s in segments if s[0] == "slide_01")
    assert texte == "Titre un. Contenu un"


def test_build_script_text_type_par_segment():
    segments = build_script_text(_script_minimal())
    types = {seg_id: seg_type for seg_id, _, seg_type in segments}
    assert types["hook"] == "hook"
    assert types["intro"] == "intro"
    assert types["slide_01"] == "content"
    assert types["conclusion"] == "conclusion"


def test_build_script_text_introduction_facultative():
    """Les scripts antérieurs à l'introduction n'en ont pas."""
    script = _script_minimal()
    del script["introduction"]
    segments = build_script_text(script)
    assert "intro" not in [s[0] for s in segments]


def test_build_script_text_slide_vide_est_ignoree():
    script = _script_minimal()
    script["slides"].append({"numero": 3, "titre": "", "contenu": ""})
    segments = build_script_text(script)
    assert "slide_03" not in [s[0] for s in segments]


# ── _fix_pronunciation ────────────────────────────────────────────────────────

def test_epelle_les_acronymes_tout_majuscules():
    resultat = _fix_pronunciation("On utilise BERT pour ça", ["BERT"])
    assert resultat == "On utilise B. E. R. T. pour ça"


def test_ne_touche_pas_aux_termes_mixtes():
    """VivienneMultilingual prononce déjà bien les termes non tout-majuscules."""
    resultat = _fix_pronunciation("On utilise Transformer pour ça", ["Transformer"])
    assert resultat == "On utilise Transformer pour ça"


def test_sans_termes_anglais_texte_inchange():
    assert _fix_pronunciation("Un texte quelconque", []) == "Un texte quelconque"


# ── Fournisseur / voix ────────────────────────────────────────────────────────

def test_resoudre_fournisseur_connu():
    assert resoudre_fournisseur("edge") == "edge"


def test_resoudre_fournisseur_inconnu_leve():
    with pytest.raises(ValueError, match="inconnu"):
        resoudre_fournisseur("azure")


def test_voix_par_defaut_nvidia_lit_lenv(monkeypatch):
    monkeypatch.setenv("NVIDIA_TTS_VOICE", "Pascal")
    assert _voix_par_defaut("nvidia") == "Pascal"


def test_voix_par_defaut_nvidia_repli_sur_constante(monkeypatch):
    monkeypatch.delenv("NVIDIA_TTS_VOICE", raising=False)
    assert _voix_par_defaut("nvidia") == NVIDIA_VOIX_DEFAUT


def test_voix_disponibles_couvre_les_trois_moteurs():
    d = voix_disponibles()
    assert set(d) == set(FOURNISSEURS)
    assert set(VOICES) <= set(d["edge"])
    assert "Louise" in d["nvidia"] and "Pascal" in d["nvidia"]


# ── Lotissement des appels de synthèse ───────────────────────────────────────

def _segments(nombre: int, taille: int = 200):
    return [(f"slide_{i:02d}", "m" * taille, "content") for i in range(nombre)]


def test_lots_respectent_le_budget_de_caracteres():
    """
    NVIDIA plafonne l'entrée, et le serveur compte après sa propre normalisation
    de texte : 1 925 caractères envoyés lui en faisaient 2 040. Le budget garde
    donc 30 % de marge sur un plafond qu'on ne sait pas calculer.
    """
    lots = _lots(_segments(12, taille=300), "nvidia")
    for lot in lots:
        longueur = sum(len(t) for _, t, _ in lot) + len(lot) - 1
        assert longueur <= LOTISSEMENTS["nvidia"].max_caracteres


def test_lots_respectent_le_plafond_de_segments():
    """Gemini refusait la narration entière à cause des sauts de ligne
    multiples : trois segments, donc deux sauts au plus par appel."""
    lots = _lots(_segments(9, taille=50), "gemini")
    for lot in lots:
        assert len(lot) <= LOTISSEMENTS["gemini"].max_segments


def test_les_lots_reconstituent_exactement_les_segments():
    """L'ordre et le contenu doivent survivre au découpage : c'est sur cette
    correspondance que repose toute la redécoupe audio qui suit."""
    segments = _segments(11, taille=180)
    aplati = [s for lot in _lots(segments, "nvidia") for s in lot]
    assert aplati == segments


def test_un_segment_plus_long_que_le_budget_part_seul():
    """Le couper romprait la correspondance entre un segment de script et sa
    tranche d'audio ; mieux vaut un appel qui déborde qu'une découpe fausse."""
    segments = [("court", "a" * 50, "content"), ("enorme", "b" * 5000, "content")]
    lots = _lots(segments, "nvidia")
    assert [len(lot) for lot in lots] == [1, 1]
    assert lots[1][0][0] == "enorme"


def test_un_moteur_sans_bornes_fait_un_seul_lot():
    """edge n'a ni quota ni plafond — et passe de toute façon par l'autre chemin."""
    assert len(_lots(_segments(9), "edge")) == 1


def test_aucun_segment_donne_aucun_lot():
    assert _lots([], "nvidia") == []


def test_chaque_moteur_groupe_a_des_bornes_positives():
    for nom, bornes in LOTISSEMENTS.items():
        assert bornes.max_segments > 0, nom
        assert bornes.max_caracteres > 0, nom


# ── Repli automatique ─────────────────────────────────────────────────────────

def test_repli_inactif_par_defaut(monkeypatch):
    monkeypatch.delenv("TTS_FALLBACK", raising=False)
    assert _repli_actif() is False


def test_repli_actif_avec_flag_booleen(monkeypatch):
    monkeypatch.setenv("TTS_FALLBACK", "1")
    assert _repli_actif() is True


def test_repli_actif_avec_nom_de_moteur(monkeypatch):
    monkeypatch.setenv("TTS_FALLBACK", "nvidia")
    assert _repli_actif() is True


def test_repli_explicite_prime_sur_lenv(monkeypatch):
    monkeypatch.setenv("TTS_FALLBACK", "1")
    assert _repli_actif(explicite=False) is False


def test_moteur_repli_booleen_retombe_sur_edge(monkeypatch):
    monkeypatch.setenv("TTS_FALLBACK", "1")
    assert _moteur_repli() == "edge"


def test_moteur_repli_nomme_explicitement(monkeypatch):
    monkeypatch.setenv("TTS_FALLBACK", "nvidia")
    assert _moteur_repli() == "nvidia"


def test_moteur_repli_valeur_absente_retombe_sur_edge(monkeypatch):
    monkeypatch.delenv("TTS_FALLBACK", raising=False)
    assert _moteur_repli() == "edge"


def _cause():
    return QuotaEpuise(
        "quota journalier épuisé",
        Diagnostic(genre="jour", delai=0.0, limite=None, message="quota journalier épuisé"),
    )


def test_basculer_sur_edge_utilise_la_voix_de_repli_dediee(monkeypatch):
    monkeypatch.setenv("TTS_FALLBACK", "1")
    moteur, voix, termes = _basculer(_cause())
    assert moteur == "edge"
    assert voix == VOIX_REPLI
    assert termes == []


def test_basculer_sur_nvidia_respecte_la_voix_configuree(monkeypatch):
    """
    Régression : _basculer utilisait la constante NVIDIA_VOIX_DEFAUT en dur,
    ignorant NVIDIA_TTS_VOICE — une bascule faisait parler « Louise » à qui
    avait configuré « Pascal ».
    """
    monkeypatch.setenv("TTS_FALLBACK", "nvidia")
    monkeypatch.setenv("NVIDIA_TTS_VOICE", "Pascal")
    moteur, voix, termes = _basculer(_cause())
    assert moteur == "nvidia"
    assert voix == "Pascal"


# ── Client NVIDIA absent ────────────────────────────────────────────────────

@pytest.fixture
def sans_client_riva(monkeypatch):
    """Simule un interpréteur dépourvu de `riva`, sans toucher au venv réel."""
    import builtins

    vrai_import = builtins.__import__

    def interception(nom, *args, **kwargs):
        if nom.startswith("riva"):
            raise ModuleNotFoundError("No module named 'riva'")
        return vrai_import(nom, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", interception)


def test_client_riva_absent_nomme_la_cause_et_la_sortie(sans_client_riva, tmp_path):
    """
    Régression : `import riva.client` nu laissait fuir un ModuleNotFoundError
    qui ne nommait ni l'interpréteur fautif, ni le repli. Même leçon que le
    contrôle de `faster_whisper` : une vérification par nom de module se teste,
    elle ne se relit pas.
    """
    from media.tts import _synthetiser_nvidia

    with pytest.raises(RuntimeError) as echec:
        _synthetiser_nvidia("Bonjour.", tmp_path / "x.mp3", "Pascal")

    message = str(echec.value)
    assert "riva.client" in message
    assert "uv run" in message              # comment relancer correctement
    assert "edge" in message                # la sortie sans rien installer
    assert sys.executable in message        # quel interpréteur a échoué


def test_client_riva_absent_ne_masque_pas_la_cause(sans_client_riva, tmp_path):
    """L'ImportError d'origine reste chaînée : sans elle, un vrai défaut
    d'installation serait indiscernable d'un mauvais interpréteur."""
    from media.tts import _synthetiser_nvidia

    with pytest.raises(RuntimeError) as echec:
        _synthetiser_nvidia("Bonjour.", tmp_path / "x.mp3", "Pascal")

    assert isinstance(echec.value.__cause__, ImportError)


# ── Contrôle préalable des dépendances de moteur ────────────────────────────

def test_dependance_manquante_detecte_riva_absent(sans_client_riva):
    """
    Le contrôle doit répondre sans importer le client : c'est ce qui permet de
    l'appeler avant l'étape 1, au lieu de découvrir l'absence à l'étape 3 après
    un appel LLM facturé.
    """
    from media.tts import dependance_manquante

    assert dependance_manquante("nvidia") == "riva.client"


@pytest.mark.parametrize("moteur", ["edge", "gemini"])
def test_moteurs_sans_client_differe_nont_rien_a_verifier(moteur):
    """`edge` et `gemini` importent leurs dépendances en tête de module : leur
    absence se voit à l'import, pas à l'usage."""
    from media.tts import dependance_manquante

    assert dependance_manquante(moteur) == ""


def test_moteur_inconnu_ne_reclame_rien():
    """Un nom hors du tableau ne doit pas inventer de dépendance : c'est
    `resoudre_fournisseur` qui valide les noms, pas ce contrôle."""
    from media.tts import dependance_manquante

    assert dependance_manquante("moteur-imaginaire") == ""


def test_les_deux_chemins_disent_la_meme_chose(sans_client_riva, tmp_path):
    """
    Le contrôle préalable et le garde-fou au point d'usage partagent leur
    message : deux formulations divergentes pour une même cause enverraient
    l'utilisateur sur deux pistes selon l'endroit où il tombe.
    """
    from media.tts import _synthetiser_nvidia, message_dependance

    with pytest.raises(RuntimeError) as echec:
        _synthetiser_nvidia("Bonjour.", tmp_path / "x.mp3", "Pascal")

    assert str(echec.value) == message_dependance("nvidia", "riva.client")


# ── Lotissement Gemini et scission de secours ───────────────────────────────

def test_le_budget_gemini_compte_la_consigne_de_jeu():
    """
    Régression du 400 du 22/09/2026 : `_lots` ne comptait que le texte des
    segments, alors que `_synthetiser_gemini` préfixe ensuite une consigne de
    266 à 299 caractères. Un lot annoncé sous la limite la dépassait donc d'un
    tiers, et le serveur le refusait.
    """
    from media.tts import LOTISSEMENTS, _consigne_jeu, _lots

    bornes = LOTISSEMENTS["gemini"]
    assert bornes.surcharge >= len(_consigne_jeu("content"))

    segments = [(f"s{i}", "mot " * 45, "content") for i in range(9)]
    for lot in _lots(segments, "gemini"):
        envoye = len(_consigne_jeu("content")) + len(chr(10).join(t for _, t, _ in lot))
        assert envoye <= bornes.max_caracteres, envoye


def test_un_lot_refuse_est_rejoue_en_deux_moities(monkeypatch, tmp_path):
    """
    Le budget est mesuré contre un modèle à une date donnée, et celui de Gemini
    est une préversion : ses bornes bougeront encore. Le filet doit rattraper
    sans qu'on reparamètre quoi que ce soit.
    """
    import media.tts as tts

    vus: list[int] = []

    def serveur_capricieux(texte, sortie, voix, segment_type, termes, fournisseur):
        vus.append(len(texte))
        if len(texte) > 40:
            raise RuntimeError("400 INVALID_ARGUMENT")
        sortie.write_bytes(b"audio")

    monkeypatch.setattr(tts, "_synthetiser", serveur_capricieux)
    monkeypatch.setattr(tts, "concatenate_segments",
                        lambda parts, cible: cible.write_bytes(b"joint"))

    lot = [(f"s{i}", "x" * 30, "content") for i in range(4)]
    tts._synthetiser_ou_scinder(lot, tmp_path / "out.mp3", "Charon", [],
                                "gemini", tmp_path, 0)

    assert (tmp_path / "out.mp3").exists()
    assert max(vus) > 40 and min(vus) <= 40      # a tenté large, puis réduit


def test_un_segment_unique_ne_se_coupe_pas(monkeypatch, tmp_path):
    """
    Couper un segment romprait la correspondance entre segment de script et
    tranche d'audio, sur laquelle repose toute la découpe du karaoké. L'erreur
    doit remonter telle quelle.
    """
    import media.tts as tts

    def toujours_refuse(*_a, **_k):
        raise RuntimeError("400 INVALID_ARGUMENT")

    monkeypatch.setattr(tts, "_synthetiser", toujours_refuse)
    with pytest.raises(RuntimeError, match="INVALID_ARGUMENT"):
        tts._synthetiser_ou_scinder([("s0", "x" * 900, "content")],
                                    tmp_path / "o.mp3", "Charon", [],
                                    "gemini", tmp_path, 0)


def test_la_scission_ne_change_jamais_de_moteur(monkeypatch, tmp_path):
    """
    C'est la règle que le regroupement existait pour garantir : deux voix dans
    une même bande son ne s'entendent qu'à la lecture, trop tard.
    """
    import media.tts as tts

    moteurs: list[str] = []

    def noter(texte, sortie, voix, segment_type, termes, fournisseur):
        moteurs.append(fournisseur)
        if len(texte) > 40:
            raise RuntimeError("400")
        sortie.write_bytes(b"a")

    monkeypatch.setattr(tts, "_synthetiser", noter)
    monkeypatch.setattr(tts, "concatenate_segments",
                        lambda parts, cible: cible.write_bytes(b"j"))

    lot = [(f"s{i}", "y" * 30, "content") for i in range(4)]
    tts._synthetiser_ou_scinder(lot, tmp_path / "o.mp3", "Charon", [],
                                "gemini", tmp_path, 0)
    assert set(moteurs) == {"gemini"}


def test_un_quota_epuise_nest_pas_traite_par_une_coupe(monkeypatch, tmp_path):
    """Couper ne créerait que des appels supplémentaires sur un quota déjà
    vide. Ce cas appartient à l'appelant, qui sait basculer de moteur."""
    import media.tts as tts
    from media.tts import QuotaEpuise
    from content.quota import Diagnostic

    def epuise(*_a, **_k):
        raise QuotaEpuise("quota du jour", Diagnostic(
            genre="jour", delai=0.0, limite=None, message="quota journalier épuisé"))

    monkeypatch.setattr(tts, "_synthetiser", epuise)
    with pytest.raises(QuotaEpuise):
        tts._synthetiser_ou_scinder([("a", "x" * 50, "content"), ("b", "y" * 50, "content")],
                                    tmp_path / "o.mp3", "Charon", [],
                                    "gemini", tmp_path, 0)
