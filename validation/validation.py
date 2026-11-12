"""Validation des transactions : règles uniques, partagées par les CSV et l'OCR."""
import math
import re
from datetime import datetime

from generation.generate_csv import COLONNES

# Champs obligatoires (les mêmes colonnes que les CSV générés)
CHAMPS = list(COLONNES)

# IBAN : 2 lettres, 2 chiffres, puis 11 à 30 caractères alphanumériques
IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
# Pays : exactement 2 lettres
PAYS_RE = re.compile(r"^[A-Z]{2}$")
FORMAT_DATE = "%Y-%m-%d %H:%M:%S"


class ValidationError(Exception):
    """Donnée invalide ; le message indique le fichier, la ligne, le champ et la raison."""

    def __init__(self, raison, fichier=None, ligne=None, champ=None, erreurs=None):
        self.raison, self.fichier, self.ligne, self.champ = raison, fichier, ligne, champ
        # `erreurs` : liste des messages quand plusieurs lignes sont invalides
        self.erreurs = erreurs or [self._formater()]
        super().__init__(raison if erreurs else self.erreurs[0])

    def _formater(self):
        """Ex. : 'transactions_1.csv, ligne 3, champ montant : Montant non positif : -5.0'."""
        morceaux = []
        if self.fichier:
            morceaux.append(str(self.fichier))
        if self.ligne is not None:
            morceaux.append(f"ligne {self.ligne}")
        if self.champ:
            morceaux.append(f"champ {self.champ}")
        return (", ".join(morceaux) + " : " if morceaux else "") + self.raison


def convertir_montant(valeur, **contexte):
    """Convertit un montant (str, int ou float) en float fini ; sinon ValidationError."""
    try:
        montant = float(str(valeur).strip()) if isinstance(valeur, str) else float(valeur)
    except (TypeError, ValueError):
        raise ValidationError(f"Montant non numérique : '{valeur}'", champ="montant", **contexte)
    if not math.isfinite(montant):
        raise ValidationError(f"Montant non numérique : '{valeur}'", champ="montant", **contexte)
    return montant


def valider_transaction(t, ligne=None, fichier=None):
    """Valide une transaction et retourne une COPIE normalisée (l'entrée n'est pas modifiée).

    Normalisation : IBAN sans espaces et en majuscules, pays en majuscules, montant en float.
    Lève ValidationError à la première règle violée.
    """
    ctx = {"fichier": fichier, "ligne": ligne}

    # 1. Champs obligatoires, présents et non vides
    for champ in CHAMPS:
        valeur = t.get(champ)
        if valeur is None or str(valeur).strip() == "":
            raise ValidationError(f"Champ obligatoire manquant ou vide : {champ}", champ=champ, **ctx)

    n = {champ: str(t[champ]).strip() for champ in CHAMPS if champ != "montant"}

    # 2. IBAN (origine et destinataire)
    for champ in ("iban_origine", "iban_destinataire"):
        n[champ] = re.sub(r"\s+", "", n[champ]).upper()
        if not IBAN_RE.match(n[champ]):
            raise ValidationError(f"IBAN invalide ({champ}) : '{n[champ]}'", champ=champ, **ctx)

    # 3. Pays sur 2 lettres
    for champ in ("pays_source", "pays_destinataire"):
        n[champ] = n[champ].upper()
        if not PAYS_RE.match(n[champ]):
            raise ValidationError(f"Pays invalide ({champ}) : '{n[champ]}' (2 lettres attendues)",
                                  champ=champ, **ctx)

    # 4. Montant numérique et strictement positif
    n["montant"] = convertir_montant(t["montant"], **ctx)
    if not n["montant"] > 0:
        raise ValidationError(f"Montant non positif : {n['montant']}", champ="montant", **ctx)

    # 5. Date au format YYYY-MM-DD HH:MM:SS
    try:
        datetime.strptime(n["datetime_transaction"], FORMAT_DATE)
    except ValueError:
        raise ValidationError(f"Date invalide : '{n['datetime_transaction']}'",
                              champ="datetime_transaction", **ctx)

    return {champ: n[champ] for champ in CHAMPS}  # ordre des colonnes conservé


def valider_transactions(transactions, nom_fichier=None):
    """Valide toute une liste ; retourne la liste normalisée ou lève UNE ValidationError.

    Toutes les lignes invalides sont signalées (pas seulement la première).
    Numérotation des lignes comme dans le CSV : la ligne 1 est l'en-tête,
    la première transaction est donc la ligne 2.
    """
    valides, erreurs = [], []
    for i, t in enumerate(transactions):
        try:
            valides.append(valider_transaction(t, ligne=i + 2, fichier=nom_fichier))
        except ValidationError as e:
            erreurs.append(e.erreurs[0])
    if erreurs:
        entete = f"{len(erreurs)} ligne(s) invalide(s)" + (f" dans {nom_fichier}" if nom_fichier else "")
        raise ValidationError(entete + " :\n  - " + "\n  - ".join(erreurs), erreurs=erreurs)
    return valides
