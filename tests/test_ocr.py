"""Tests OCR : analyse de texte (unitaires) et lecture d'images (nécessite Tesseract)."""
import os
import sqlite3

import pytest

from database.db import create_database
from ocr import ocr
from ocr.generate_images import (
    TRANSACTIONS_IMAGES,
    creer_image,
    generate_and_upload_images,
    generate_images,
    lignes_transaction,
)
from ocr.ocr import OcrError, parse_transaction, validate_transaction
from pipeline.run_pipeline import run_pipeline, traiter_objet
from storage import s3_store

TEXTE = """TRANSACTION BANCAIRE
Date: 2024-04-02 14:30:00
IBAN origine: DE89 3704 0044 0532 0130 00
Pays source: de
Banque source: Deutsche Bank
IBAN destinataire: ES9121000418450200051332
Pays destinataire: ES
Montant: 7 500,00 EUR
"""

ATTENDU = {
    "datetime_transaction": "2024-04-02 14:30:00",
    "iban_origine": "DE89370400440532013000",
    "pays_source": "DE",
    "banque_source": "Deutsche Bank",
    "iban_destinataire": "ES9121000418450200051332",
    "pays_destinataire": "ES",
    "montant": 7500.0,
}

# Tesseract est obligatoire en CI ; en local on ignore ces tests s'il manque
besoin_tesseract = pytest.mark.skipif(
    not ocr.tesseract_disponible() and not os.environ.get("CI"),
    reason="Tesseract non installé",
)


# ---------- Tests unitaires (sans Tesseract) ----------

def test_parse_transaction_texte_fixe():
    assert parse_transaction(TEXTE) == ATTENDU


def test_parse_accepte_point_decimal():
    t = parse_transaction(TEXTE.replace("7 500,00", "250.50"))
    assert t["montant"] == 250.50


def test_parse_champ_manquant():
    with pytest.raises(OcrError, match="introuvable.*montant"):
        parse_transaction(TEXTE.replace("Montant:", "Somme:"))


@pytest.mark.parametrize("ancien,nouveau,message", [
    ("ES9121000418450200051332", "12345", "IBAN invalide"),
    ("7 500,00", "0,00", "non positif"),
    ("2024-04-02 14:30:00", "2024-13-45 14:30:00", "Date invalide"),
])
def test_validation_refuse_les_donnees_invalides(ancien, nouveau, message):
    with pytest.raises(OcrError, match=message):
        parse_transaction(TEXTE.replace(ancien, nouveau))


def test_validate_transaction_ok():
    assert validate_transaction(dict(ATTENDU)) == ATTENDU


def test_generation_images_deterministe(tmp_path):
    a = generate_images(str(tmp_path / "a"))
    b = generate_images(str(tmp_path / "b"))
    assert len(a) == 3
    for x, y in zip(a, b):
        assert open(x, "rb").read() == open(y, "rb").read()
    # au moins une image dépasse 5000
    assert any(t[-1] > 5000 for t in TRANSACTIONS_IMAGES.values())


def test_texte_affiche_est_relisible_par_le_parseur():
    for t in TRANSACTIONS_IMAGES.values():
        parsed = parse_transaction("\n".join(lignes_transaction(t)))
        assert parsed["montant"] == t[-1] and parsed["iban_origine"] == t[1]


# ---------- Tests d'intégration (Tesseract + S3) ----------

@besoin_tesseract
def test_ocr_lit_une_image(tmp_path):
    for nom, t in TRANSACTIONS_IMAGES.items():
        chemin = str(tmp_path / nom)
        creer_image(t, chemin)
        lu = parse_transaction(ocr.extract_text(chemin))
        assert lu == {
            "datetime_transaction": t[0], "iban_origine": t[1], "pays_source": t[2],
            "banque_source": t[3], "iban_destinataire": t[4], "pays_destinataire": t[5],
            "montant": t[6],
        }


@besoin_tesseract
def test_image_s3_ocr_insertion_et_flag(tmp_path, s3_client, bucket):
    cles = generate_and_upload_images(s3_client, bucket, str(tmp_path / "img"))
    assert s3_store.list_image_keys(s3_client, bucket) == sorted(cles)
    conn = create_database(str(tmp_path / "p.db"))
    assert all(traiter_objet(conn, s3_client, bucket, c) for c in cles)
    lignes = conn.execute(
        "SELECT montant, gros_montant FROM transactions ORDER BY montant").fetchall()
    assert lignes == [(42.75, 0), (250.5, 0), (7500.0, 1)]
    total = conn.execute(
        "SELECT total FROM resultats WHERE type_agregat='banque_source' AND cle='Deutsche Bank'"
    ).fetchone()[0]
    assert total == 7500.0
    # anti-doublon : le même contenu n'est jamais retraité
    assert not any(traiter_objet(conn, s3_client, bucket, c) for c in cles)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 3
    conn.close()


@besoin_tesseract
def test_pipeline_complet_csv_et_images(tmp_path, s3_client, bucket):
    data, db = str(tmp_path / "d"), str(tmp_path / "p.db")
    assert run_pipeline(data, db, bucket, delai=0, avec_images=True) == 6  # 3 CSV + 3 images
    assert run_pipeline(data, db, bucket, delai=0, avec_images=True) == 0
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM transactions WHERE gros_montant=1").fetchone()[0] == 8  # 7 en CSV + 1 image
    c.close()
