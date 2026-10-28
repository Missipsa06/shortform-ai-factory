from media.corpus_voix import MOTS_MAX, MOTS_MIN, _phrases, _regrouper


def test_les_phrases_se_coupent_sur_la_ponctuation_finale():
    assert _phrases("Un. Deux ? Trois ! Quatre… Cinq") == ["Un.", "Deux ?", "Trois !", "Quatre…", "Cinq"]


def test_les_phrases_courtes_se_regroupent_jusqu_au_minimum():
    courtes = ["Un deux trois quatre cinq."] * 6          # 5 mots chacune
    groupes = _regrouper(courtes)
    assert groupes and all(MOTS_MIN <= len(g.split()) <= MOTS_MAX for g in groupes)


def test_une_phrase_isolee_trop_courte_est_ecartee():
    assert _regrouper(["Trop court."]) == []
