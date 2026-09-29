# Chaîne de contenu IA & Data Science

Pipeline automatisé de production de vidéos courtes (format 9:16, TikTok / Reels)
vulgarisant l'IA et la Data Science pour le grand public.

De la veille sur les sources ouvertes jusqu'à la publication, avec **arrêt
obligatoire sur validation humaine** avant toute mise en ligne.

---

## Architecture

![Pipeline : la veille collecte et indexe des articles dans une file Postgres ; le DAG de production en tire un résumé réel pour écrire le script, synthétise la voix puis rend la vidéo ; un humain la valide avant de déclencher la publication.](docs/pipeline.png)

Deux DAGs reliés par une file d'articles : `veille_quotidienne` (cron) collecte
et indexe, `produire_video` (déclenché, avec paramètres) en consomme un pour
fabriquer la vidéo. Le point focal, en cyan, est le passage du **résumé réel** au
script : c'est lui qui décide de la qualité. Illustrations, figures d'articles et
agent MCP sont omis du schéma pour le garder lisible ; ils sont décrits plus bas.
Version HTML : [pipeline.html](pipeline.html).

**Principes de conception :**

1. **La veille ne produit pas de vidéos.** Générer une vidéo par article scrapé
   coûterait des appels LLM et produirait du remplissage. La veille alimente une
   file ; le choix des sujets reste une décision.
2. **La matière prime sur le modèle.** Sans source documentaire, un LLM comble
   les trous — typiquement une slide « le futur de X, les possibilités sont
   infinies » annonçant comme futur ce qui existe déjà. Changer de fournisseur
   n'y change rien ; fournir un vrai article, si.
3. **Les DAGs n'embarquent aucune logique métier.** Ils appellent les mêmes
   fonctions que `automation/workflow.py`, qui reste le chemin d'exécution local
   sans Docker.
4. **Échouer tôt.** Le script LLM est validé avant la synthèse vocale et
   l'encodage : un JSON incomplet coûte 0,1 s, pas six minutes.
5. **Le rendu est reproductible.** Les slides sont animées mais ne jouent jamais :
   on fixe l'instant de chaque animation, on capture, on avance d'une image. À
   script et ressources identiques, deux rendus donnent le même fichier, et
   l'image reste calée sur la voix à l'image près. Cela ne dit rien de
   l'uniformité d'une vidéo à l'autre, qui est au contraire un défaut à réduire.
6. **On ne retranscrit pas ce qu'on connaît déjà.** Le texte envoyé à la synthèse
   vocale est certain ; la transcription ne sert qu'à le *dater* mot à mot. Une
   erreur de reconnaissance ne peut décaler un mot, jamais le réécrire.

---

## Démarrage rapide

