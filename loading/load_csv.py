"""Chargement d'un fichier CSV de transactions."""
import pandas as pd


def load_transactions(path):
    """Lit un CSV et retourne une liste de dictionnaires (un par transaction).

    Le montant est converti en float. Lève FileNotFoundError si le fichier
    n'existe pas.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df["montant"] = df["montant"].astype(float)
    return df.to_dict("records")
