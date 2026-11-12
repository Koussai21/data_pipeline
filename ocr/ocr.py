"""Extraction OCR d'une transaction depuis une image (Tesseract + Pillow)."""
import io
import re
import shutil

from PIL import Image

# Les règles de validation vivent dans validation/ (source unique, partagée avec les CSV)
from validation.validation import CHAMPS, IBAN_RE, ValidationError, valider_transaction  # noqa: F401

# Un motif par champ : "Libellé : valeur" sur une ligne.
MOTIFS = {
    "datetime_transaction": r"Date\s*:\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})",
    "iban_origine": r"IBAN\s+origine\s*:\s*([A-Za-z0-9 ]+)",
    "pays_source": r"Pays\s+source\s*:\s*([A-Za-z]{2})\b",
    "banque_source": r"Banque\s+source\s*:\s*(.+)",
    "iban_destinataire": r"IBAN\s+destinataire\s*:\s*([A-Za-z0-9 ]+)",
    "pays_destinataire": r"Pays\s+destinataire\s*:\s*([A-Za-z]{2})\b",
    "montant": r"Montant\s*:\s*([0-9][0-9 .,]*)",
}


class OcrError(Exception):
    """Image illisible ou transaction invalide (message lisible)."""


def tesseract_disponible():
    """Vrai si le binaire tesseract est installé."""
    return shutil.which("tesseract") is not None


def extract_text(image_path):
    """Lit le texte d'une image (chemin, octets ou objet fichier) avec Tesseract."""
    import pytesseract  # import tardif : le reste du projet marche sans OCR

    if isinstance(image_path, (bytes, bytearray)):
        image_path = io.BytesIO(image_path)
    try:
        with Image.open(image_path) as img:
            # --psm 6 : un bloc de texte uniforme, adapté à nos fiches
            return pytesseract.image_to_string(img.convert("L"), config="--psm 6")
    except (OSError, pytesseract.TesseractError, pytesseract.TesseractNotFoundError) as e:
        raise OcrError(f"Lecture OCR impossible : {e}")


def _normaliser_iban(brut):
    return re.sub(r"\s+", "", brut).upper()


def _normaliser_montant(brut):
    """'7 500,00' -> 7500.0 (espaces retirés, virgule décimale acceptée)."""
    texte = brut.strip().replace(" ", "")
    if "," in texte and "." not in texte:
        texte = texte.replace(",", ".")
    texte = texte.replace(",", "")
    return float(texte.rstrip("."))


def parse_transaction(text):
    """Extrait les 7 champs du texte OCR, les valide et retourne un dict (format du loader CSV).

    Lève OcrError si un champ manque ou est invalide.
    """
    trouve = {}
    for champ, motif in MOTIFS.items():
        m = re.search(motif, text, flags=re.IGNORECASE)
        if not m:
            raise OcrError(f"Champ introuvable dans l'image : {champ}")
        trouve[champ] = m.group(1).strip()

    transaction = {
        "datetime_transaction": trouve["datetime_transaction"].replace("T", " "),
        "iban_origine": _normaliser_iban(trouve["iban_origine"]),
        "pays_source": trouve["pays_source"].upper(),
        "banque_source": trouve["banque_source"],
        "iban_destinataire": _normaliser_iban(trouve["iban_destinataire"]),
        "pays_destinataire": trouve["pays_destinataire"].upper(),
    }
    try:
        transaction["montant"] = _normaliser_montant(trouve["montant"])
    except ValueError:
        raise OcrError(f"Montant illisible : '{trouve['montant']}'")
    return validate_transaction(transaction)


def validate_transaction(t):
    """Valide via le module validation/ et retourne la transaction normalisée.

    Les erreurs de validation sont encapsulées en OcrError (message conservé).
    """
    try:
        return valider_transaction(t)
    except ValidationError as e:
        raise OcrError(str(e))


def load_transaction_from_image(source):
    """Image -> OCR -> extraction -> validation : retourne une liste d'UNE transaction."""
    return [parse_transaction(extract_text(source))]
