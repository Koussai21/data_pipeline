"""Génération d'images de transactions (Pillow) pour tester l'OCR, avec envoi vers S3."""
import os

from PIL import Image, ImageDraw, ImageFont

PREFIXE_S3 = "images/"

# Contenu déterministe (même ordre de colonnes que les CSV). Une image > 5000.
TRANSACTIONS_IMAGES = {
    "transaction_img_1.png": ("2024-04-01 09:15:00", "FR7612345000010000000001", "FR", "BNP",
                              "DE89370400440532013000", "DE", 250.50),
    "transaction_img_2.png": ("2024-04-02 14:30:00", "DE89370400440532013000", "DE", "Deutsche Bank",
                              "ES9121000418450200051332", "ES", 7500.00),
    "transaction_img_3.png": ("2024-04-03 18:45:00", "ES9121000418450200051332", "ES", "Santander",
                              "IT60X0542811101000000123456", "IT", 42.75),
}

POLICES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "DejaVuSansMono.ttf",
]


def _police(taille=34):
    """Police nette à chasse fixe (meilleure pour l'OCR) ; repli sur la police Pillow."""
    for chemin in POLICES:
        try:
            return ImageFont.truetype(chemin, taille)
        except OSError:
            continue
    return ImageFont.load_default(size=taille)


def lignes_transaction(t):
    """Texte affiché sur l'image pour une transaction (format lu par ocr.parse_transaction)."""
    dt, iban_o, pays_s, banque, iban_d, pays_d, montant = t
    return [
        "TRANSACTION BANCAIRE",
        f"Date: {dt}",
        f"IBAN origine: {iban_o}",
        f"Pays source: {pays_s}",
        f"Banque source: {banque}",
        f"IBAN destinataire: {iban_d}",
        f"Pays destinataire: {pays_d}",
        f"Montant: {montant:.2f} EUR",
    ]


def creer_image(transaction, chemin):
    """Dessine une fiche noir sur blanc (grande police, marges) et l'enregistre en PNG."""
    police = _police()
    lignes = lignes_transaction(transaction)
    img = Image.new("RGB", (1100, 60 + 60 * len(lignes)), "white")
    dessin = ImageDraw.Draw(img)
    for i, ligne in enumerate(lignes):
        dessin.text((40, 40 + 60 * i), ligne, fill="black", font=police)
    img.save(chemin, format="PNG")


def generate_images(output_dir="data/images"):
    """Crée les images de test et retourne la liste des chemins."""
    os.makedirs(output_dir, exist_ok=True)
    chemins = []
    for nom, transaction in TRANSACTIONS_IMAGES.items():
        chemin = os.path.join(output_dir, nom)
        creer_image(transaction, chemin)
        chemins.append(chemin)
    return chemins


def generate_and_upload_images(client, bucket, output_dir="data/images"):
    """Génère les images puis les envoie dans S3 sous le préfixe images/ ; retourne les clés."""
    from storage import s3_store

    chemins = generate_images(output_dir)
    cles = s3_store.upload_files(client, bucket, chemins, prefix=PREFIXE_S3)
    s3_store.verify_files_present(client, bucket, cles)
    return cles
