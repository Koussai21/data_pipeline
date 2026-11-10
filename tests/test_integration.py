"""Tests d'intégration : CSV -> traitement -> SQLite."""
import os
import sqlite3
import uuid

import pytest

from database.db import create_database, hash_contenu, hash_fichier, insert_results, is_file_processed
from generation.generate_csv import generate_csv_files
from loading.load_csv import load_transactions
from pipeline.run_pipeline import PipelineError, inserer_avec_retry, run_pipeline, traiter_objet
from storage import s3_store
from storage.s3_store import S3Error
from processing.processing import (
    flag_large_amounts,
    sum_by_destination_iban,
    sum_by_origin_iban,
    sum_by_source_bank,
)


@pytest.fixture
def csv_files(tmp_path):
    return generate_csv_files(str(tmp_path / "data"))


@pytest.fixture
def conn(tmp_path):
    c = create_database(str(tmp_path / "test.db"))
    yield c
    c.close()


@pytest.fixture(scope="session")
def s3_client():
    """Client S3 vers RustFS ; démarre le serveur si besoin (skip en local s'il est indisponible)."""
    try:
        return s3_store.start_store()
    except S3Error as e:
        if os.environ.get("CI"):
            raise  # en CI, l'absence de S3 est une vraie erreur
        pytest.skip(f"S3 local indisponible : {e}")


@pytest.fixture
def bucket(s3_client):
    """Bucket unique par test, vidé et supprimé à la fin."""
    nom = f"test-{uuid.uuid4().hex[:12]}"
    s3_store.create_bucket(s3_client, nom)
    yield nom
    for cle in s3_store.list_csv_keys(s3_client, nom):
        s3_client.delete_object(Bucket=nom, Key=cle)
    s3_client.delete_bucket(Bucket=nom)


def _results(chemin):
    tx = load_transactions(chemin)
    return {
        "sum_by_origin_iban": sum_by_origin_iban(tx),
        "sum_by_source_bank": sum_by_source_bank(tx),
        "sum_by_destination_iban": sum_by_destination_iban(tx),
        "transactions": flag_large_amounts(tx),
    }


def test_generation_et_chargement(csv_files):
    assert len(csv_files) >= 3
    for c in csv_files:
        tx = load_transactions(c)
        assert 5 <= len(tx) <= 10
        assert isinstance(tx[0]["montant"], float)


def test_insertion_et_valeurs(conn, csv_files):
    chemin = csv_files[0]
    insert_results(conn, _results(chemin), hash_fichier(chemin), "f1")
    total = conn.execute(
        "SELECT total FROM resultats WHERE type_agregat='iban_origine' AND cle=?",
        ("FR7612345000010000000001",),
    ).fetchone()[0]
    assert total == 5350.51  # 100 + 250.50 + 5000.01
    banque = conn.execute(
        "SELECT total FROM resultats WHERE type_agregat='banque_source' AND cle='BNP'"
    ).fetchone()[0]
    assert banque == 17850.51
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 5


def test_flags_en_base(conn, csv_files):
    chemin = csv_files[0]
    insert_results(conn, _results(chemin), hash_fichier(chemin), "f1")
    gros = conn.execute("SELECT montant FROM transactions WHERE gros_montant=1 ORDER BY montant").fetchall()
    assert [g[0] for g in gros] == [5000.01, 7500.0]


def test_fichier_jamais_traite_deux_fois(conn, csv_files, s3_client, bucket):
    cle = s3_store.upload_files(s3_client, bucket, csv_files[:1])[0]
    assert traiter_objet(conn, s3_client, bucket, cle) is True
    assert is_file_processed(conn, hash_fichier(csv_files[0]))  # même hash que le fichier source
    assert traiter_objet(conn, s3_client, bucket, cle) is False
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 5


def test_s3_bucket_upload_et_verification(s3_client, bucket, csv_files):
    cles = s3_store.upload_files(s3_client, bucket, csv_files)
    s3_store.verify_files_present(s3_client, bucket, cles)
    assert s3_store.list_csv_keys(s3_client, bucket) == sorted(cles)
    # le contenu relu depuis S3 est identique au fichier généré
    with open(csv_files[0], "rb") as f:
        assert s3_store.get_object_bytes(s3_client, bucket, cles[0]) == f.read()


