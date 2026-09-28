# Raccourcis du pipeline de création de contenu.
#
#   make                     liste les cibles
#   make <cible> VAR=valeur  lance une étape
#
# Toutes les commandes passent par `uv run` : aucun venv à activer.

UV       := uv run
TOPIC    ?=
STEM     ?=
PROVIDER ?=
VOICE    ?=
WHISPER  ?= base
LIMIT    ?= 20

PROC   := data/processed
AUDIO  := data/audio
VIDEOS := data/videos

.DEFAULT_GOAL := help
.PHONY: help install install-subs script slides images audio render assemble video subtitles \
        all up down restart logs shell psql dag-test docker-check dashboard scrape \
        veille-video veille-hasard export-drive publish test clean

# Un seul espace entre les mots : make peut utiliser sh ou cmd selon le terminal,
# et sh écrase les espaces multiples. Le séparateur ':' rend donc pareil partout.
help:
	@echo Pipeline shortform-ai-factory. Cibles disponibles :
	@echo make install : installe le noyau et Chromium
	@echo make install-subs : ajoute faster-whisper pour les sous-titres
	@echo make all TOPIC="les transformers" : pipeline complet, du LLM a la video sous-titree
	@echo make script TOPIC="les GANs" PROVIDER=gemini : script JSON seul, PROVIDER facultatif
	@echo make video STEM=manuel_rag : voix, rendu anime et montage depuis un JSON existant
	@echo make subtitles STEM=manuel_rag : incruste les sous-titres
	@echo make up / down / restart / logs : stack Airflow dans Docker
	@echo make shell : shell dans le conteneur scheduler
	@echo make psql : console SQL sur la base metier
	@echo make dag-test : verifie que les DAGs s importent sans erreur
	@echo make dashboard : interface de validation humaine
	@echo make scrape : collecte les sources ouvertes
	@echo make veille-video : video fondee sur l article le plus recent de la veille
	@echo make veille-hasard : idem, mais sujet tire au sort dans la file
	@echo make export-drive : copie les videos approuvees vers Google Drive
	@echo make publish : publie les videos approuvees, en prive
	@echo make test : lance pytest
	@echo make clean : supprime les caches Python
	@echo Variables : TOPIC, STEM, PROVIDER, VOICE, WHISPER, LIMIT

# ── Installation ──────────────────────────────────────────────────────────────

install:
	uv sync
	$(UV) playwright install chromium

install-subs:
	uv sync --group subtitles

# ── Étapes unitaires ──────────────────────────────────────────────────────────

script:
	$(UV) python -m content.llm --topic "$(TOPIC)" $(if $(PROVIDER),--provider $(PROVIDER),)

audio:
	$(UV) python -m media.tts --input $(PROC)/$(STEM).json $(if $(VOICE),--voice $(VOICE),)

# Le rendu a besoin des durees produites par l'etape audio : il passe apres.
render:
	$(UV) python -m content.render --script $(PROC)/$(STEM).json

assemble:
	$(UV) python -m media.assembly --clips $(PROC)/$(STEM)/clips/ --audio $(AUDIO)/$(STEM)/$(STEM).mp3

# Slides PNG statiques : apercu rapide, sans rendu video.
slides:
	$(UV) python -m content.export --slides $(PROC)/$(STEM).json

subtitles:
	$(UV) python -m media.subtitles --video $(VIDEOS)/$(STEM)_raw.mp4 --model $(WHISPER)

# ── Chaînes ───────────────────────────────────────────────────────────────────

# Une illustration distincte par slide, selon IMAGE_PROVIDER.
images:
	$(UV) python -c "import json,logging,sys;from pathlib import Path;logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s');from content.images import fetch_slide_images;p=Path('$(PROC)/$(STEM).json');fetch_slide_images(json.load(open(p,encoding='utf-8')),Path('data/images')/p.stem)"

# Reprend un script JSON déjà généré et va jusqu'au MP4, sans rappeler le LLM.
# `images` en tete : le rendu les reprend, mais encore faut-il qu'elles existent.
# Sans cette etape, `make video` livrait des degrades la ou `make all` illustrait.
video: images audio render assemble

# Pipeline complet depuis un sujet : LLM, images, slides, TTS, montage, sous-titres.
all:
	$(UV) python -m automation.workflow --topic "$(TOPIC)" $(if $(PROVIDER),--provider $(PROVIDER),) $(if $(VOICE),--voice $(VOICE),) --whisper-model $(WHISPER)

# ── Stack Airflow (Docker) ────────────────────────────────────────────────────
# `astro` doit etre dans le PATH : ouvre un nouveau terminal apres son installation.

# L'image du dashboard partage le Dockerfile du pipeline : les couches sont deja
# en cache apres le build d'Astro, ce build supplementaire est quasi instantane.
# Le daemon est verifie avant le build : sans lui, docker echoue sur une erreur
# de tube nomme Windows (« open //./pipe/dockerDesktopLinuxEngine ») qui ne dit
# pas qu'il suffit de lancer Docker Desktop.
#
# Ecrit en Python et non en shell : `make` execute ses recettes via cmd.exe sous
# Windows et via sh ailleurs. Un test shell portable n'existe pas — `{ ... }` et
# `>/dev/null` sont des erreurs de syntaxe pour cmd.exe.
docker-check:
	@$(UV) python -c "import subprocess,sys;p=subprocess.run(['docker','info'],capture_output=True);sys.exit(0 if p.returncode==0 else 'Docker ne repond pas. Demarre Docker Desktop, attends 10 a 60 s, puis relance make up.')"

up: docker-check
	docker build -t shortform-ai-factory-dashboard:latest .
	astro dev start --no-browser

down:
	astro dev stop

restart:
	astro dev restart

logs:
	astro dev logs --follow

shell:
	astro dev bash --scheduler

psql:
	docker exec -it $$(docker ps -qf name=postgres) psql -U postgres -d contenu

# Airflow n'est installe que dans l'image : les tests de DAG tournent dedans.
dag-test:
	astro dev pytest tests/dags

# ── Exploitation ──────────────────────────────────────────────────────────────

dashboard:
	$(UV) streamlit run validation/dashboard.py

scrape:
	$(UV) python -m scraper.pipeline --source all --limit $(LIMIT)

# Vidéo fondée sur un article réel de la veille, plutôt que sur un sujet inventé.
# C'est le chemin à privilégier : `all TOPIC=...` fait écrire le LLM de mémoire.
veille-video:
	$(UV) python -m automation.workflow --depuis-veille $(if $(PROVIDER),--provider $(PROVIDER),) $(if $(VOICE),--voice $(VOICE),) --whisper-model $(WHISPER)

# SELF_ONLY explicite : jamais de publication publique par simple frappe.
publish:
	$(UV) python -m publish.tiktok --approved-only --privacy SELF_ONLY


# tests/dags exige airflow, installe seulement dans l'image (voir dag-test) :
# l'ignorer ici evite un ModuleNotFoundError qui interromprait toute la suite.
test:
	$(UV) pytest --ignore=tests/dags

clean:
	$(UV) python -c "import shutil, pathlib; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"

# Sujet tire au sort dans la file, plutot que le plus recent.
veille-hasard:
	$(UV) python -m automation.workflow --aleatoire $(if $(PROVIDER),--provider $(PROVIDER),) $(if $(VOICE),--voice $(VOICE),) --whisper-model $(WHISPER)

# Copie les videos approuvees vers le dossier synchronise (Google Drive).
# Pour publier depuis le telephone sans passer par l'ordinateur.
export-drive:
	$(UV) python -m publish.drive
