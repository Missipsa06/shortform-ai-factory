# Runbook — chaîne de contenu IA & Data Science

De l'installation à la publication. Toutes les commandes passent par `uv run` :
aucun environnement virtuel à activer.

---

## 1. Prérequis

À installer une fois, hors du projet.

| Outil | Vérifier | Remarque |
|---|---|---|
| Python 3.12 | `python --version` | 3.11 minimum ; l'image Docker tourne en 3.14 |
| uv | `uv --version` | gère l'environnement et les dépendances |
| Docker Desktop | `docker info` | doit être **démarré**, pas seulement installé |
| Astro CLI | `astro version` | ouvrir un **nouveau terminal** après l'installation, sinon absent du PATH |
| FFmpeg | `ffmpeg -version` | build Gyan recommandé — voir l'avertissement ci-dessous |

> **FFmpeg de conda : à éviter.** Il est dépourvu de libass et ne peut pas
> incruster de sous-titres. `media/ffmpeg_utils.py` cherche un binaire hors PATH,
> mais autant installer un build complet.

---

## 2. Installation

```bash
make install          # uv sync + playwright install chromium
make install-subs     # ajoute faster-whisper (karaoké et sous-titres)
```

> **`playwright install chromium` est à refaire dans chaque environnement**, et
> pas seulement quand aucun navigateur n'est présent : chaque version de
> `playwright` exige un numéro de build précis. Un build présent mais différent
> produit un « Executable doesn't exist » trompeur.

---

## 3. Configuration

Copier `.env.example` vers `.env` et renseigner **uniquement** la clé du
fournisseur choisi.

```bash
LLM_PROVIDER=mistral        # mistral | gemini | openrouter | nvidia | kimi
MISTRAL_API_KEY=...         # tier gratuit
GEMINI_API_KEY=...          # sert aussi à la synthèse vocale

TTS_PROVIDER=gemini         # gemini | nvidia | edge
GEMINI_TTS_VOICE=Charon
NVIDIA_API_KEY=...          # LLM et/ou voix NVIDIA (build.nvidia.com)
NVIDIA_TTS_VOICE=Louise     # Louise | Pascal

IMAGE_PROVIDER=cloudflare   # pexels (photos) | cloudflare (généré)
CLOUDFLARE_ACCOUNT_ID=...   # section Workers AI du tableau de bord
CLOUDFLARE_API_TOKEN=...    # droits « Workers AI — Read » ET « Edit »
IMAGE_SLIDES_SCHEMA=        # 0 : prive d'illustration les slides à schéma

DRIVE_EXPORT_DIR=              # dossier synchronisé, pour `make export-drive`

CONTENU_DB_URL=postgresql://postgres:postgres@127.0.0.1:55432/contenu
```

**Trois pièges de configuration :**

- **Port 55432, jamais 5432.** Astro attribue un port aléatoire à Postgres à
  chaque démarrage — observé à 18337 puis 5432 sur deux lancements consécutifs.
  `docker-compose.override.yml` le fixe à 55432 pour que `.env` reste valable.
- **`127.0.0.1`, jamais `localhost`.** Sous Windows, libpq tente une négociation
  GSSAPI sur un nom d'hôte et peut geler plusieurs minutes.
- **`LLM_PROVIDER=kimi` est facturé.** Mettre `mistral` ou `gemini` pour rester
  sur les paliers gratuits. Le repli automatique le place en dernier.

---

## 4. Démarrer la stack

Nécessaire pour la base de données, l'orchestration et le dashboard.

```bash
make up        # vérifie Docker, construit l'image, lance Astro
```

| Service | Adresse |
|---|---|
| Airflow UI | http://localhost:8081 |
| Dashboard de validation | http://localhost:8501 |
| Postgres | `127.0.0.1:55432` |

```bash
make down      # arrêter
make restart   # redémarrer
make logs      # suivre les journaux
make psql      # console SQL sur la base métier
make shell     # shell dans le conteneur scheduler
```

---

## 5. Produire une vidéo

Quatre modes, du plus recommandé au moins recommandé. Tous se lancent aussi
depuis le dashboard (`make dashboard`), qui suit l'exécution et affiche son
journal.

