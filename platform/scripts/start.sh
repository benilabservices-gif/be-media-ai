#!/bin/sh
# Démarrage en production (Render) : migrations et configuration métier, puis API.
# L'offre gratuite de Render n'a pas de commande « pre-deploy » : on les enchaîne ici.
# Sans risque en redémarrage : les migrations et la publication de config sont idempotentes.
set -e

alembic upgrade head
python -m digital360.cli seed-config
exec uvicorn digital360.main:create_app --factory \
    --host 0.0.0.0 --port "${PORT:-8000}" \
    --proxy-headers --forwarded-allow-ips "*"
