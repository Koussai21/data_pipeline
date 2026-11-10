"""Pipeline niveau 2 : S3 -> génération -> envoi S3 -> lecture S3 -> traitement -> SQLite (avec retry)."""
import argparse
import io
import os
import sqlite3
import sys
import time

from database.db import create_database, hash_contenu, insert_results, is_file_processed
from generation.generate_csv import generate_csv_files
from loading.load_csv import load_transactions
from processing.processing import (
    flag_large_amounts,
    sum_by_destination_iban,
    sum_by_origin_iban,
    sum_by_source_bank,
)
from storage import s3_store
from storage.s3_store import S3Error


class PipelineError(Exception):
    """Erreur lisible levée quand le pipeline doit s'arrêter proprement."""


def inserer_avec_retry(conn, results, hash_f, nom_fichier,
                       max_tentatives=3, delai=1.0, sleep=time.sleep):
    """Tente l'insertion ; en cas d'échec SQLite, attend puis réessaie.

    Après `max_tentatives` échecs, lève PipelineError (arrêt propre).
    Une erreur d'intégrité (fichier déjà traité) n'est pas réessayée.
    """
    for tentative in range(1, max_tentatives + 1):
        try:
            insert_results(conn, results, hash_f, nom_fichier)
            return
        except sqlite3.IntegrityError as e:
            raise PipelineError(f"{nom_fichier} : déjà traité ou conflit de données ({e})")
        except sqlite3.Error as e:
            print(f"[ERREUR] insertion de {nom_fichier}, tentative {tentative}/{max_tentatives} : {e}")
            if tentative == max_tentatives:
                raise PipelineError(
                    f"Abandon : insertion de {nom_fichier} impossible après {max_tentatives} tentatives."
                )
            print(f"         nouvelle tentative dans {delai} s...")
            sleep(delai)


def traiter_objet(conn, client, bucket, cle, max_tentatives=3, delai=1.0, lecteur=None):
    """Lit un CSV depuis S3 (avec retry), le traite et l'insère ; False s'il avait déjà été traité."""
    contenu = s3_store.get_with_retry(
        client, bucket, cle, max_tentatives, delai,
        lecteur=lecteur or s3_store.get_object_bytes,
    )
    h = hash_contenu(contenu)
    if is_file_processed(conn, h):
        print(f"[IGNORÉ] {cle} : déjà traité.")
        return False
    transactions = load_transactions(io.BytesIO(contenu))
    results = {
        "sum_by_origin_iban": sum_by_origin_iban(transactions),
        "sum_by_source_bank": sum_by_source_bank(transactions),
        "sum_by_destination_iban": sum_by_destination_iban(transactions),
        "transactions": flag_large_amounts(transactions),
    }
    inserer_avec_retry(conn, results, h, cle, max_tentatives, delai)
    print(f"[OK] {cle} : {len(transactions)} transactions insérées.")
    return True


def run_pipeline(data_dir="data", db_path="pipeline.db", bucket=s3_store.BUCKET_PAR_DEFAUT,
                 max_tentatives=3, delai=1.0, lecteur=None, client=None):
    """Exécute toute la chaîne ; retourne le nombre de fichiers nouvellement traités."""
    # 1. Démarrage du stockage S3 et création du bucket
    client = s3_store.start_store(client)
    s3_store.create_bucket(client, bucket)
    # 2. Génération locale puis envoi vers S3, et vérification de présence
    chemins = generate_csv_files(data_dir)
    cles = s3_store.upload_files(client, bucket, chemins)
    s3_store.verify_files_present(client, bucket, cles)
    # 3. Le traitement lit désormais les CSV depuis S3 (et non depuis le disque)
    conn = create_database(db_path)
    try:
        return sum(
            traiter_objet(conn, client, bucket, cle, max_tentatives, delai, lecteur)
            for cle in s3_store.list_csv_keys(client, bucket)
        )
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Pipeline de transactions (niveau 2 : S3 local)")
    parser.add_argument("--simuler-echec-s3", type=int, default=0, metavar="N",
                        help="fait échouer N lectures S3 par fichier (test du retry)")
    parser.add_argument("--max-tentatives", type=int, default=3)
    parser.add_argument("--delai", type=float, default=1.0, help="attente entre tentatives (s)")
    args = parser.parse_args(argv)
    lecteur = s3_store.lecteur_defaillant(args.simuler_echec_s3) if args.simuler_echec_s3 else None
    try:
        n = run_pipeline(max_tentatives=args.max_tentatives, delai=args.delai, lecteur=lecteur)
    except (PipelineError, S3Error) as e:
        print(f"[ÉCHEC] {e}", file=sys.stderr)
        return 1
    print(f"Pipeline terminé : {n} nouveau(x) fichier(s) traité(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
