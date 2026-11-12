"""Tests de la validation des transactions (CSV et règles partagées avec l'OCR)."""
import copy
import io

import pytest

from database.db import create_database, hash_contenu, is_file_processed
from generation.generate_csv import generate_csv_files
from loading.load_csv import load_transactions
from ocr import ocr
from pipeline.run_pipeline import traiter_bucket, traiter_objet
from storage import s3_store
from validation import validation
from validation.validation import ValidationError, valider_transaction, valider_transactions

ENTETE = ",".join(validation.CHAMPS)
LIGNE_OK = "2024-01-01 09:00:00,FR7612345000010000000001,FR,BNP,DE89370400440532013000,DE,100.00"


@pytest.fixture
def ligne_valide(tmp_path):
    """Première transaction de transactions_1.csv (générée dans le test)."""
    return load_transactions(generate_csv_files(str(tmp_path))[0])[0]


def _csv(*lignes):
    """Construit un CSV en mémoire (octets) avec l'en-tête standard."""
    return ("\n".join([ENTETE, *lignes]) + "\n").encode()


# ---------- Transaction valide ----------

def test_transaction_valide_ressort_normalisee(ligne_valide):
    assert valider_transaction(ligne_valide) == ligne_valide


def test_normalisation_iban_pays_montant(ligne_valide):
    t = {**ligne_valide, "iban_origine": " fr76 1234 5000 0100 0000 0001 ",
         "pays_source": " fr ", "montant": "250.5"}
    n = valider_transaction(t)
    assert n["iban_origine"] == "FR7612345000010000000001"
    assert n["pays_source"] == "FR" and n["montant"] == 250.5


def test_validation_ne_modifie_pas_l_entree(ligne_valide):
    avant = copy.deepcopy(ligne_valide)
    valider_transaction({**ligne_valide, "pays_source": "fr"})
    assert ligne_valide == avant


# ---------- Cas invalides (un test chacun) ----------

@pytest.mark.parametrize("champ,valeur,attendu", [
    ("iban_origine", "12345", "IBAN invalide"),
    ("iban_destinataire", "DE89", "IBAN invalide"),
    ("montant", "-5", "non positif"),
    ("montant", "0", "non positif"),
    ("montant", "abc", "non numérique"),
    ("montant", "inf", "non numérique"),
    ("datetime_transaction", "2024-13-45 10:00:00", "Date invalide"),
    ("datetime_transaction", "01/01/2024", "Date invalide"),
    ("pays_source", "FRA", "Pays invalide"),
    ("pays_destinataire", "", "obligatoire"),
    ("pays_destinataire", "F1", "Pays invalide"),
    ("banque_source", "  ", "obligatoire"),
])
def test_champ_invalide(ligne_valide, champ, valeur, attendu):
    with pytest.raises(ValidationError, match=attendu) as exc:
        valider_transaction({**ligne_valide, champ: valeur}, ligne=7, fichier="f.csv")
    assert exc.value.champ == champ
    assert "f.csv" in str(exc.value) and "ligne 7" in str(exc.value)


def test_colonne_manquante_dans_un_dict(ligne_valide):
    t = dict(ligne_valide)
    del t["montant"]
    with pytest.raises(ValidationError, match="obligatoire.*montant"):
        valider_transaction(t)


def test_csv_colonne_manquante():
    contenu = b"datetime_transaction,montant\n2024-01-01 09:00:00,10\n"
    with pytest.raises(ValidationError, match="Colonne.*manquante.*iban_origine"):
        load_transactions(io.BytesIO(contenu), nom_fichier="x.csv")


def test_csv_vide():
    with pytest.raises(ValidationError, match="CSV vide"):
        load_transactions(io.BytesIO(b""), nom_fichier="x.csv")
    with pytest.raises(ValidationError, match="sans aucune transaction"):
        load_transactions(io.BytesIO(_csv()))  # en-tête seul


def test_csv_montant_non_numerique_indique_la_ligne():
    contenu = _csv(LIGNE_OK, LIGNE_OK.replace("100.00", "beaucoup"))
    with pytest.raises(ValidationError, match=r"x\.csv, ligne 3, champ montant"):
        load_transactions(io.BytesIO(contenu), nom_fichier="x.csv")


def test_csv_ligne_trop_courte_est_refusee():
    # champs manquants en fin de ligne -> vides -> montant non convertible, avec le n° de ligne
    contenu = _csv(LIGNE_OK, "2024-01-01 09:00:00,FR7612345000010000000001")
    with pytest.raises(ValidationError, match="ligne 3"):
        load_transactions(io.BytesIO(contenu))


