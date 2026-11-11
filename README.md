# Pipeline de données financières (CI/CD – niveau 3)

Pipeline Python qui ingère des transactions financières depuis des **CSV** et des **images**
(OCR), les traite et les stocke en base. Il tourne en local (SQLite) ou en production
(Docker Compose : RustFS + PostgreSQL + application planifiée), déployé automatiquement par GitHub Actions.

## Architecture

```
 CSV générés ──┐                                   ┌──> sommes par IBAN / banque
               ├─> S3 (RustFS) ─> lecture + hash ─>├──> drapeau gros montants (> 5000)
 Images PNG ───┘   images/        (retry, anti-     └──> base de données (SQLite | PostgreSQL)
   └─ OCR (Tesseract) + validation     doublon)           tables resultats / transactions /
                                                           fichiers_traites
 Planificateur (APScheduler) : un cycle toutes les N minutes sur les NOUVEAUX fichiers du bucket
```

| Dossier / fichier | Rôle |
|---|---|
| `storage/` | S3 local : démarrage, bucket, envoi, vérification, lecture avec retry |
| `generation/` | génère 3 CSV déterministes |
| `ocr/` | `extract_text`, `parse_transaction` (+ validation IBAN/montant/date), génération d'images de test |
| `loading/` | `load_transactions` : CSV -> liste de dicts |
| `processing/` | fonctions pures (sommes, `flag_large_amounts`) |
| `database/` | SQLite **ou** PostgreSQL (selon `DATABASE_URL`), insertion atomique, `fichiers_traites` |
| `pipeline/` | `run_pipeline.py` (orchestration + retry), `scheduler.py` (cycles réguliers) |
| `Dockerfile`, `docker-compose.yml` | image de l'app ; S3 local pour le développement |
| `docker-compose.prod.yml`, `deploy/` | production (3 services) et guide de préparation de la VM |
| `.github/workflows/` | `ci.yml` (tests) et `cd.yml` (build, push GHCR, déploiement SSH) |

**Anti-doublon** : chaque fichier (CSV ou image) est identifié par le SHA-256 de son contenu,
enregistré dans `fichiers_traites` (clé primaire) dans la même transaction que ses résultats.

