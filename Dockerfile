# Image de l'application de traitement (pipeline + OCR)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Tesseract (OCR) et une police nette pour la génération des images de test
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dépendances d'abord (meilleur cache de build)
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Le conteneur lance le pipeline ; on peut ajouter des options (ex. --planifie)
ENTRYPOINT ["python", "-m", "pipeline.run_pipeline"]
