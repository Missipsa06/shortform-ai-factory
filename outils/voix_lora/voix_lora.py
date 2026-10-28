"""
Affinage LoRA de Qwen3-TTS 1.7B Base sur une voix propre.

À lancer avec le venv de ce dossier (`make voix-env`), jamais celui du projet :
PyTorch et le GPU restent hors de pyproject.toml.

    codes      encode les prises en codes audio (tokenizer 12 Hz)
    entrainer  greffe des adaptateurs LoRA et les entraîne, un fichier par époque
    exporter   fusionne une époque dans les poids : modèle CustomVoice complet,
               la voix devient un locuteur nommé, sans extrait de référence
    ecouter    fait dire des phrases de contrôle par une époque exportée, ou par
               le clonage actuel (--clone) pour comparer à l'oreille

Adapté de `finetuning/sft_12hz.py` du dépôt QwenLM/Qwen3-TTS (Apache 2.0), dont
la boucle est reprise telle quelle. Trois écarts, tous dictés par une RTX 4060
de 8 Go : LoRA sur la partie qui génère (talker et prédicteur de codes) au lieu
de tous les poids — l'officiel, avec AdamW, demande ~20 Go pour 1.7B —,
gradient checkpointing, et l'attention `sdpa` de PyTorch, FlashAttention 2 ne
s'installant pas sous Windows.

Tout lit et écrit dans data/voix/<voix>/ (voir media/corpus_voix.py).
"""

import argparse
import json
import logging
import random
import shutil
import sys
import time
from pathlib import Path

import torch

ICI = Path(__file__).resolve().parent
RACINE = ICI.parents[1]
sys.path.insert(0, str(ICI))     # dataset.py, repris du dépôt officiel

logger = logging.getLogger("voix_lora")

BASE = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
TOKENIZER = "Qwen/Qwen3-TTS-Tokenizer-12Hz"
# Rang de `codec_embedding` où le script officiel range le nouveau locuteur.
ID_LOCUTEUR = 3000
# Couches linéaires des blocs du talker et du prédicteur de codes.
CIBLES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]

# Phrases de contrôle, absentes du corpus : anglicismes, chiffres, question.
# C'est là que le clonage actuel décroche (88 % du texte, 7 termes sur 11).
ECOUTE = [
    "Tmux garde votre session ouverte, même quand vous fermez le terminal.",
    "Le modèle passe de 18,1 % à 71 % de réussite après un post-entraînement ciblé.",
    "Pourquoi un agent de code a-t-il besoin d'un harnais, et pas seulement d'un bon prompt ?",
    "Ollama télécharge les poids, les quantifie en 4 bits, et lance un serveur local sur votre machine.",
    "Retenez une chose : un benchmark mesure une tâche, pas une intelligence.",
]


def dossier(voix: str) -> Path:
    """Dossier de travail d'une voix."""
    return RACINE / "data" / "voix" / voix


def chemin_base() -> Path:
    """Dossier local du modèle Base, téléchargé au premier appel (~4,5 Go)."""
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(BASE))


def charger(chemin: Path, **kwargs):
    """Charge un modèle Qwen3-TTS (enveloppe officielle) en bf16."""
    from qwen_tts.inference.qwen3_tts_model import Qwen3TTSModel

    return Qwen3TTSModel.from_pretrained(
        str(chemin), torch_dtype=torch.bfloat16, attn_implementation="sdpa", **kwargs
    )


def greffer(model, rang: int, alpha: int) -> None:
    """Greffe les adaptateurs LoRA sur le talker, en place, et gèle tout le reste."""
    from peft import LoraConfig, inject_adapter_in_model

    config = LoraConfig(r=rang, lora_alpha=alpha, lora_dropout=0.05, target_modules=CIBLES)
    model.talker = inject_adapter_in_model(config, model.talker)
    for nom, param in model.named_parameters():
        param.requires_grad = "lora_" in nom
        if param.requires_grad:
            # Adaptateurs en fp32 sur une base bf16 : de petites mises à jour
            # sur des poids bf16 se perdraient dans l'arrondi.
            param.data = param.data.float()


