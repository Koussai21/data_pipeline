"""Pipeline niveau 3 : S3 -> génération (CSV + images) -> envoi S3 -> lecture S3 -> OCR -> traitement -> base (retry)."""
import argparse
import io
import os
import sys
import time

from database.db import (
    ERREURS_BASE,
    ERREURS_INTEGRITE,
    create_database,
    hash_contenu,
    insert_results,
    is_file_processed,
)
from generation.generate_csv import generate_csv_files
from loading.load_csv import load_transactions
from ocr.generate_images import generate_and_upload_images
from processing.processing import (
    flag_large_amounts,
    sum_by_destination_iban,
    sum_by_origin_iban,
    sum_by_source_bank,
)
from ocr import ocr
from storage import s3_store
from storage.s3_store import S3Error
from validation.validation import ValidationError, valider_transactions


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
        except ERREURS_INTEGRITE as e:
            raise PipelineError(f"{nom_fichier} : déjà traité ou conflit de données ({e})")
        except ERREURS_BASE as e:
            print(f"[ERREUR] insertion de {nom_fichier}, tentative {tentative}/{max_tentatives} : {e}")
            if tentative == max_tentatives:
                raise PipelineError(
                    f"Abandon : insertion de {nom_fichier} impossible après {max_tentatives} tentatives."
                )
            print(f"         nouvelle tentative dans {delai} s...")
            sleep(delai)


def extraire_transactions(cle, contenu):
    """Transforme le contenu d'un objet S3 en liste de transactions VALIDÉES.

    CSV : chargement puis validation (ValidationError si une ligne est invalide).
    Image : OCR, qui applique les mêmes règles (OcrError sinon).
    """
    if cle.lower().endswith(".csv"):
        return valider_transactions(load_transactions(io.BytesIO(contenu), nom_fichier=cle), cle)
    return ocr.load_transaction_from_image(contenu)


def traiter_objet(conn, client, bucket, cle, max_tentatives=3, delai=1.0, lecteur=None):
    """Lit un objet (CSV ou image) depuis S3 avec retry, le traite et l'insère.

    Retourne False s'il avait déjà été traité (même hash). Une image illisible
    lève OcrError, et un CSV invalide ValidationError : dans les deux cas rien n'est
    inséré et le fichier n'est PAS marqué comme traité (il sera repris s'il est corrigé).
    """
    contenu = s3_store.get_with_retry(
        client, bucket, cle, max_tentatives, delai,
        lecteur=lecteur or s3_store.get_object_bytes,
    )
    h = hash_contenu(contenu)
    if is_file_processed(conn, h):
        print(f"[IGNORÉ] {cle} : déjà traité.")
        return False
    transactions = extraire_transactions(cle, contenu)
    results = {
        "sum_by_origin_iban": sum_by_origin_iban(transactions),
        "sum_by_source_bank": sum_by_source_bank(transactions),
        "sum_by_destination_iban": sum_by_destination_iban(transactions),
        "transactions": flag_large_amounts(transactions),
    }
    inserer_avec_retry(conn, results, h, cle, max_tentatives, delai)
    print(f"[OK] {cle} : {len(transactions)} transaction(s) insérée(s).")
    return True


def traiter_bucket(client, bucket, db_cible, max_tentatives=3, delai=1.0, lecteur=None, avec_images=True):
    """Traite tous les NOUVEAUX fichiers du bucket (CSV puis images) ; retourne leur nombre.

    Les fichiers déjà vus (hash dans fichiers_traites) sont ignorés : c'est ce que
    le planificateur appelle à chaque cycle.
    """
    try:
        cles = s3_store.list_csv_keys(client, bucket)
        if avec_images:
            cles += s3_store.list_image_keys(client, bucket)
    except s3_store.ERREURS_S3 as e:
        raise S3Error(f"Listage du bucket '{bucket}' impossible : {e}")
    conn = create_database(db_cible)
    nouveaux = 0
    try:
        for cle in cles:
            try:
                nouveaux += traiter_objet(conn, client, bucket, cle, max_tentatives, delai, lecteur)
            except (ocr.OcrError, ValidationError) as e:  # fichier invalide : signalé, on continue
                print(f"[INVALIDE] {cle} ignoré : {e}")
        return nouveaux
    finally:
        conn.close()


def run_pipeline(data_dir="data", db_path=None, bucket=s3_store.BUCKET_PAR_DEFAUT,
                 max_tentatives=3, delai=1.0, lecteur=None, client=None, avec_images=None):
    """Exécute toute la chaîne ; retourne le nombre de fichiers nouvellement traités.

    db_path : chemin SQLite ou URL PostgreSQL ; par défaut la variable DATABASE_URL, sinon pipeline.db.
    avec_images : None = automatique (actif si Tesseract est installé).
    """
    db_path = db_path or os.environ.get("DATABASE_URL", "pipeline.db")
    if avec_images is None:
        avec_images = ocr.tesseract_disponible()
        if not avec_images:
            print("[OCR] Tesseract introuvable : traitement des images désactivé.")
    # 1. Démarrage du stockage S3 et création du bucket
    client = s3_store.start_store(client)
    s3_store.create_bucket(client, bucket)
    # 2. Génération locale puis envoi vers S3, et vérification de présence
    chemins = generate_csv_files(data_dir)
    cles = s3_store.upload_files(client, bucket, chemins)
    s3_store.verify_files_present(client, bucket, cles)
    if avec_images:
        generate_and_upload_images(client, bucket, os.path.join(data_dir, "images"))
    # 3. Le traitement lit les fichiers (CSV, images) depuis S3 et non depuis le disque
    return traiter_bucket(client, bucket, db_path, max_tentatives, delai, lecteur, avec_images)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Pipeline de transactions (niveau 2 : S3 local)")
    parser.add_argument("--simuler-echec-s3", type=int, default=0, metavar="N",
                        help="fait échouer N lectures S3 par fichier (test du retry)")
    parser.add_argument("--sans-ocr", action="store_true", help="ne pas traiter les images")
    parser.add_argument("--planifie", action="store_true",
                        help="mode production : exécution initiale puis cycles réguliers (APScheduler)")
    parser.add_argument("--max-tentatives", type=int, default=3)
    parser.add_argument("--delai", type=float, default=1.0, help="attente entre tentatives (s)")
    args = parser.parse_args(argv)
    lecteur = s3_store.lecteur_defaillant(args.simuler_echec_s3) if args.simuler_echec_s3 else None
    avec_images = False if args.sans_ocr else None
    try:
        if args.planifie:
            from pipeline.scheduler import demarrer_planificateur
            demarrer_planificateur(max_tentatives=args.max_tentatives, delai=args.delai,
                                   avec_images=avec_images)
            return 0
        n = run_pipeline(max_tentatives=args.max_tentatives, delai=args.delai, lecteur=lecteur,
                         avec_images=avec_images)
    except (PipelineError, S3Error, ocr.OcrError) as e:
        print(f"[ÉCHEC] {e}", file=sys.stderr)
        return 1
    print(f"Pipeline terminé : {n} nouveau(x) fichier(s) traité(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