**Prérequis** : [uv](https://docs.astral.sh/uv/), [Docker Desktop](https://www.docker.com/products/docker-desktop/),
[Astro CLI](https://www.astronomer.io/docs/astro/cli/install-cli), FFmpeg, GNU Make.

```bash
cp .env.example .env        # puis renseigne au moins une clé LLM
make install                # environnement local + navigateur Chromium
make install-subs           # karaoké et sous-titres (faster-whisper)
make up                     # construit l'image et démarre la stack Docker
```

| Service | Adresse |
|---|---|
| Airflow | http://localhost:8081 |
| Dashboard de validation | http://localhost:8501 |
| Postgres | `postgresql://postgres:postgres@127.0.0.1:55432/contenu` |

> Les ports sont **fixés** dans `docker-compose.override.yml`. Astro en attribue
> sinon un aléatoire à chaque démarrage, ce qui invalide `CONTENU_DB_URL` et
> l'URL de l'UI d'une session à l'autre.

Produire une première vidéo, sans passer par Airflow :

```bash
make scrape                                     # remplit la file de sujets
make veille-video PROVIDER=mistral              # vidéo fondée sur un article réel
make dashboard                                  # la valider
```

---

## Choisir un sujet

Quatre modes, du plus recommandé au moins recommandé.

```bash
# Thème choisi, sources réelles : les articles du corpus qui en parlent,
# croisés et passés ensemble au LLM.
uv run python -m automation.workflow --topic "attention" --depuis-veille --sources 4

# L'article le plus récent de la veille — sourcé, mais on subit le thème
make veille-video

# Un article de la file tiré au sort, plutôt que le plus récent
make veille-hasard

# Un article précis
uv run python -m automation.workflow --source-id 2608.09855v1

# Sujet libre, sans source : le LLM écrit de mémoire et invente plus facilement
make all TOPIC="les réseaux de neurones convolutifs"
```

Le premier mode combine deux signaux pour classer les articles : les **mots-clés**
et la **proximité sémantique**. Le second permet à une requête française de
trouver des documents anglais, ce dont la recherche littérale est incapable.

**Donner de la matière au sujet libre (expérimental).** `content/agent.py` confie
à un modèle outillé (MCP, serveur `mcp-server-fetch`) la collecte de vraies
sources et rend une note de synthèse qui remplace le résumé fabriqué. Il n'est
pas encore branché dans le pipeline et s'appelle à la main :

```bash
uv run python -m content.agent --topic "les modèles de diffusion"
```

Il échoue toujours en repli : sans `uvx`, sans réseau ou sans quota, il rend une
chaîne vide et le pipeline reprend son comportement habituel.

**Les sources se choisissent sur leur licence.** Une vidéo reformulée et
traduite est une œuvre dérivée : toute licence « ND » l'interdit, « NC » en
interdirait la monétisation. Sur vingt-trois flux non techniques sondés, seuls
deux, en CC BY ou dans le domaine public, ont été retenus : Montreal AI Ethics
Institute (CC BY 4.0) et NIST (domaine public).

---

## Le pipeline, étape par étape

| Étape | Module | Ce qui se passe |
|---|---|---|
| Veille | `scraper/` | Arxiv, HuggingFace Blog, Montreal AI Ethics Institute et NIST → Postgres, déduplication par `source_id`, puis vectorisation des nouveaux articles. |
| Sélection | `automation/workflow.py` | Score hybride lexical + sémantique, partition par statut, articles marqués consommés. |
| Figures source | `scraper/figures.py` | Relève les figures publiées dans l'article (arXiv HTML, blog HuggingFace), avec légende et crédit. Le LLM peut en choisir une par indice. |
| Script | `content/llm.py` + `providers.py` | Reformulation grand public en JSON structuré (hook, introduction, slides, conclusion, hashtags). Tout fait cité doit se retrouver dans la source. |
| Illustrations | `content/images.py` | Photos Pexels ou images générées (Cloudflare Workers AI), sur chaque slide de contenu. `IMAGE_SLIDES_SCHEMA=0` en prive celles qui portent un schéma. |
| Voix | `media/tts.py` | Gemini TTS, NVIDIA Magpie ou edge-tts. Passe **avant** l'image : elle en donne la durée. Les segments sont synthétisés **par lots** pour ménager les quotas, puis redécoupés par slide. |
| Datation | `media/align.py` | Horodate chaque mot du texte connu, pour le karaoké. |
| Schémas | `content/slides.py` | Onze figures générées, la figure d'article et 32 pictogrammes, construits au rythme de la narration (voir plus bas). |
| Rendu | `content/slides.py` + `render.py` | Slides HTML/SVG animées + surlignage mot à mot, capturées image par image via Playwright → un clip MP4 par slide. Polices embarquées pour un rendu identique en local et en conteneur. |
| Montage | `media/assembly.py` | FFmpeg concatène les clips et colle la bande son. |
| Validation | `validation/dashboard.py` + `runs.py` | Lancement et suivi des exécutions (détachées, journal sur disque), prévisualisation, approbation ou rejet. |
| Publication | `publish/tiktok.py` | Content Posting API, en `SELF_ONLY` par défaut. |
| Export mobile | `publish/drive.py` | Copie les vidéos approuvées dans un dossier synchronisé (`DRIVE_EXPORT_DIR`), pour publier depuis le téléphone. |

---

## Fournisseurs

Quatre familles, toutes sur le même patron d'adaptateur : une variable
d'environnement choisit le moteur, une option de ligne de commande le surcharge.

| Rôle | Variable | Valeurs | Défaut |
|---|---|---|---|
| Script | `LLM_PROVIDER` | `gemini`, `mistral`, `openrouter`, `nvidia`, `kimi` | `mistral` |
| Voix | `TTS_PROVIDER` | `gemini` (Charon), `nvidia` (Louise, Pascal), `edge` | `gemini` |
| Illustrations | `IMAGE_PROVIDER` | `pexels`, `cloudflare` | `pexels` |
| Vecteurs | `EMBEDDING_MODEL` | tout modèle fastembed (local) | MiniLM multilingue |

**Repli automatique.** Un quota journalier épuisé ou un service surchargé ne doit
pas coûter une exécution entière. `content/quota.py` distingue les 429 « par
minute » — on réessaie — des 429 « par jour » — on change de fournisseur. Le
repli est actif par défaut sur le LLM, **inactif sur la voix** : basculer d'office
produirait des vidéos dans une voix non retenue, défaut qui ne s'entend qu'à la
lecture. `TTS_FALLBACK=1` l'autorise ponctuellement.

**Recherche sémantique sans base vectorielle.** Les vecteurs vivent dans une
colonne de la table `articles` et la similarité se calcule en numpy. À l'échelle
d'un corpus de quelques centaines d'articles, la recherche exhaustive est exacte
et immédiate ; un index approximatif ne se justifierait qu'à plusieurs dizaines
de milliers de documents. Le modèle est local (ONNX), donc sans quota — ce qui
compte, puisque ré-indexer tout le corpus est fréquent.

---

## Figures schématiques

Le LLM choisit un type de figure par slide et en fournit les données ; le SVG est
généré, jamais dessiné à la main.

| Type | Ce qu'il montre |
|---|---|
| `stat` | Un chiffre clé. L'anneau **se remplit jusqu'à la valeur** quand celle-ci porte un dénominateur. |
| `etapes` | Une suite dans le temps, cadres reliés par des flèches. |
| `frise` | Une chronologie : chaque jalon porte une date. |
| `cycle` | Un processus qui revient à son point de départ. |
| `embranchement` | Une question fermée et ses deux ou trois issues. |
| `couches` | Une superposition, une architecture en niveaux. |
| `comparaison` | Un écart chiffré entre deux à quatre grandeurs. |
| `matrice` | Le croisement de deux axes indépendants. |
| `venn` | Deux notions qui se recouvrent, et ce qu'elles partagent. |
| `liste` | Des points clés sans ordre. |
| `neurones` | Un réseau de neurones. |
| `figure_source` | Une figure publiée par les auteurs de l'article, affichée telle quelle avec son crédit. |

`figure_source` n'est pas un schéma généré : c'est la seule figure qui ne soit pas
inventée. Elle n'est jamais recadrée, pour ne pas perdre ses axes et ses
libellés, et une figure sans attribution est refusée. Sans version HTML de
l'article (un papier arXiv sur trois), le type est fermé et on revient au schéma.

Chaque item peut porter un pictogramme, choisi dans un vocabulaire de 32 tracés
embarqués. Une icône hors vocabulaire est ignorée, jamais rendue de travers.

Trois règles tiennent la cohérence d'une figure à l'autre : **une seule épaisseur
de trait**, **des formes ouvertes** — le fond reste celui de la slide — et **un
seul accent coloré** par figure, qui désigne la conclusion et rien d'autre. La
seule entorse à la première est réservée à ce qui porte une mesure : les barres
d'une comparaison, l'anneau d'une statistique.

**Les figures se construisent au rythme de la parole.** Les horodatages produits
pour le karaoké servent une seconde fois : chaque élément paraît quand la
narration en est là, au lieu que le schéma soit posé d'un bloc. Un seul calage,
deux usages.

---

## Commandes

`make` seul affiche l'aide. Les principales :

```bash
make up / down / restart / logs   # stack Airflow
make scrape                       # collecte + vectorisation
make veille-video                 # vidéo depuis un article réel
make veille-hasard                # idem, article tiré au sort dans la file
make all TOPIC="..."              # pipeline complet sur un sujet libre
make render STEM=<stem>           # reprendre après un échec, sans repayer le TTS
make assemble STEM=<stem>
make dashboard                    # validation humaine
make publish                      # vidéos approuvées → TikTok, en SELF_ONLY
make export-drive                 # vidéos approuvées → dossier synchronisé
make psql                         # console SQL sur la base métier
make test                         # tests unitaires (content/, media/, scraper/)
make dag-test                     # intégrité des DAGs (dans l'image)
```

> `make test` et `make dag-test` sont deux suites distinctes. La seconde exige
> Airflow, absent de l'environnement local, et ne tourne que dans l'image.

> Reprendre une exécution interrompue passe par `render` puis `assemble`, jamais
> par `make video` : cette cible relance aussi la synthèse vocale et consomme un
> appel de quota pour rien.

---

## Structure

```
dags/            veille_quotidienne.py, produire_video.py
scraper/         collecte des sources, figures d'articles, couche d'accès Postgres
content/         LLM, quotas, slides, illustrations, vecteurs, rendu, agent MCP
media/           TTS, datation mot à mot, montage FFmpeg, nommage
validation/      dashboard Streamlit + suivi des exécutions lancées depuis l'UI
publish/         TikTok, export Drive (Instagram à faire)
automation/      workflow.py — exécution locale séquentielle, sans Airflow
tests/           content/, media/, scraper/ en local, dags/ dans l'image
data/            artefacts par sujet (scripts, slides, audio, vidéos)
```

La procédure pas à pas, de l'installation à la publication et au dépannage, est
dans [RUNBOOK.md](RUNBOOK.md).

Chaque sujet est identifié par un **stem** — `manuel_les_transformers`,
`arxiv_2608.09855v1`, `veille_attention` selon son origine — qui relie tous ses
artefacts. Ne jamais reconstruire ces chemins à la main : `media/naming.py` et
`content/slides.plan_slides` sont les références.

---

## Format cible

1080 × 1920, 30 fps, 4 à 8 slides, moins de 5 minutes, karaoké mot à mot
incrusté, voix française de synthèse, AAC 128 kbps.

---

## Tests

`make test` couvre la logique pure : figures schématiques et pictogrammes,
diagnostic des quotas, sélection de fournisseur et extraction JSON, boucle de
l'agent MCP, nettoyage, segmentation et lotissement du texte lu, conventions de
nommage, échappement du démuxeur concat de FFmpeg, analyse des pages sources et
extraction des figures. Aucun appel réseau, aucun binaire externe, aucune base.

Restent délibérément hors de cette suite, faute de pouvoir les exécuter sans
dépendance vivante : le scraping, les appels réels aux API de texte, de voix et
d'image, le montage FFmpeg de bout en bout, la transcription Whisper et la
publication.

---

## Reste à faire

- `publish/instagram.py` (Meta Graph API / Reels)
- Modèle de scoring des sujets, entraîné sur les validations du dashboard
- Musique de fond, transitions entre slides, variations d'habillage
- Brancher l'agent MCP dans le sujet libre, après comparaison de scripts avec et sans
- Figures d'article en mode multi-sources (pas encore d'article principal désigné)
- Éprouver les onze figures sur de vraies sorties de LLM, pas seulement des scripts écrits à la main
- Traçabilité des licences en base (seules les sources RSS en déclarent une, rien n'est stocké), attribution Pexels
