"""Tests unitaires des fonctions pures de traitement."""
import copy

from processing.processing import (
    flag_large_amounts,
    sum_by_destination_iban,
    sum_by_origin_iban,
    sum_by_source_bank,
)

TX = [
    {"iban_origine": "A", "banque_source": "B1", "iban_destinataire": "X", "montant": 100.0},
    {"iban_origine": "A", "banque_source": "B1", "iban_destinataire": "Y", "montant": 5000.0},
    {"iban_origine": "C", "banque_source": "B2", "iban_destinataire": "X", "montant": 6000.5},
]


def test_sum_by_origin_iban():
    assert sum_by_origin_iban(TX) == {"A": 5100.0, "C": 6000.5}


def test_sum_by_source_bank():
    assert sum_by_source_bank(TX) == {"B1": 5100.0, "B2": 6000.5}


def test_sum_by_destination_iban():
    assert sum_by_destination_iban(TX) == {"X": 6100.5, "Y": 5000.0}


def test_listes_vides():
    assert sum_by_origin_iban([]) == {}
    assert flag_large_amounts([]) == []


def test_flag_seuil_strict():
    # 5000 pile n'est PAS un gros montant (strictement supérieur au seuil)
    flags = [t["gros_montant"] for t in flag_large_amounts(TX)]
    assert flags == [False, False, True]


def test_flag_seuil_personnalise():
    flags = [t["gros_montant"] for t in flag_large_amounts(TX, threshold=100)]
    assert flags == [False, True, True]


def test_flag_ne_modifie_pas_l_entree():
    avant = copy.deepcopy(TX)
    resultat = flag_large_amounts(TX)
    assert TX == avant
    assert "gros_montant" not in TX[0]
    assert resultat is not TX and resultat[0] is not TX[0]