def test_csv_valide_se_charge_comme_avant(tmp_path):
    tx = load_transactions(generate_csv_files(str(tmp_path))[0])
    assert len(tx) == 5 and isinstance(tx[0]["montant"], float)
    assert valider_transactions(tx) == tx  # déjà normalisé : rien ne change


def test_valider_transactions_detaille_toutes_les_lignes():
    tx = load_transactions(io.BytesIO(_csv(
        LIGNE_OK,
        LIGNE_OK.replace("FR7612345000010000000001", "12345"),  # IBAN invalide (ligne 3)
        LIGNE_OK,
        LIGNE_OK.replace("2024-01-01", "nope"),  # date invalide (ligne 5)
    )))
    with pytest.raises(ValidationError) as exc:
        valider_transactions(tx, "x.csv")
    assert len(exc.value.erreurs) == 2
    assert "ligne 3" in str(exc.value) and "ligne 5" in str(exc.value)


# ---------- Règles partagées avec l'OCR ----------

def test_ocr_utilise_les_memes_regles():
    assert ocr.IBAN_RE is validation.IBAN_RE and ocr.CHAMPS == validation.CHAMPS
    t = {"datetime_transaction": "2024-01-01 09:00:00", "iban_origine": "XX",
         "pays_source": "FR", "banque_source": "BNP", "iban_destinataire": "DE89370400440532013000",
         "pays_destinataire": "DE", "montant": 10.0}
    with pytest.raises(ocr.OcrError, match="IBAN invalide"):
        ocr.validate_transaction(t)


# ---------- Intégration pipeline (S3 requis, comme conftest.py) ----------

@pytest.fixture
def conn(tmp_path):
    c = create_database(str(tmp_path / "test.db"))
    yield c
    c.close()


def _compte(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_csv_valide_toujours_insere(conn, tmp_path, s3_client, bucket):
    cle = s3_store.upload_files(s3_client, bucket, generate_csv_files(str(tmp_path / "d"))[:1])[0]
    assert traiter_objet(conn, s3_client, bucket, cle) is True
    total = conn.execute("SELECT total FROM resultats WHERE type_agregat='iban_origine' AND cle=?",
                         ("FR7612345000010000000001",)).fetchone()[0]
    assert total == 5350.51 and _compte(conn, "transactions") == 5


def test_fichier_mixte_rien_insere_et_pas_de_hash(conn, s3_client, bucket):
    contenu = _csv(*[LIGNE_OK] * 4, LIGNE_OK.replace("100.00", "-1"))  # 4 valides + 1 invalide
    s3_client.put_object(Bucket=bucket, Key="mixte.csv", Body=contenu)
    with pytest.raises(ValidationError, match="ligne 6"):
        traiter_objet(conn, s3_client, bucket, "mixte.csv")
    assert _compte(conn, "transactions") == 0 and _compte(conn, "resultats") == 0
    assert not is_file_processed(conn, hash_contenu(contenu))
    assert _compte(conn, "fichiers_traites") == 0


def test_bucket_continue_apres_csv_invalide_puis_reprend_le_fichier_corrige(
        tmp_path, s3_client, bucket, capsys):
    db = str(tmp_path / "p.db")
    s3_store.upload_files(s3_client, bucket, generate_csv_files(str(tmp_path / "d"))[:1])
    s3_client.put_object(Bucket=bucket, Key="casse.csv", Body=_csv(LIGNE_OK, "pas,un,csv,valide"))
    # le CSV cassé est signalé, le CSV valide est traité, le cycle ne plante pas
    assert traiter_bucket(s3_client, bucket, db, delai=0, avec_images=False) == 1
    assert "[INVALIDE] casse.csv" in capsys.readouterr().out
    # même fichier encore cassé : toujours rejeté, sans doublon ni effet de bord
    assert traiter_bucket(s3_client, bucket, db, delai=0, avec_images=False) == 0
    # une fois corrigé (contenu différent -> autre hash), il est repris au cycle suivant
    s3_client.put_object(Bucket=bucket, Key="casse.csv", Body=_csv(LIGNE_OK))
    assert traiter_bucket(s3_client, bucket, db, delai=0, avec_images=False) == 1
    conn = create_database(db)
    assert _compte(conn, "transactions") == 6 and _compte(conn, "fichiers_traites") == 2
    conn.close()