### Sur un thème choisi, croisant plusieurs articles — le meilleur des deux

```bash
make scrape
uv run python -m automation.workflow --topic "attention" --depuis-veille \
       --sources 4 --provider mistral
```

Tu choisis le sujet **et** le script reste sourcé : les articles du corpus qui
mentionnent le thème sont retenus, puis passés ensemble au LLM. Les trois
sources retenues pour « attention » ont produit un script citant les 768
dimensions d'un vecteur, les 8 têtes d'attention et le KV cache — trois détails
venus de trois articles différents.

Le classement croise, à parts égales, deux signaux : les **mots-clés** et la
**proximité sémantique**. Le second permet à une requête française de trouver
des articles anglais (« les réseaux de neurones » remonte des articles sur les
*neural networks*) et départage les nombreux ex æquo du premier. Les articles
retenus sont journalisés avec leur score : tu peux vérifier qu'ils sont
pertinents avant de lancer la production.

Les vecteurs sont produits à la collecte ; pour les régénérer à la main :

```bash
uv run python -m content.embeddings --indexer          # les nouveaux
uv run python -m content.embeddings --indexer --tout   # tout, après changement de modèle
uv run python -m content.embeddings --requete "les transformers"   # tester
```

> Sans vecteurs, le classement redevient purement lexical avec un avertissement —
> la production de vidéo n'est jamais bloquée. Il est alors **littéral** : un
> thème en français ne trouvera que les articles qui emploient ces mots-là. Si
> rien ne correspond, le message d'erreur liste les termes cherchés.

### Sur un article de la veille

```bash
make veille-video PROVIDER=mistral    # le plus récent
make veille-hasard PROVIDER=mistral   # tiré au sort dans la file
```

Sourcé, mais tu subis le thème. Le plus récent ramène les mêmes thèmes tant
qu'Arxiv publie sur les mêmes sujets ; le tirage au sort puise dans toute la
file en attente et diversifie la production.

### Sur un article précis

```bash
uv run python -m automation.workflow --source-id 2608.09855v1 --provider mistral
```

### Sur un sujet libre, sans source

```bash
make all TOPIC="les réseaux de neurones convolutifs" PROVIDER=mistral
```

> **Sans matière documentaire, le LLM écrit de mémoire** et comble les trous avec
> du remplissage — typiquement une slide « Le futur de X, les possibilités sont
> infinies » annonçant comme futur ce qui existe déjà. Changer de fournisseur ne
> corrige pas ce défaut : la matière le corrige.

Pour donner de la matière à un sujet libre, l'agent outillé (MCP) va chercher de
vraies sources et rend une note de synthèse. Il n'est **pas branché** dans le
pipeline — à essayer à la main, et à comparer avec le script produit sans lui :

```bash
uv run python -m content.agent --topic "les modèles de diffusion"
```

Il exige `uvx` et le réseau ; à défaut il rend une chaîne vide, sans erreur.

### Figures des articles

Sur un article arXiv (version HTML) ou HuggingFace, le LLM peut afficher une
figure publiée par les auteurs, avec son crédit, à la place d'un schéma généré.
Rien à configurer. Pour voir ce qu'un article offre :

```bash
uv run python -m scraper.figures --source arxiv --source-id 2609.11860v1
```

> Un papier arXiv sur trois n'a pas de version HTML : le 404 est normal, la
> vidéo revient aux schémas générés. Le mode multi-sources n'utilise pas encore
> les figures.

Le **stem** relie tous les artefacts d'un sujet. Il apparaît dans les journaux :

| Origine | Stem |
|---|---|
| `TOPIC="les GANs"` | `manuel_les_gans` |
| Article Arxiv | `arxiv_2608.09855v1` |
| Thème multi-sources | `veille_attention` |

> Les articles retenus passent en statut `processed` dès que le script existe :
> ils sortent de la file et ne seront pas repris. En mode multi-sources, ce sont
> donc 4 articles consommés par vidéo.

---

## 6. Étape par étape, et reprise après échec

