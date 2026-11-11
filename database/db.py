"""Accès base de données : SQLite (local/tests) ou PostgreSQL (production).

Le choix se fait via la chaîne passée à create_database : une URL
`postgresql://...` (ex. variable DATABASE_URL) -> PostgreSQL, sinon chemin SQLite.
Le SQL est écrit avec des `?` ; il est converti en `%s` pour PostgreSQL.
"""
import hashlib
import sqlite3

try:  # psycopg n'est requis que pour PostgreSQL
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

# Types d'erreurs communs aux deux moteurs (utilisés par le pipeline pour le retry)
ERREURS_BASE = (sqlite3.Error,) + ((psycopg.Error,) if psycopg else ())
ERREURS_INTEGRITE = (sqlite3.IntegrityError,) + ((psycopg.IntegrityError,) if psycopg else ())

# Schéma commun ; les types qui diffèrent selon le moteur sont des paramètres.
SCHEMA = """
CREATE TABLE IF NOT EXISTS resultats (
    id {cle_auto},
    type_agregat TEXT NOT NULL,   -- iban_origine / banque_source / iban_destinataire
    cle TEXT NOT NULL,
    total {reel} NOT NULL,
    hash_fichier TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
    id {cle_auto},
    datetime_transaction TEXT,
    iban_origine TEXT,
    pays_source TEXT,
    banque_source TEXT,
    iban_destinataire TEXT,
    pays_destinataire TEXT,
    montant {reel},
    gros_montant INTEGER NOT NULL,
    hash_fichier TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fichiers_traites (
    hash_fichier TEXT PRIMARY KEY,   -- SHA-256 du contenu : jamais traité deux fois
    nom_fichier TEXT NOT NULL,
    date_traitement {horodatage} NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

TYPES_SQLITE = {"cle_auto": "INTEGER PRIMARY KEY AUTOINCREMENT", "reel": "REAL", "horodatage": "TEXT"}
TYPES_POSTGRES = {"cle_auto": "BIGSERIAL PRIMARY KEY", "reel": "DOUBLE PRECISION", "horodatage": "TIMESTAMP"}

# Correspondance clé du dictionnaire `results` -> type_agregat en base.
AGREGATS = {
    "sum_by_origin_iban": "iban_origine",
    "sum_by_source_bank": "banque_source",
    "sum_by_destination_iban": "iban_destinataire",
}


def est_postgres(cible):
    """Vrai si `cible` est une URL PostgreSQL."""
    return str(cible).startswith(("postgresql://", "postgres://"))


class Connexion:
    """Petite surcouche uniforme sur sqlite3 / psycopg (execute, executemany, transaction)."""

    def __init__(self, brute, postgres):
        self._brute = brute
        self.postgres = postgres

    def _sql(self, sql):
        return sql.replace("?", "%s") if self.postgres else sql

    def execute(self, sql, params=()):
        if self.postgres:
            cur = self._brute.cursor()
            cur.execute(self._sql(sql), params)
            return cur
        return self._brute.execute(sql, params)

    def executemany(self, sql, lignes):
        if self.postgres:
            cur = self._brute.cursor()
            cur.executemany(self._sql(sql), lignes)
            return cur
        return self._brute.executemany(sql, lignes)

    def commit(self):
        self._brute.commit()

    def rollback(self):
        self._brute.rollback()

    def close(self):
        self._brute.close()

    # `with conn:` = une transaction (commit si OK, rollback si exception), sans fermer la connexion
    def __enter__(self):
        return self

    def __exit__(self, type_exc, exc, tb):
        if type_exc is None:
            self.commit()
        else:
            self.rollback()
        return False


def create_database(cible):
    """Crée (si besoin) la base et ses tables ; retourne une Connexion.

    `cible` : chemin SQLite ou URL PostgreSQL (postgresql://user:mdp@hote:5432/base).
    """
    if est_postgres(cible):
        if psycopg is None:
            raise RuntimeError("psycopg est requis pour PostgreSQL (pip install 'psycopg[binary]')")
        conn = Connexion(psycopg.connect(cible), postgres=True)
        types = TYPES_POSTGRES
    else:
        conn = Connexion(sqlite3.connect(cible), postgres=False)
        types = TYPES_SQLITE
    with conn:
        for instruction in SCHEMA.format(**types).split(";"):
            conn.execute(instruction)
    return conn


def hash_contenu(contenu):
    """Calcule le SHA-256 d'un contenu en mémoire (octets), ex. un objet lu depuis S3."""
    return hashlib.sha256(contenu).hexdigest()


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
    d'erreur, rien n'est écrit. Lève une erreur d'intégrité si le fichier a
    déjà été traité (clé primaire sur le hash).
    """
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
