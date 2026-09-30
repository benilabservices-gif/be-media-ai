# BENILAB Digital360 — Platform

Moteur backend de BENILAB Digital360 : API, logique métier, base de données et worker de tâches.
Le frontend statique (Kilo) vit dans [`../digital360/`](../digital360/) et consomme cette API.

- Architecture complète : [ARCHITECTURE.md](ARCHITECTURE.md)
- Contrat d'API (OpenAPI) : `GET /api/v1/openapi.json`, documentation interactive sur `/api/v1/docs` (désactivée en production)

## État actuel : M3 (diagnostic, score, recommandations, passport)

| Disponible | À venir (voir ARCHITECTURE.md §18) |
|---|---|
| App FastAPI, config par variables d'environnement, logs JSON avec `request_id` | M4 : catalogue, prix, entitlements |
| Erreurs `application/problem+json`, sondes `/api/v1/health/live` et `/ready` | M5 : commandes, factures, paiements |
| Isolation des tenants par RLS PostgreSQL forcée (`core/tenancy.py`) | M6+ : production des sites, abonnements… |
| Authentification, organisations, `/me` (M2) | |
| Diagnostic anonyme, Digital Score, plan d'action, Digital Passport (M3) | |
| RBAC par permissions (`core/permissions.py`) | |
| Machine à états + transitions persistées et auditées (`core/state_machine.py`, `core/workflow.py`) | |
| Évaluateur de conditions JSON (`core/rules.py`) | |
| File de tâches / outbox + worker (`core/jobs.py`, `python -m digital360.worker`) | |
| Journal d'audit append-only, clés d'idempotence | |

## Démarrage

### Avec Docker

```bash
cp .env.example .env
docker compose up --build
# API : http://localhost:8000/api/v1/health/ready
```

### Sans Docker (PostgreSQL 16 installé localement)

Créer d'abord le rôle applicatif, **non superuser** (sinon la RLS est ignorée et `/health/ready` refuse le trafic) :

```bash
psql -U postgres -f docker/postgres-init/01-app-role.sql
```

```bash
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env               # adapter DATABASE_URL
alembic upgrade head
python -m digital360.cli seed-config   # publie config/seeds/*.yaml
uvicorn digital360.main:create_app --factory --reload
python -m digital360.worker        # dans un second terminal
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

## Configuration métier (questionnaire, barème, règles)

Fichiers YAML dans [`config/seeds/`](config/seeds/), lisibles par l'équipe BENILAB. Pour modifier : copier le fichier en `vN+1`, incrémenter `version`, modifier, puis `python -m digital360.cli seed-config`. Les diagnostics passés gardent leur version ; publier deux fois la même version est sans effet ; modifier une version déjà publiée est refusé.

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
| `CONFIG_NOT_PUBLISHED` (503) sur le diagnostic | Lancer `python -m digital360.cli seed-config` |
| `/health/ready` → `superuser ou BYPASSRLS` | Se connecter avec le rôle applicatif (`docker/postgres-init/01-app-role.sql`), jamais avec `postgres` |
| Tâche en état `DEAD` | Voir `jobs.last_error` ; corriger puis remettre `status = 'PENDING'` |
| `DATABASE_URL doit utiliser le schéma postgresql+asyncpg://` | Ajouter `+asyncpg` au schéma de l'URL |
| `alembic` ne trouve pas sa configuration | Lancer la commande depuis `platform/` |
