"""Accès SQLite : création du schéma, insertion des résultats, suivi des fichiers."""
import hashlib
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS resultats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type_agregat TEXT NOT NULL,   -- iban_origine / banque_source / iban_destinataire
    cle TEXT NOT NULL,
    total REAL NOT NULL,
    hash_fichier TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    datetime_transaction TEXT,
    iban_origine TEXT,
    pays_source TEXT,
    banque_source TEXT,
    iban_destinataire TEXT,
    pays_destinataire TEXT,
    montant REAL,
    gros_montant INTEGER NOT NULL,
    hash_fichier TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fichiers_traites (
    hash_fichier TEXT PRIMARY KEY,   -- SHA-256 du contenu : jamais traité deux fois
    nom_fichier TEXT NOT NULL,
    date_traitement TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# Correspondance clé du dictionnaire `results` -> type_agregat en base.
AGREGATS = {
    "sum_by_origin_iban": "iban_origine",
    "sum_by_source_bank": "banque_source",
    "sum_by_destination_iban": "iban_destinataire",
}


def create_database(path):
    """Crée (si besoin) la base SQLite et ses tables ; retourne la connexion."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def hash_fichier(path):
    """Calcule le SHA-256 du contenu d'un fichier."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloc in iter(lambda: f.read(65536), b""):
            h.update(bloc)
    return h.hexdigest()


def is_file_processed(conn, hash_f):
    """Vrai si un fichier de ce hash a déjà été traité."""
    cur = conn.execute("SELECT 1 FROM fichiers_traites WHERE hash_fichier = ?", (hash_f,))
    return cur.fetchone() is not None


def insert_results(conn, results, hash_f="inconnu", nom_fichier="inconnu"):
    """Insère les résultats d'un fichier de façon atomique.

    `results` contient les clés sum_by_origin_iban, sum_by_source_bank,
    sum_by_destination_iban (dicts) et transactions (liste annotée).
    Résultats et marquage du fichier sont dans la même transaction : en cas
    d'erreur, rien n'est écrit. Lève sqlite3.IntegrityError si le fichier a
    déjà été traité.
    """
    try:
        with conn:  # commit si OK, rollback si exception
            conn.execute(
                "INSERT INTO fichiers_traites (hash_fichier, nom_fichier) VALUES (?, ?)",
                (hash_f, nom_fichier),
            )
            for cle_res, type_agg in AGREGATS.items():
                conn.executemany(
                    "INSERT INTO resultats (type_agregat, cle, total, hash_fichier) VALUES (?, ?, ?, ?)",
                    [(type_agg, k, v, hash_f) for k, v in results.get(cle_res, {}).items()],
                )
            conn.executemany(
                "INSERT INTO transactions (datetime_transaction, iban_origine, pays_source,"
                " banque_source, iban_destinataire, pays_destinataire, montant, gros_montant,"
                " hash_fichier) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (t["datetime_transaction"], t["iban_origine"], t["pays_source"],
                     t["banque_source"], t["iban_destinataire"], t["pays_destinataire"],
                     t["montant"], int(t["gros_montant"]), hash_f)
                    for t in results.get("transactions", [])
                ],
            )
    except sqlite3.Error:
        conn.rollback()
        raise
