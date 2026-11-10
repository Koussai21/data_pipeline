"""Fonctions de traitement pures : aucune ne modifie ses arguments."""


def _somme_par_cle(transactions, cle):
    """Additionne les montants regroupés par la valeur de la colonne `cle`."""
    totaux = {}
    for t in transactions:
        totaux[t[cle]] = round(totaux.get(t[cle], 0.0) + float(t["montant"]), 2)
    return totaux


def sum_by_origin_iban(transactions):
    """Somme des montants par IBAN d'origine."""
    return _somme_par_cle(transactions, "iban_origine")


def sum_by_source_bank(transactions):
    """Somme des montants par banque source."""
    return _somme_par_cle(transactions, "banque_source")


def sum_by_destination_iban(transactions):
    """Somme des montants par IBAN destinataire."""
    return _somme_par_cle(transactions, "iban_destinataire")


def flag_large_amounts(transactions, threshold=5000):
    """Retourne une copie annotée : champ `gros_montant` vrai si montant > seuil.

    La liste et les dictionnaires d'entrée ne sont pas modifiés.
    """
    return [
        {**t, "gros_montant": float(t["montant"]) > threshold}
        for t in transactions
    ]