def test_s3_verification_detecte_fichier_manquant(s3_client, bucket, csv_files):
    s3_store.upload_files(s3_client, bucket, csv_files[:1])
    with pytest.raises(S3Error, match="absents"):
        s3_store.verify_files_present(s3_client, bucket, ["transactions_1.csv", "fantome.csv"])


def test_create_bucket_idempotent(s3_client, bucket):
    s3_store.create_bucket(s3_client, bucket)  # déjà existant : pas d'erreur


def test_retry_s3_reussit_apres_echecs(s3_client, bucket, csv_files, conn):
    cle = s3_store.upload_files(s3_client, bucket, csv_files[:1])[0]
    attentes = []
    contenu = s3_store.get_with_retry(s3_client, bucket, cle, 3, 0.5, attentes.append,
                                      s3_store.lecteur_defaillant(2))
    assert attentes == [0.5, 0.5]
    assert hash_contenu(contenu) == hash_fichier(csv_files[0])


def test_retry_s3_abandon_propre(s3_client, bucket, csv_files, conn):
    cle = s3_store.upload_files(s3_client, bucket, csv_files[:1])[0]
    with pytest.raises(S3Error, match="3 tentatives"):
        traiter_objet(conn, s3_client, bucket, cle, 3, 0, s3_store.lecteur_defaillant(99))
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_insertion_atomique_en_cas_d_erreur(conn):
    mauvais = {"sum_by_origin_iban": {"A": 1.0}, "transactions": [{"montant": 1}]}  # champs manquants
    with pytest.raises(KeyError):
        insert_results(conn, mauvais, "h", "f")
    # le rollback ne doit rien laisser derrière lui... y compris le marquage du fichier
    assert conn.execute("SELECT COUNT(*) FROM fichiers_traites").fetchone()[0] == 0


class _ConnQuiEchoue:
    """Fausse connexion : échoue `n` fois puis délègue à la vraie."""

    def __init__(self, vraie, n):
        self.vraie, self.restants = vraie, n

    def __enter__(self):
        if self.restants > 0:
            self.restants -= 1
            raise sqlite3.OperationalError("database is locked")
        return self.vraie.__enter__()

    def __exit__(self, *a):
        return self.vraie.__exit__(*a)

    def __getattr__(self, nom):
        return getattr(self.vraie, nom)


def test_retry_reussit_apres_echecs(conn, csv_files):
    attentes = []
    fausse = _ConnQuiEchoue(conn, 2)
    chemin = csv_files[0]
    inserer_avec_retry(fausse, _results(chemin), hash_fichier(chemin), "f", 3, 0.5, attentes.append)
    assert attentes == [0.5, 0.5]
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 5


def test_retry_abandon_propre(conn, csv_files):
    fausse = _ConnQuiEchoue(conn, 99)
    chemin = csv_files[0]
    with pytest.raises(PipelineError, match="3 tentatives"):
        inserer_avec_retry(fausse, _results(chemin), hash_fichier(chemin), "f", 3, 0, lambda s: None)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_pipeline_bout_en_bout_idempotent(tmp_path, s3_client, bucket):
    data, db = str(tmp_path / "d"), str(tmp_path / "p.db")
    assert run_pipeline(data, db, bucket, delai=0) == 3
    assert run_pipeline(data, db, bucket, delai=0) == 0  # relance : rien de retraité
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM fichiers_traites").fetchone()[0] == 3
    c.close()


def test_pipeline_lit_depuis_s3_pas_depuis_le_disque(tmp_path, s3_client, bucket):
    """Les CSV locaux supprimés après l'envoi : le traitement doit quand même fonctionner."""
    data = str(tmp_path / "d")
    cles = s3_store.upload_files(s3_client, bucket, generate_csv_files(data))
    for f in os.listdir(data):
        os.remove(os.path.join(data, f))
    conn = create_database(str(tmp_path / "p.db"))
    assert all(traiter_objet(conn, s3_client, bucket, cle) for cle in cles)
    conn.close()