def lire_jsonl(chemin: Path) -> list[dict]:
    """Lignes d'un fichier JSONL."""
    return [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines() if l.strip()]


# ── codes ────────────────────────────────────────────────────────────────────

def codes(voix: str) -> Path:
    """Ajoute à chaque ligne de train_raw.jsonl ses codes audio (train_codes.jsonl)."""
    from qwen_tts import Qwen3TTSTokenizer

    lignes = lire_jsonl(dossier(voix) / "train_raw.jsonl")
    tokenizer = Qwen3TTSTokenizer.from_pretrained(TOKENIZER, device_map="cuda:0")
    for i in range(0, len(lignes), 8):      # 8 et non 32 : la mémoire d'une carte de 8 Go
        lot = lignes[i:i + 8]
        res = tokenizer.encode([l["audio"] for l in lot])
        for code, ligne in zip(res.audio_codes, lot):
            ligne["audio_codes"] = code.cpu().tolist()
    sortie = dossier(voix) / "train_codes.jsonl"
    sortie.write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in lignes),
                      encoding="utf-8")
    logger.info("%d prises encodées → %s", len(lignes), sortie)
    return sortie


# ── entrainer ────────────────────────────────────────────────────────────────

def entrainer(voix: str, nom: str, rang: int, alpha: int, lr: float, epoques: int,
              lot: int, accumulation: int, max_pas: int | None) -> None:
    """
    Boucle de `sft_12hz.py`, sur les seuls adaptateurs. Chaque époque écrit
    data/voix/<voix>/lora/epoque-<n>.safetensors : adaptateurs et empreinte du
    locuteur, quelques dizaines de Mo. On choisit l'époque à l'oreille — le
    surapprentissage d'une voix unique peut venir dès 1 à 3 passes.
    """
    from dataset import TTSDataset
    from safetensors.torch import save_file
    from torch.utils.data import DataLoader
    from transformers import AutoConfig

    base_dir = chemin_base()
    qwen = charger(base_dir)
    config = AutoConfig.from_pretrained(str(base_dir))
    model = qwen.model.to("cuda")
    greffer(model, rang, alpha)
    try:
        model.talker.model.gradient_checkpointing_enable(
            gradient_checkpointing_kwargs={"use_reentrant": False})
    except (AttributeError, ValueError) as exc:
        logger.warning("Gradient checkpointing indisponible (%s) : mémoire plus élevée", exc)

    jeu = TTSDataset(lire_jsonl(dossier(voix) / "train_codes.jsonl"), qwen.processor, config)
    chargeur = DataLoader(jeu, batch_size=lot, shuffle=True, collate_fn=jeu.collate_fn,
                          generator=torch.Generator().manual_seed(7))
    entraines = [p for p in model.parameters() if p.requires_grad]
    logger.info("%d prises, %.1f M de paramètres entraînés (rang %d)",
                len(jeu), sum(p.numel() for p in entraines) / 1e6, rang)
    optimiseur = torch.optim.AdamW(entraines, lr=lr, weight_decay=0.01)

    sortie = dossier(voix) / "lora"
    sortie.mkdir(parents=True, exist_ok=True)
    empreinte = None
    pas, debut = 0, time.time()
    torch.cuda.reset_peak_memory_stats()
    model.train()

    for epoque in range(epoques):
        for i, batch in enumerate(chargeur):
            batch = {k: v.to("cuda") for k, v in batch.items()}
            # ── repris de sft_12hz.py ──
            speaker_embedding = model.speaker_encoder(batch["ref_mels"].to(model.dtype)).detach()
            if empreinte is None:
                empreinte = speaker_embedding
            input_ids, codec_ids = batch["input_ids"], batch["codec_ids"]
            codec_mask = batch["codec_mask"]
            input_text_embedding = (model.talker.model.text_embedding(input_ids[:, :, 0])
                                    * batch["text_embedding_mask"])
            input_codec_embedding = (model.talker.model.codec_embedding(input_ids[:, :, 1])
                                     * batch["codec_embedding_mask"])
            input_codec_embedding[:, 6, :] = speaker_embedding
            input_embeddings = input_text_embedding + input_codec_embedding
            for k in range(1, 16):
                emb = model.talker.code_predictor.get_input_embeddings()[k - 1](codec_ids[:, :, k])
                input_embeddings = input_embeddings + emb * codec_mask.unsqueeze(-1)
            outputs = model.talker(
                inputs_embeds=input_embeddings[:, :-1, :],
                attention_mask=batch["attention_mask"][:, :-1],
                labels=batch["codec_0_labels"][:, 1:],
                output_hidden_states=True,
            )
            hidden_states = outputs.hidden_states[0][-1]
            _logits, sub_loss = model.talker.forward_sub_talker_finetune(
                codec_ids[codec_mask], hidden_states[codec_mask[:, :-1]])
            loss = outputs.loss + 0.3 * sub_loss
            # ── fin de la reprise ──
            (loss / accumulation).backward()
            if (i + 1) % accumulation == 0 or i + 1 == len(chargeur):
                torch.nn.utils.clip_grad_norm_(entraines, 1.0)
                optimiseur.step()
                optimiseur.zero_grad()
            pas += 1
            if pas % 10 == 0:
                logger.info("époque %d · pas %d · perte %.4f · %.1f s/pas · pic %.2f Go",
                            epoque, pas, loss.item(), (time.time() - debut) / pas,
                            torch.cuda.max_memory_allocated() / 1e9)
            if max_pas and pas >= max_pas:
                break

        etat = {n: p.detach().cpu().contiguous()
                for n, p in model.talker.named_parameters() if "lora_" in n}
        etat["__locuteur__"] = empreinte[0].float().cpu().contiguous()
        fichier = sortie / f"epoque-{epoque}.safetensors"
        save_file(etat, str(fichier), metadata={
            "base": BASE, "nom": nom, "rang": str(rang), "alpha": str(alpha), "pas": str(pas)})
        logger.info("Époque %d écrite → %s", epoque, fichier)
        if max_pas and pas >= max_pas:
            break

    logger.info("Terminé : %d pas en %.0f s, pic mémoire %.2f Go", pas, time.time() - debut,
                torch.cuda.max_memory_allocated() / 1e9)


