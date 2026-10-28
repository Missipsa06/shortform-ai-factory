"""
Enregistrement du corpus de voix, phrase par phrase, depuis le navigateur.

    make voix-enregistrer VOIX=moi

Une prise par phrase, rangée sous `data/voix/<voix>/enregistrements/<id>.wav`
dès qu'elle est faite : on peut s'arrêter et reprendre n'importe quand, la page
repart de la première phrase sans prise. Réenregistrer remplace la prise.

48 kHz et non les 16 kHz par défaut de `st.audio_input` : l'affinage travaille
à 24 kHz, une prise à 16 kHz y perdrait tout l'aigu de la voix.
"""

import html
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from media.corpus_voix import MOTS_PAR_SECONDE, dossier, lire_corpus  # noqa: E402

voix = sys.argv[1] if len(sys.argv) > 1 else "moi"
st.set_page_config(page_title=f"Voix — {voix}", layout="centered")

corpus = lire_corpus(voix)
prises = dossier(voix) / "enregistrements"
prises.mkdir(parents=True, exist_ok=True)
faites = {p.stem for p in prises.glob("*.wav")}

if "rang" not in st.session_state:
    st.session_state.rang = next((i for i, (id_, _) in enumerate(corpus) if id_ not in faites), 0)
rang = st.session_state.rang
id_, phrase = corpus[rang]

minutes = sum(len(p.split()) for i, p in corpus if i in faites) / MOTS_PAR_SECONDE / 60
st.progress(len(faites) / len(corpus),
            text=f"{len(faites)} / {len(corpus)} phrases · ~{minutes:.0f} min enregistrées")
st.caption(
    "Pièce calme, micro à 20 cm, même place et même ton à chaque séance. Lis naturellement, "
    "comme pour une vidéo ; une hésitation ou un mot changé → refais la prise. "
    "Les anglicismes se disent comme tu les dirais à l'oral."
)

st.markdown(f"#### {id_} {'✅' if id_ in faites else ''}")
st.markdown(f"<div style='font-size:1.6rem;line-height:1.5'>{html.escape(phrase)}</div>",
            unsafe_allow_html=True)

audio = st.audio_input("Enregistrer", sample_rate=48000, key=f"prise_{id_}")
if audio is not None:
    (prises / f"{id_}.wav").write_bytes(audio.getvalue())
    st.success("Prise enregistrée.")
elif id_ in faites:
    st.audio(str(prises / f"{id_}.wav"))

precedente, suivante = st.columns(2)
if precedente.button("← Précédente", disabled=rang == 0, use_container_width=True):
    st.session_state.rang -= 1
    st.rerun()
if suivante.button("Suivante →", disabled=rang == len(corpus) - 1, use_container_width=True,
                   type="primary"):
    st.session_state.rang += 1
    st.rerun()
