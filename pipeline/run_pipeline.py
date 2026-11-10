"""Pipeline : génération -> chargement -> traitement -> base -> insertion (avec retry)."""
import os
import sqlite3
import sys
import time

from database.db import create_database, hash_fichier, insert_results, is_file_processed
from generation.generate_csv import generate_csv_files
from loading.load_csv import load_transactions
from processing.processing import (
    flag_large_amounts,
    sum_by_destination_iban,
    sum_by_origin_iban,
    sum_by_source_bank,
)


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


def traiter_fichier(conn, chemin, max_tentatives=3, delai=1.0):
    """Traite un CSV ; retourne False s'il avait déjà été traité."""
    nom = os.path.basename(chemin)
    h = hash_fichier(chemin)
    if is_file_processed(conn, h):
        print(f"[IGNORÉ] {nom} : déjà traité.")
        return False
    transactions = load_transactions(chemin)
    results = {
        "sum_by_origin_iban": sum_by_origin_iban(transactions),
        "sum_by_source_bank": sum_by_source_bank(transactions),
        "sum_by_destination_iban": sum_by_destination_iban(transactions),
        "transactions": flag_large_amounts(transactions),
    }
    inserer_avec_retry(conn, results, h, nom, max_tentatives, delai)
    print(f"[OK] {nom} : {len(transactions)} transactions insérées.")
    return True


def run_pipeline(data_dir="data", db_path="pipeline.db", max_tentatives=3, delai=1.0):
    """Exécute toute la chaîne ; retourne le nombre de fichiers nouvellement traités."""
    chemins = generate_csv_files(data_dir)
    conn = create_database(db_path)
    try:
        return sum(traiter_fichier(conn, c, max_tentatives, delai) for c in chemins)
    finally:
        conn.close()


def main():
    try:
        n = run_pipeline()
    except PipelineError as e:
        print(f"[ÉCHEC] {e}", file=sys.stderr)
        return 1
    print(f"Pipeline terminé : {n} nouveau(x) fichier(s) traité(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