L'ordre est imposé : **la voix précède l'image**, car seule la synthèse donne la
durée de chaque slide.

```bash
make script TOPIC="les GANs" PROVIDER=mistral   # 1. script JSON
make audio  STEM=manuel_les_gans                # 2. voix + segments
make render STEM=manuel_les_gans                # 3. slides animées + karaoké
make assemble STEM=manuel_les_gans              # 4. montage final
```

```bash
make slides STEM=manuel_les_gans    # aperçu PNG rapide, sans rendu vidéo
make images STEM=manuel_les_gans    # illustrations seules
make                                # rappel de toutes les cibles
```

> Les illustrations sont en cache sous `data/images/<stem>/`. Le modèle d'image
> ne rend jamais deux fois la même image : supprimer ce dossier donne d'autres
> visuels au prochain rendu.

**Sous-titres incrustés** — inutiles dans le cas normal :

```bash
make subtitles STEM=manuel_les_gans WHISPER=base
```

> Le karaoké affiche déjà le texte prononcé, mot à mot, directement dans le
> rendu. Y superposer un SRT incrusté afficherait deux fois le même texte au même
> moment : le pipeline saute donc cette étape quand le karaoké est actif. Cette
> cible ne sert que si le karaoké est désactivé.

> **Après un échec, ne pas relancer `make all`.** Le script est déjà sur disque :
> reprendre à l'étape suivante évite de repayer le LLM et de consommer un appel
> TTS.
>
> **Ne pas utiliser `make video` non plus** pour une reprise : la cible vaut
> `audio render assemble` et refait la synthèse vocale. Enchaîner `make render`
> puis `make assemble`.

---

## 7. Valider puis publier

Le pipeline s'arrête et attend une validation humaine. Aucune publication
automatique.

```bash
make dashboard      # ou http://localhost:8501 si la stack tourne
make publish        # vidéos approuvées uniquement, en SELF_ONLY
make export-drive   # vidéos approuvées → dossier synchronisé
```

Jeton TikTok, à générer une fois :

```bash
uv run python -m publish.tiktok_auth
```

**Publier depuis le téléphone.** `make export-drive` copie les vidéos approuvées
dans `DRIVE_EXPORT_DIR`, un dossier que le client Google Drive (ou OneDrive)
synchronise. L'application mobile les rend disponibles, on les partage vers
TikTok. Choisir le mode **« mise en miroir »** et non « streaming » : ce dernier
expose un lecteur virtuel que Docker Desktop ne monte pas de façon fiable.

```bash
uv run python -m publish.drive --lister   # voir ce qui partirait
uv run python -m publish.drive --tout     # inclure les vidéos déjà publiées
```

---

## 8. Orchestration Airflow

Deux DAGs, jamais un seul :

- **`veille_quotidienne`** — cron, remplit la file de sujets, ne produit aucune vidéo
- **`produire_video`** — déclenché avec paramètres, va jusqu'à la publication

Dans l'UI, la case **« Prendre un article de la veille »** est cochée par défaut :
c'est là que les deux DAGs se rejoignent.

```bash
make dag-test    # vérifie que les DAGs s'importent (tourne dans l'image)
```

> Deux suites distinctes : `make test` couvre `content/`, `media/` et `scraper/`
> en local et écarte `tests/dags/`, qui importe `airflow` — présent seulement
> dans l'image Docker. `make dag-test` exécute celle-ci dans le conteneur.

---

## 9. Quotas

| Service | Limite | Réinitialisation |
|---|---|---|
| Gemini TTS | 10 appels/jour | minuit Pacifique, ≈ 9 h en France |
| Gemini LLM | 5 requêtes/minute sur le palier gratuit | à la minute |
| Cloudflare Workers AI | 10 000 Neurons/jour, ≈ 150 images | quotidienne |
| NVIDIA TTS | 2 000 caractères par requête (non documenté) | — |
| Mistral, OpenRouter | paliers gratuits | — |
| Kimi | **facturé** | — |

