"""Planificateur (APScheduler) : traite régulièrement les nouveaux fichiers du bucket."""
import os

from apscheduler.schedulers.blocking import BlockingScheduler

from ocr import ocr
from pipeline.run_pipeline import PipelineError, run_pipeline, traiter_bucket
from storage import s3_store
from storage.s3_store import S3Error


def executer_cycle(client, bucket, db_cible, max_tentatives=3, delai=1.0, avec_images=True):
    """Un cycle : détecte et traite les nouveaux fichiers. Les erreurs sont affichées sans tuer le service."""
    try:
        n = traiter_bucket(client, bucket, db_cible, max_tentatives, delai, avec_images=avec_images)
        print(f"[CYCLE] {n} nouveau(x) fichier(s) traité(s).")
        return n
    except (PipelineError, S3Error, ocr.OcrError) as e:
        print(f"[CYCLE] échec, nouvel essai au prochain cycle : {e}")
        return 0


def demarrer_planificateur(max_tentatives=3, delai=1.0, avec_images=None):
    """Exécution complète au démarrage (bucket + données de démo), puis un cycle toutes les N minutes."""
    minutes = float(os.environ.get("SCHEDULE_MINUTES", "5"))
    bucket = s3_store.BUCKET_PAR_DEFAUT
    db_cible = os.environ.get("DATABASE_URL", "pipeline.db")
    if avec_images is None:
        avec_images = ocr.tesseract_disponible()

    run_pipeline(db_path=db_cible, bucket=bucket, max_tentatives=max_tentatives,
                 delai=delai, avec_images=avec_images)
    client = s3_store.get_client()

    planif = BlockingScheduler()
    # max_instances=1 : jamais deux cycles en parallèle ; coalesce : on rattrape sans accumuler
    planif.add_job(executer_cycle, "interval", minutes=minutes, max_instances=1, coalesce=True,
                   args=[client, bucket, db_cible, max_tentatives, delai, avec_images])
    print(f"[PLANIFICATEUR] un cycle toutes les {minutes:g} minute(s). Ctrl+C pour arrêter.")
    try:
        planif.start()
    except (KeyboardInterrupt, SystemExit):
        print("[PLANIFICATEUR] arrêté.")
