# Préparer la VM de production

Prérequis : une VM Linux (Ubuntu/Debian) accessible en SSH.

## 1. Installer Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER      # puis se reconnecter
docker compose version
```

## 2. Récupérer le projet

```bash
git clone https://github.com/<proprietaire>/<depot>.git ~/data_pipeline
cd ~/data_pipeline
```

Le chemin `~/data_pipeline` est celui que le workflow CD utilise par défaut
(modifiable via la variable de dépôt GitHub `DEPLOY_PATH`).

## 3. Créer le fichier .env

```bash
cp deploy/.env.example .env
nano .env        # remplacer TOUTES les valeurs (mots de passe, clés S3, APP_IMAGE)
chmod 600 .env
```

Aucun secret réel n'est versionné : `.env` est dans `.gitignore`.

## 4. Autoriser la VM à télécharger l'image (si le dépôt/paquet est privé)

Créer un *Personal Access Token* GitHub avec le droit `read:packages`, puis :

```bash
echo "<TOKEN>" | docker login ghcr.io -u <utilisateur_github> --password-stdin
```

## 5. Premier démarrage (manuel)

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps        # rustfs et db doivent être "healthy"
docker compose -f docker-compose.prod.yml logs -f app
```

## 6. Secrets GitHub pour le déploiement automatique

Dans *Settings → Secrets and variables → Actions* :

| Secret | Contenu |
|---|---|
| `SSH_HOST` | adresse IP ou nom de la VM |
| `SSH_USER` | utilisateur SSH (membre du groupe `docker`) |
| `SSH_KEY` | clé privée SSH (la clé publique va dans `~/.ssh/authorized_keys` de la VM) |

Si l'un de ces secrets est absent, le workflow `cd.yml` construit et publie l'image
mais **saute proprement** l'étape de déploiement.

## Vérifier / dépanner

```bash
docker compose -f docker-compose.prod.yml exec db psql -U pipeline -c "SELECT COUNT(*) FROM transactions;"
docker compose -f docker-compose.prod.yml down        # arrêt (données conservées)
```
