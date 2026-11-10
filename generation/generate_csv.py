"""Génération de fichiers CSV de transactions financières (données déterministes)."""
import csv
import os

COLONNES = [
    "datetime_transaction",
    "iban_origine",
    "pays_source",
    "banque_source",
    "iban_destinataire",
    "pays_destinataire",
    "montant",
]

# Jeux de données fixes : aucun aléa, donc les résultats sont testables.
# Cas couverts : montants > 5000, plusieurs IBAN, plusieurs banques.
JEUX_DE_DONNEES = {
    "transactions_1.csv": [
        ("2024-01-01 09:00:00", "FR7612345000010000000001", "FR", "BNP", "DE89370400440532013000", "DE", 100.00),
        ("2024-01-01 10:30:00", "FR7612345000010000000001", "FR", "BNP", "ES9121000418450200051332", "ES", 250.50),
        ("2024-01-02 11:15:00", "FR7612345000010000000002", "FR", "BNP", "DE89370400440532013000", "DE", 5000.00),
        ("2024-01-02 14:45:00", "FR7612345000010000000002", "FR", "BNP", "IT60X0542811101000000123456", "IT", 7500.00),
        ("2024-01-03 08:20:00", "FR7612345000010000000001", "FR", "BNP", "ES9121000418450200051332", "ES", 5000.01),
    ],
    "transactions_2.csv": [
        ("2024-02-01 09:10:00", "DE89370400440532013000", "DE", "Deutsche Bank", "FR7612345000010000000001", "FR", 1200.00),
        ("2024-02-01 12:00:00", "ES9121000418450200051332", "ES", "Santander", "FR7612345000010000000001", "FR", 300.00),
        ("2024-02-02 16:30:00", "DE89370400440532013000", "DE", "Deutsche Bank", "IT60X0542811101000000123456", "IT", 12000.00),
        ("2024-02-03 10:05:00", "ES9121000418450200051332", "ES", "Santander", "DE89370400440532013000", "DE", 49.99),
        ("2024-02-03 18:40:00", "FR7612345000010000000003", "FR", "Société Générale", "ES9121000418450200051332", "ES", 8000.00),
        ("2024-02-04 07:55:00", "FR7612345000010000000003", "FR", "Société Générale", "DE89370400440532013000", "DE", 4999.99),
    ],
    "transactions_3.csv": [
        ("2024-03-01 09:00:00", "IT60X0542811101000000123456", "IT", "UniCredit", "FR7612345000010000000002", "FR", 20.00),
        ("2024-03-01 13:25:00", "IT60X0542811101000000123456", "IT", "UniCredit", "FR7612345000010000000003", "FR", 6000.00),
        ("2024-03-02 15:00:00", "FR7612345000010000000002", "FR", "BNP", "IT60X0542811101000000123456", "IT", 750.75),
        ("2024-03-02 17:10:00", "FR7612345000010000000003", "FR", "Société Générale", "DE89370400440532013000", "DE", 5000.00),
        ("2024-03-03 08:00:00", "DE89370400440532013000", "DE", "Deutsche Bank", "ES9121000418450200051332", "ES", 15000.00),
        ("2024-03-03 19:30:00", "ES9121000418450200051332", "ES", "Santander", "IT60X0542811101000000123456", "IT", 10.00),
        ("2024-03-04 11:11:00", "ES9121000418450200051332", "ES", "Santander", "FR7612345000010000000001", "FR", 5500.00),
    ],
}


def generate_csv_files(output_dir="data"):
    """Écrit les CSV dans output_dir et retourne la liste des chemins créés."""
    os.makedirs(output_dir, exist_ok=True)
    chemins = []
    for nom, lignes in JEUX_DE_DONNEES.items():
        chemin = os.path.join(output_dir, nom)
        with open(chemin, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(COLONNES)
            writer.writerows(lignes)
        chemins.append(chemin)
    return chemins


if __name__ == "__main__":
    for p in generate_csv_files():
        print(f"Fichier généré : {p}")
