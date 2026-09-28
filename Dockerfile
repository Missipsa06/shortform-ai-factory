# Image du pipeline : Airflow (Astro Runtime) + ffmpeg + Chromium + le code du projet.
#
# L'image de base déclenche automatiquement, dès le FROM, l'installation de
# packages.txt (apt) puis de requirements.txt (pip), et la copie du projet dans
# /usr/local/airflow. Tout ce qui suit s'exécute donc APRÈS ces étapes.
FROM astrocrpublic.azurecr.io/runtime:3.3-2

# Les modules du projet (scraper/, content/, media/...) sont copiés à la racine
# d'AIRFLOW_HOME, qui n'est pas sur le sys.path d'Airflow : on l'ajoute pour que
# les DAGs puissent faire `from content.llm import ...`.
ENV PYTHONPATH=/usr/local/airflow

# Chromium hors du HOME : accessible quel que soit l'utilisateur d'exécution,
# et non écrasé par un montage de volume sur le répertoire personnel.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

USER root
# Dépendances système du navigateur (nécessite root), puis répertoire cible
# accessible en écriture à l'utilisateur astro (uid 50000).
RUN playwright install-deps chromium \
    && mkdir -p ${PLAYWRIGHT_BROWSERS_PATH} \
    && chown -R 50000:0 ${PLAYWRIGHT_BROWSERS_PATH}

USER astro
# Le binaire du navigateur lui-même, installé sous l'utilisateur d'exécution.
RUN playwright install chromium
