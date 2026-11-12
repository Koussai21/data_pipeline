"""Chargement d'un fichier CSV de transactions."""
import pandas as pd

from validation.validation import CHAMPS, ValidationError, convertir_montant


def load_transactions(path, nom_fichier=None):
    """Lit un CSV (chemin ou objet fichier, ex. BytesIO lu depuis S3) : liste de dicts.

    Le montant est converti en float. Lève ValidationError (message lisible) si le
    CSV est vide, illisible, s'il manque une colonne ou si un montant n'est pas
    numérique. Les autres règles sont appliquées ensuite par valider_transactions.
    Lève FileNotFoundError si le fichier n'existe pas.
    """
    nom = nom_fichier or (path if isinstance(path, str) else None)
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        raise ValidationError("CSV vide", fichier=nom)
    except (pd.errors.ParserError, UnicodeDecodeError) as e:
        raise ValidationError(f"CSV mal formé ou illisible : {e}", fichier=nom)

    manquantes = [c for c in CHAMPS if c not in df.columns]
    if manquantes:
        raise ValidationError(f"Colonne(s) manquante(s) : {', '.join(manquantes)}", fichier=nom)
    if df.empty:
        raise ValidationError("CSV sans aucune transaction", fichier=nom)

    transactions = df.fillna("").to_dict("records")  # lignes trop courtes -> champs vides
    erreurs = []
    for i, t in enumerate(transactions):
        try:
            t["montant"] = convertir_montant(t["montant"], fichier=nom, ligne=i + 2)
        except ValidationError as e:
            erreurs.append(e.erreurs[0])
    if erreurs:
        raise ValidationError("Montant(s) non convertible(s) :\n  - " + "\n  - ".join(erreurs),
                              erreurs=erreurs)
    return transactions
