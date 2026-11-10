"""Stockage S3 local (RustFS lancé via docker compose) : bucket, envoi, vérification, lecture."""
import os
import subprocess
import time

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

# Paramètres de connexion (surchargeables par variables d'environnement)
ENDPOINT = os.environ.get("S3_ENDPOINT", "http://localhost:9000")
ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "rustfsadmin")
SECRET_KEY = os.environ.get("S3_SECRET_KEY", "rustfsadmin")
BUCKET_PAR_DEFAUT = "transactions"

RACINE_PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Erreurs réseau/S3 considérées comme "récupérables" (donc réessayables)
ERREURS_S3 = (ClientError, BotoCoreError, OSError)


class S3Error(Exception):
    """Erreur lisible liée au stockage S3 (arrêt propre du pipeline)."""


def get_client(endpoint=None):
    """Crée un client boto3 pointant vers le S3 local (timeouts courts, pas de retry interne)."""
    return boto3.client(
        "s3",
        endpoint_url=endpoint or ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name="us-east-1",
        config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1}),
    )


def store_is_ready(client):
    """Vrai si le serveur S3 répond."""
    try:
        client.list_buckets()
        return True
    except ERREURS_S3:
        return False


def start_store(client=None, timeout=60):
    """Démarre le serveur S3 local (docker compose) s'il ne répond pas, puis attend qu'il soit prêt."""
    client = client or get_client()
    if store_is_ready(client):
        print("[S3] Serveur déjà démarré.")
        return client
    print("[S3] Démarrage du serveur RustFS (docker compose)...")
    try:
        subprocess.run(["docker", "compose", "up", "-d"], cwd=RACINE_PROJET,
                       check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as e:
        detail = getattr(e, "stderr", "") or e
        raise S3Error(f"Impossible de lancer docker compose : {detail}")
    fin = time.time() + timeout
    while time.time() < fin:
        if store_is_ready(client):
            print("[S3] Serveur prêt.")
            return client
        time.sleep(1)
    raise S3Error(f"Le serveur S3 ne répond pas après {timeout} s.")


def create_bucket(client, bucket):
    """Crée le bucket s'il n'existe pas (idempotent)."""
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        try:
            client.create_bucket(Bucket=bucket)
        except ERREURS_S3 as e:
            raise S3Error(f"Création du bucket '{bucket}' impossible : {e}")
    print(f"[S3] Bucket '{bucket}' prêt.")


def upload_files(client, bucket, chemins):
    """Envoie les fichiers locaux dans le bucket (clé = nom du fichier) ; retourne les clés."""
    cles = []
    for chemin in chemins:
        cle = os.path.basename(chemin)
        try:
            client.upload_file(chemin, bucket, cle)
        except ERREURS_S3 as e:
            raise S3Error(f"Envoi de {cle} impossible : {e}")
        cles.append(cle)
    return cles


def list_csv_keys(client, bucket):
    """Liste triée des clés .csv présentes dans le bucket."""
    cles, token = [], None
    while True:
        args = {"Bucket": bucket}
        if token:
            args["ContinuationToken"] = token
        rep = client.list_objects_v2(**args)
        cles += [o["Key"] for o in rep.get("Contents", []) if o["Key"].endswith(".csv")]
        token = rep.get("NextContinuationToken")
        if not token:
            return sorted(cles)


def verify_files_present(client, bucket, cles_attendues):
    """Vérifie que toutes les clés attendues sont dans le bucket, sinon lève S3Error."""
    presentes = set(list_csv_keys(client, bucket))
    manquantes = sorted(set(cles_attendues) - presentes)
    if manquantes:
        raise S3Error(f"Fichiers absents de S3 : {', '.join(manquantes)}")
    print(f"[S3] Vérification OK : {len(cles_attendues)} fichier(s) présent(s).")


def get_object_bytes(client, bucket, cle):
    """Lit le contenu d'un objet S3 (un seul essai)."""
    return client.get_object(Bucket=bucket, Key=cle)["Body"].read()


def get_with_retry(client, bucket, cle, max_tentatives=3, delai=1.0,
                   sleep=time.sleep, lecteur=get_object_bytes):
    """Récupère un objet S3 avec retry : affiche l'erreur, attend, réessaie.

    Après `max_tentatives` échecs, lève S3Error (arrêt propre).
    `lecteur` est injectable pour simuler une panne.
    """
    for tentative in range(1, max_tentatives + 1):
        try:
            return lecteur(client, bucket, cle)
        except ERREURS_S3 as e:
            print(f"[ERREUR] lecture S3 de {cle}, tentative {tentative}/{max_tentatives} : {e}")
            if tentative == max_tentatives:
                raise S3Error(f"Abandon : lecture de {cle} impossible après {max_tentatives} tentatives.")
            print(f"         nouvelle tentative dans {delai} s...")
            sleep(delai)


def lecteur_defaillant(nb_echecs):
    """Retourne un lecteur qui échoue `nb_echecs` fois (par fichier) avant de fonctionner : simulation de panne."""
    compteurs = {}

    def lecteur(client, bucket, cle):
        compteurs[cle] = compteurs.get(cle, 0) + 1
        if compteurs[cle] <= nb_echecs:
            raise ConnectionError("panne simulée du stockage S3")
        return get_object_bytes(client, bucket, cle)

    return lecteur