# ── exporter ─────────────────────────────────────────────────────────────────

def exporter(voix: str, epoque: int) -> Path:
    """
    Fusionne une époque dans les poids et écrit un modèle CustomVoice complet,
    au format de sortie du script officiel : config `custom_voice`, locuteur
    nommé au rang `ID_LOCUTEUR`, encodeur de locuteur retiré.
    """
    from peft.tuners.tuners_utils import BaseTunerLayer
    from safetensors import safe_open
    from safetensors.torch import load_file, save_file

    fichier = dossier(voix) / "lora" / f"epoque-{epoque}.safetensors"
    with safe_open(str(fichier), "pt") as f:
        meta = f.metadata()
    etat = load_file(str(fichier))
    empreinte = etat.pop("__locuteur__")

    base_dir = chemin_base()
    model = charger(base_dir).model
    greffer(model, int(meta["rang"]), int(meta["alpha"]))
    absents = model.talker.load_state_dict(etat, strict=False)
    if absents.unexpected_keys or any("lora_" in k for k in absents.missing_keys):
        raise RuntimeError(f"Adaptateurs incompatibles avec le modèle : {absents}")

    for nom, module in list(model.talker.named_modules()):
        if isinstance(module, BaseTunerLayer):
            module.merge()
            parent, _, enfant = nom.rpartition(".")
            setattr(model.talker.get_submodule(parent), enfant, module.get_base_layer())

    sortie = dossier(voix) / "export" / f"epoque-{epoque}"
    # Les poids du modèle sont réécrits plus bas ; le reste (tokenizer, config,
    # tokenizer de la parole) est copié tel quel.
    shutil.copytree(base_dir, sortie, dirs_exist_ok=True,
                    ignore=lambda d, noms: [n for n in noms if Path(d) == base_dir
                                            and n.startswith("model") and "safetensors" in n])
    config = json.loads((base_dir / "config.json").read_text(encoding="utf-8"))
    config["tts_model_type"] = "custom_voice"
    config["talker_config"]["spk_id"] = {meta["nom"]: ID_LOCUTEUR}
    config["talker_config"]["spk_is_dialect"] = {meta["nom"]: False}
    (sortie / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False),
                                        encoding="utf-8")

    poids = {k: v.detach().cpu().contiguous() for k, v in model.state_dict().items()
             if not k.startswith("speaker_encoder")}
    table = poids["talker.model.codec_embedding.weight"]
    table[ID_LOCUTEUR] = empreinte.to(table.dtype)
    save_file(poids, str(sortie / "model.safetensors"))
    logger.info("Modèle exporté → %s (locuteur « %s »)", sortie, meta["nom"])
    return sortie


