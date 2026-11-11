"""Tests niveau 3 : PostgreSQL (si disponible) et planificateur (cycles sur nouveaux fichiers)."""
import os
import uuid

import pytest

from database.db import create_database, est_postgres, hash_fichier, insert_results, is_file_processed
from generation.generate_csv import generate_csv_files
from loading.load_csv import load_transactions
from pipeline.run_pipeline import traiter_bucket, traiter_objet
from pipeline.scheduler import executer_cycle
from processing.processing import (
    flag_large_amounts,
    sum_by_destination_iban,
    sum_by_origin_iban,
    sum_by_source_bank,
)
from storage import s3_store

URL_PG = os.environ.get("TEST_POSTGRES_URL")


def test_est_postgres():
    assert est_postgres("postgresql://u:p@h:5432/d")
    assert not est_postgres("pipeline.db")


@pytest.fixture
def pg():
    if not URL_PG:
        pytest.skip("TEST_POSTGRES_URL non défini")
    conn = create_database(URL_PG)
    for table in ("resultats", "transactions", "fichiers_traites"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
    conn.close()
    conn = create_database(URL_PG)  # recrée un schéma propre
    yield conn
    conn.close()


def test_postgres_insertion_valeurs_et_doublon(pg, tmp_path):
    chemin = generate_csv_files(str(tmp_path))[0]
    tx = load_transactions(chemin)
    results = {
        "sum_by_origin_iban": sum_by_origin_iban(tx),
        "sum_by_source_bank": sum_by_source_bank(tx),
        "sum_by_destination_iban": sum_by_destination_iban(tx),
        "transactions": flag_large_amounts(tx),
    }
    h = hash_fichier(chemin)
    insert_results(pg, results, h, "f1")
    assert is_file_processed(pg, h)
    total = pg.execute("SELECT total FROM resultats WHERE type_agregat='banque_source' AND cle='BNP'"
                       ).fetchone()[0]
    assert round(total, 2) == 17850.51
    assert pg.execute("SELECT COUNT(*) FROM transactions WHERE gros_montant=1").fetchone()[0] == 2
    # un second insert du même hash est refusé et ne laisse rien derrière lui
    from database.db import ERREURS_INTEGRITE
    with pytest.raises(ERREURS_INTEGRITE):
        insert_results(pg, results, h, "f1")
    assert pg.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 5


def test_planificateur_ne_traite_que_les_nouveaux_fichiers(tmp_path, s3_client, bucket):
    db = str(tmp_path / "p.db")
    csvs = generate_csv_files(str(tmp_path / "d"))
    s3_store.upload_files(s3_client, bucket, csvs[:2])
    assert executer_cycle(s3_client, bucket, db, delai=0, avec_images=False) == 2
    assert executer_cycle(s3_client, bucket, db, delai=0, avec_images=False) == 0  # rien de neuf
    s3_store.upload_files(s3_client, bucket, csvs[2:])  # un nouveau fichier arrive
    assert executer_cycle(s3_client, bucket, db, delai=0, avec_images=False) == 1


def test_cycle_survit_a_une_panne_s3(tmp_path):
    class ClientCasse:
        def list_objects_v2(self, **kw):
            raise ConnectionError("S3 injoignable")

    # la panne de listage devient une S3Error absorbée : le service continue au cycle suivant
    assert executer_cycle(ClientCasse(), "b", str(tmp_path / "p.db"), avec_images=False) == 0