**La voix est synthétisée par lots de segments**, pas en un seul appel : chaque
moteur a ses bornes (`LOTISSEMENTS` dans `media/tts.py`). Gemini accepte au plus
3 segments et 700 caractères par lot, soit **environ 5 appels par vidéo** —
compter **deux vidéos par jour** sur le palier gratuit. NVIDIA passe en 2 lots
environ, edge-tts fait un appel par segment et n'a pas de quota.

Un lot refusé par le serveur est rejoué seul en deux moitiés, sans changer de
moteur : rien à faire. Les bornes de Gemini TTS, modèle en préversion, peuvent
bouger d'une révision à l'autre.

> Le plafond de 10 appels n'est pas appliqué strictement : des appels repassent
> parfois quelques minutes après un 429. Réessayer avant de considérer la
> journée perdue.

Le repli automatique est actif sur le LLM (paliers gratuits d'abord, Kimi en
dernier recours) et **inactif sur la voix**, pour ne pas produire de vidéo dans
une voix non choisie. Pour l'autoriser ponctuellement — typiquement pendant le
travail sur le visuel :

```bash
# .env
TTS_FALLBACK=1     # bascule sur edge-tts si le quota Gemini est épuisé
```

La bascule n'a lieu que sur le **premier lot**, avant qu'un octet d'audio
n'existe. Un échec plus tard interrompt la vidéo plutôt que de mélanger deux
voix dans la même bande son.

---

## 10. Diagnostic

| Message | Cause | Correction |
|---|---|---|
| `open //./pipe/dockerDesktopLinuxEngine` | Docker Desktop à l'arrêt | le démarrer, attendre 10 à 60 s |
| `Postgres injoignable sur 127.0.0.1:55432` | stack éteinte | `make up` |
| `connection timeout expired` | port faux dans `CONTENU_DB_URL` | vérifier 55432, et `127.0.0.1` |
| `Aucun article en attente` | file vide | `make scrape` |
| `Aucun des N articles ne mentionne « … »` | thème absent du corpus | essayer un terme technique anglais, ou `--topic` seul |
| `aucun avec un résumé d'au moins 200 caractères` | corpus sans contenu | `make scrape` pour recollecter |
| `quota journalier épuisé (10 appels/jour)` | Gemini TTS | attendre 9 h, ou `TTS_FALLBACK=1` |
| `service surchargé (HTTP 503)` | fournisseur saturé | le repli bascule seul ; sinon `PROVIDER=mistral` |
| `Capacity temporarily exceeded` (Cloudflare) | GPU saturés, passager | réessai automatique, rien à faire |
| `Additional properties '/seed' not allowed` | modèle d'image changé | retirer `CLOUDFLARE_IMAGE_MODEL` du `.env` |
| `Executable doesn't exist` (Playwright) | build Chromium incompatible | `uv run playwright install chromium` |
| `No module named 'riva'` (ou autre dépendance déclarée) | interpréteur hors du venv (conda actif, `python` nu) | toujours passer par `uv run` ou `make` ; en urgence `--tts-provider edge` |
| `2040 > 2000` (NVIDIA TTS) | texte trop long, compté après normalisation serveur | normalement évité par les lots ; sinon `TTS_PROVIDER=edge` |
| `404 Not found for account` (NVIDIA) | modèle hors du palier du compte, ou retiré | le repli essaie le modèle de secours ; sinon `NVIDIA_MODEL` |
| Karaoké et sous-titres absents, sans erreur | `faster-whisper` non installé | `make install-subs` |
| `astro n'est pas reconnu` | PATH pas rechargé | ouvrir un nouveau terminal |
| Deux voix dans une même vidéo | segments d'anciens essais | supprimer `data/audio/<stem>/` |

---

## 11. Entretien

```bash
make clean      # supprime les caches Python
```

Régénérer `requirements.txt` après tout changement de dépendances — Astro le lit
au build de l'image :

```bash
uv add <paquet>
uv export --format requirements-txt --all-groups --no-hashes \
          --no-emit-project -o requirements.txt
```

---

## Séquence complète, machine neuve

```bash
make install
make install-subs
cp .env.example .env          # puis renseigner les clés
make up
make scrape
make veille-video PROVIDER=mistral
make dashboard
```