# ── ecouter ──────────────────────────────────────────────────────────────────

def ecouter(voix: str, epoque: int | None, clone: bool) -> Path:
    """
    Fait dire `ECOUTE` par une époque exportée, ou par le clonage depuis
    ref.wav (--clone) : la même mesure avant et après, à comparer à l'oreille.
    """
    import soundfile as sf

    base = dossier(voix)
    if clone:
        modele = charger(chemin_base(), device_map="cuda:0")
        ref, texte_ref = base / "jeu" / "ref.wav", (base / "jeu" / "ref.txt").read_text(encoding="utf-8")
        dire = lambda t: modele.generate_voice_clone(  # noqa: E731
            text=t, language="french", ref_audio=str(ref), ref_text=texte_ref)
        sortie = base / "ecoute" / "clone"
    else:
        export = base / "export" / f"epoque-{epoque}"
        modele = charger(export, device_map="cuda:0")
        nom = next(iter(json.loads((export / "config.json").read_text(encoding="utf-8"))
                        ["talker_config"]["spk_id"]))
        dire = lambda t: modele.generate_custom_voice(text=t, speaker=nom, language="french")  # noqa: E731
        sortie = base / "ecoute" / f"epoque-{epoque}"

    sortie.mkdir(parents=True, exist_ok=True)
    for i, phrase in enumerate(ECOUTE, 1):
        debut = time.time()
        wavs, sr = dire(phrase)
        sf.write(str(sortie / f"{i}.wav"), wavs[0], sr)
        logger.info("%d/%d en %.1f s : %s", i, len(ECOUTE), time.time() - debut, phrase[:50])
    logger.info("À écouter : %s", sortie)
    return sortie


def main() -> None:
    """Point d'entrée CLI."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    random.seed(7)
    torch.manual_seed(7)
    parser = argparse.ArgumentParser(description="Affinage LoRA de Qwen3-TTS sur une voix propre")
    parser.add_argument("etape", choices=["codes", "entrainer", "exporter", "ecouter"])
    parser.add_argument("--voix", required=True)
    parser.add_argument("--nom", help="nom du locuteur dans le modèle (défaut : --voix)")
    parser.add_argument("--rang", type=int, default=32)
    parser.add_argument("--alpha", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--epoques", type=int, default=8)
    parser.add_argument("--lot", type=int, default=2)
    parser.add_argument("--accumulation", type=int, default=4)
    parser.add_argument("--max-pas", type=int, help="arrêt anticipé, pour un essai de mesure")
    parser.add_argument("--epoque", type=int)
    parser.add_argument("--clone", action="store_true", help="écouter le clonage actuel")
    args = parser.parse_args()

    if args.etape == "codes":
        codes(args.voix)
    elif args.etape == "entrainer":
        entrainer(args.voix, args.nom or args.voix, args.rang, args.alpha, args.lr,
                  args.epoques, args.lot, args.accumulation, args.max_pas)
    elif args.etape == "exporter":
        exporter(args.voix, args.epoque)
    else:
        ecouter(args.voix, args.epoque, args.clone)


if __name__ == "__main__":
    main()
