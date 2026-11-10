# Pipeline de données financières (CI/CD – niveau 1)

Pipeline Python : génération de CSV de transactions → chargement → traitement
(sommes, détection des gros montants) → insertion SQLite avec retry.
Un fichier déjà traité 
n'est jamais retraité.

## Structure

| Dossier | Rôle |
|---|---|
| `generation/` | génère 3 CSV déterministes dans `data/` |
| `loading/` | `load_transactions(path)` → liste de dicts |
| `processing/` | fonctions pures (sommes par IBAN/banque, `flag_large_amounts`) |
| `database/` | création SQLite, insertion, suivi `fichiers_traites` |
| `pipeline/` | orchestration + retry |
| `tests/` | tests unitaires et d'intégration |

## Lancement

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m pipeline.run_pipeline   # crée data/*.csv et pipeline.db
python -m pipeline.run_pipeline   # 2e lancement : fichiers ignorés
pytest -v                         # tests
```

Résultat : base `pipeline.db` (tables `resultats`, `transactions`, `fichiers_traites`).
Code de sortie 1 si l'insertion échoue après toutes les tentatives (3 par défaut, 1 s d'attente).

## CI

`.github/workflows/ci.yml` lance `pytest` à chaque push.

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
