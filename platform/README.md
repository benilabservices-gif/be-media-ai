# BENILAB Digital360 — Platform

Moteur backend de BENILAB Digital360 : API, logique métier, base de données et, plus tard, workers.
Le frontend statique (Kilo) vit dans [`../digital360/`](../digital360/) et consomme cette API.

- Architecture complète : [ARCHITECTURE.md](ARCHITECTURE.md)
- Contrat d'API (OpenAPI) : `GET /api/v1/openapi.json`, documentation interactive sur `/api/v1/docs` (désactivée en production)

## État actuel : M0 (socle)

| Disponible | À venir (voir ARCHITECTURE.md §18) |
|---|---|
| App FastAPI, config par variables d'environnement | M1 : tenancy + RLS, permissions, state machine, jobs, audit |
| Logs JSON avec `request_id` | M2 : authentification, organisations |
| Erreurs au format `application/problem+json` | M3 : diagnostic, score, recommandations, passport |
| Sondes `/api/v1/health/live` et `/api/v1/health/ready` | … |
| Alembic (async), Docker, CI | |

## Démarrage

### Avec Docker

```bash
cp .env.example .env
docker compose up --build
# API : http://localhost:8000/api/v1/health/ready
```

### Sans Docker (PostgreSQL 16 installé localement)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env               # adapter DATABASE_URL
alembic upgrade head
uvicorn digital360.main:create_app --factory --reload
```

Toutes les commandes se lancent **depuis `platform/`** : `alembic.ini` y est résolu relativement.

## Qualité et tests

```bash
ruff check . && black --check . && mypy src && lint-imports
pytest                                            # tests unitaires ; l'intégration est ignorée
TEST_DATABASE_URL=postgresql+asyncpg://... pytest # avec une base migrée : tout est exécuté
```

- `tests/unit/` : aucune dépendance externe.
- `tests/integration/` : vrai PostgreSQL (pas de mock de la base). Ignorés si `TEST_DATABASE_URL` n'est pas défini.
- La CI ([`.github/workflows/platform.yml`](../.github/workflows/platform.yml)) exécute tout, avec PostgreSQL, et vérifie que les migrations sont réversibles.

## Migrations

```bash
alembic revision --autogenerate -m "ajoute la table organizations"
alembic upgrade head
alembic downgrade -1
```

Les migrations doivent rester rétrocompatibles (expand → migrate → contract) : en production, elles passent avant le démarrage des nouvelles instances.

## Variables d'environnement

| Variable | Obligatoire | Description |
|---|---|---|
| `DATABASE_URL` | oui | `postgresql+asyncpg://user:pass@host:port/db` |
| `APP_ENV` | non (`local`) | `local`, `test`, `staging`, `production` |
| `LOG_LEVEL` | non (`INFO`) | Niveau de log |
| `CORS_ALLOWED_ORIGINS` | non | Origines du frontend, séparées par des virgules |
| `ALEMBIC_INI_PATH` | non (`alembic.ini`) | Chemin d'`alembic.ini`, relatif au dossier de lancement |

## Déploiement

Le dossier `platform/` est exclu du déploiement Vercel (`../.vercelignore`) : il ne doit jamais être servi publiquement. L'API se déploie comme conteneur Docker (voir ARCHITECTURE.md §16.2). Ordre de release : `alembic upgrade head`, puis démarrage des instances.

## Dépannage

| Symptôme | Cause probable |
|---|---|
| `/health/ready` → `base injoignable` | `DATABASE_URL` incorrect ou PostgreSQL arrêté |
| `/health/ready` → `migrations non à jour` | Lancer `alembic upgrade head` |
| `DATABASE_URL doit utiliser le schéma postgresql+asyncpg://` | Ajouter `+asyncpg` au schéma de l'URL |
| `alembic` ne trouve pas sa configuration | Lancer la commande depuis `platform/` |
