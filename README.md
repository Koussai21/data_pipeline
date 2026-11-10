# Pipeline de données financières (CI/CD – niveau 1)

Pipeline Python : génération de CSV de transactions → chargement → traitement
(sommes, détection des gros montants) → insertion SQLite avec retry.
Un fichier déjà traité (même hash SHA-256) n'est jamais retraité.

## Structure

| Dossier | Rôle |
|---|---|
| `generation/` | génère 3 CSV déterministes dans `data/` |
| `loading/` | `load_transactions(path)` → liste de dicts |
| `processing/` | fonctions pures (sommes par IBAN/banque, `flag_large_amounts`) |
| `database/` | création SQLite, insertion, suivi `fichiers_traites` |
| `pipeline/` | orchestration + retry |
| `tests/` | tests unitaires et d'intégration |

## Lancement

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m pipeline.run_pipeline   # crée data/*.csv et pipeline.db
python -m pipeline.run_pipeline   # 2e lancement : fichiers ignorés
pytest -v                         # tests
```

Résultat : base `pipeline.db` (tables `resultats`, `transactions`, `fichiers_traites`).
Code de sortie 1 si l'insertion échoue après toutes les tentatives (3 par défaut, 1 s d'attente).

## CI

`.github/workflows/ci.yml` lance `pytest` à chaque push.
