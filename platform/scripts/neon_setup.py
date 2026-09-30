"""Prépare une base Neon (ou tout PostgreSQL managé) pour Digital360, en une commande.

1. crée le rôle applicatif `digital360` (NON superuser, sans BYPASSRLS) avec un mot de passe
   aléatoire, et sa base dédiée dont il est propriétaire ;
2. applique les migrations et publie la configuration métier avec ce rôle ;
3. vérifie que la Row Level Security s'applique bien à ce rôle ;
4. affiche l'adresse à coller dans Render (variable DATABASE_URL).

Usage, depuis platform/ :
    .venv\\Scripts\\python scripts\\neon_setup.py

L'adresse du propriétaire (onglet « Connect » de Neon, rôle neondb_owner) est demandée de
façon masquée. Elle n'est ni affichée ni enregistrée. Relancer le script est sans risque :
il régénère le mot de passe du rôle applicatif (pensez alors à mettre à jour Render).
"""

import argparse
import asyncio
import getpass
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import asyncpg
from sqlalchemy import make_url

PLATFORM = Path(__file__).resolve().parents[1]
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_OWNER_URL = re.compile(r"postgres(?:ql)?://[^\s'\"]+")


def _connect_kwargs(url: str) -> dict[str, object]:
    parsed = make_url(url)
    ssl_required = parsed.query.get("sslmode") in ("require", "verify-ca", "verify-full") or (
        parsed.host or ""
    ).endswith("neon.tech")
    return {
        "host": parsed.host,
        "port": parsed.port or 5432,
        "user": parsed.username,
        "password": parsed.password,
        "database": parsed.database,
        "ssl": "require" if ssl_required else None,
    }


async def prepare(owner_url: str, role: str, database: str) -> str:
    password = secrets.token_urlsafe(32)  # [A-Za-z0-9_-] : sans échappement SQL ni URL
    owner = await asyncpg.connect(**_connect_kwargs(owner_url))  # type: ignore[arg-type]
    try:
        exists = await owner.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
        verb = "ALTER" if exists else "CREATE"
        await owner.execute(
            f"{verb} ROLE {role} WITH LOGIN PASSWORD '{password}' "
            "NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB"
        )
        print(f"  rôle {role} {'mis à jour' if exists else 'créé'}")
        if not await owner.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", database):
            await owner.execute(f"CREATE DATABASE {database} OWNER {role}")
            print(f"  base {database} créée (propriétaire : {role})")
        else:
            await owner.execute(f"ALTER DATABASE {database} OWNER TO {role}")
            print(f"  base {database} existante, propriétaire : {role}")
    finally:
        await owner.close()

    parsed = make_url(owner_url)
    # Hôte direct (sans pooler) : pas de contrainte PgBouncer pour les migrations et l'API
    host = (parsed.host or "").replace("-pooler", "")
    app = parsed.set(
        drivername="postgresql",
        username=role,
        password=password,
        host=host,
        database=database,
        query={"sslmode": "require"} if _connect_kwargs(owner_url)["ssl"] else {},
    )
    return app.render_as_string(hide_password=False)


async def verify(app_url: str) -> None:
    connection = await asyncpg.connect(**_connect_kwargs(app_url))  # type: ignore[arg-type]
    try:
        bypasses = await connection.fetchval(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        if bypasses:
            raise SystemExit("ÉCHEC : le rôle applicatif contourne la RLS. Ne pas déployer.")
        tables = await connection.fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
        )
        published = await connection.fetchval(
            "SELECT count(*) FROM config_documents WHERE status = 'PUBLISHED'"
        )
        print(
            f"  RLS active pour le rôle applicatif, {tables} tables, {published} configurations publiées"
        )
    finally:
        await connection.close()


def run(command: list[str], app_url: str) -> None:
    environment = {**os.environ, "DATABASE_URL": app_url}
    result = subprocess.run(command, cwd=PLATFORM, env=environment, check=False)  # noqa: S603
    if result.returncode != 0:
        raise SystemExit(f"ÉCHEC : {' '.join(command)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--role", default="digital360")
    parser.add_argument("--database", default="digital360")
    parser.add_argument(
        "--owner-url-file", type=Path, help="fichier contenant l'adresse propriétaire"
    )
    parser.add_argument(
        "--output", type=Path, help="écrit DATABASE_URL dans ce fichier sans l'afficher"
    )
    args = parser.parse_args()
    for name in (args.role, args.database):
        if not _IDENTIFIER.match(name):
            raise SystemExit(f"nom invalide : {name}")

    if args.owner_url_file:
        # Neon affiche parfois « psql 'postgresql://…' » : on ne garde que l'adresse
        content = args.owner_url_file.read_text(encoding="utf-8-sig")
        found = _OWNER_URL.search(content)
        if not found:
            raise SystemExit(f"aucune adresse postgresql:// trouvée dans {args.owner_url_file}")
        owner_url = found.group(0)
    else:
        owner_url = os.environ.get("OWNER_DATABASE_URL") or getpass.getpass(
            "Adresse de connexion du propriétaire Neon (saisie masquée) : "
        )
    if not owner_url.strip():
        raise SystemExit("aucune adresse saisie")

    print("1/4 Rôle applicatif et base dédiée")
    app_url = asyncio.run(prepare(owner_url.strip(), args.role, args.database))
    print("2/4 Migrations")
    run([sys.executable, "-m", "alembic", "upgrade", "head"], app_url)
    print("3/4 Configuration métier")
    run([sys.executable, "-m", "digital360.cli", "seed-config"], app_url)
    print("4/4 Vérifications")
    asyncio.run(verify(app_url))

    if args.output:
        args.output.write_text(app_url + "\n", encoding="utf-8")
        print(f"\nTerminé. Adresse DATABASE_URL écrite dans {args.output} (à coller dans Render).")
        return
    print(
        "\nTerminé. Collez cette adresse dans Render, variable DATABASE_URL (et nulle part ailleurs) :\n"
    )
    print(f"  {app_url}\n")
    print("Ne la partagez pas : elle contient le mot de passe du rôle applicatif.")


if __name__ == "__main__":
    main()