**Retry** : lecture S3 et insertion en base réessaient (3 fois, 1 s d'attente par défaut), affichent l'erreur,
puis s'arrêtent proprement (code 1).

## Développement

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo apt-get install tesseract-ocr fonts-dejavu-core   # pour l'OCR (sinon les images sont ignorées)

python -m pipeline.run_pipeline                 # lance RustFS (docker compose), CSV + images -> SQLite pipeline.db
python -m pipeline.run_pipeline --sans-ocr      # sans traiter les images
python -m pipeline.run_pipeline --simuler-echec-s3 5   # démo du retry S3 : échec propre (code 1)
python -m pipeline.run_pipeline --planifie      # exécution initiale puis cycle toutes les SCHEDULE_MINUTES (5)
pytest -v                                       # tous les tests (niveaux 1, 2 et 3)
docker compose down                             # arrêt du S3 de développement
```

Tests selon l'environnement : sans Docker, les tests S3 sont ignorés ; sans Tesseract, les tests d'OCR sur
image sont ignorés ; sans `TEST_POSTGRES_URL`, le test PostgreSQL est ignoré. En CI (`CI=true`) tout est obligatoire.

```bash
# exemple : tester aussi PostgreSQL
docker run -d --name pg-test -e POSTGRES_PASSWORD=postgres -p 5433:5432 postgres:16-alpine
TEST_POSTGRES_URL=postgresql://postgres:postgres@localhost:5433/postgres pytest -v
```

Variables d'environnement : `DATABASE_URL` (URL PostgreSQL, sinon SQLite `pipeline.db`), `S3_ENDPOINT`,
`S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_AUTOSTART` (0 = ne pas lancer docker compose), `SCHEDULE_MINUTES`.

## Production (Docker Compose)

Trois services : `rustfs` (S3, healthcheck, volume), `db` (PostgreSQL 16, healthcheck, volume) et `app`
(image du `Dockerfile`, démarre quand les deux autres sont *healthy*, lance `--planifie`).

```bash
cp deploy/.env.example .env && nano .env        # remplir les secrets (jamais commités)
docker compose -f docker-compose.prod.yml up -d --build   # ou: pull puis up -d avec l'image GHCR
docker compose -f docker-compose.prod.yml logs -f app
```

L'application ajoute les données de démonstration au démarrage, puis, à chaque cycle, détecte les fichiers
déposés dans le bucket (`*.csv` ou `images/*.png`) et ne traite que les nouveaux.
Préparation de la VM : voir [deploy/README.md](deploy/README.md).

## CI/CD

- **CI** (`ci.yml`, à chaque push) : installe Tesseract, démarre RustFS et PostgreSQL, lance tous les tests,
  vérifie que l'image Docker se construit.
- **CD** (`cd.yml`, après succès de la CI sur `main`) : construit l'image, la pousse sur `ghcr.io/<depot>`
  (avec `GITHUB_TOKEN`), puis déploie en SSH (`docker compose -f docker-compose.prod.yml pull && up -d`).
  Secrets requis : `SSH_HOST`, `SSH_USER`, `SSH_KEY`. S'ils manquent, le déploiement est sauté sans erreur.

## Guide Docker (S3 local RustFS)

Le stockage S3 tourne dans un conteneur Docker décrit par `docker-compose.yml`
(image `rustfs/rustfs`, port `9000`, volume persistant `rustfs-data`).

### 1. Installer et vérifier Docker

```bash
docker --version
docker compose version
docker info          # doit afficher "Server Version"; sinon le démon n'est pas lancé
```

- **Linux** : `sudo systemctl start docker`, et ajouter votre utilisateur au groupe
  `docker` (`sudo usermod -aG docker $USER`, puis se reconnecter).
- **Docker Desktop (Mac/Windows/Linux)** : lancer l'application et attendre qu'elle soit prête.

### 2. Démarrer / arrêter le S3

```bash
docker compose up -d          # démarre RustFS en arrière-plan (le pipeline le fait aussi tout seul)
docker compose ps             # état du conteneur "pipeline-rustfs"
docker compose logs -f rustfs # logs en direct
docker compose down           # arrête le conteneur (les données sont conservées)
docker compose down -v        # arrête ET supprime les données (volume rustfs-data)
```

### 3. Vérifier que le S3 répond

```bash
curl -i http://localhost:9000/   # toute réponse HTTP (même 403) prouve que le serveur est up
```

Identifiants par défaut : `rustfsadmin` / `rustfsadmin`. Ils peuvent être changés dans
`docker-compose.yml` ; il faut alors définir les variables d'environnement
`S3_ACCESS_KEY` et `S3_SECRET_KEY` (et `S3_ENDPOINT` si le port change) avant de lancer le pipeline.

### 4. Problèmes courants

| Symptôme | Cause / solution |
|---|---|
| `Cannot connect to the Docker daemon` | Le démon n'est pas lancé (voir étape 1). Avec Docker Desktop sous Linux, essayer `docker context use default` ou `export DOCKER_HOST=unix:///var/run/docker.sock`. |
| `permission denied` sur `docker.sock` | Utilisateur absent du groupe `docker` (voir étape 1). |
| `port is already allocated` (9000) | Un autre service utilise le port 9000 : l'arrêter, ou changer le mapping `"9000:9000"` dans `docker-compose.yml` et définir `S3_ENDPOINT`. |
| `[ÉCHEC] Le serveur S3 ne répond pas après 60 s` | Consulter `docker compose logs rustfs`. |
| Tests ignorés (skipped) | Le S3 est indisponible en local : démarrer Docker puis relancer `pytest`. |
