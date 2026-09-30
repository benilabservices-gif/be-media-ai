import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Request
from sqlalchemy import MetaData, make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# Noms de contraintes déterministes : indispensable pour que les migrations Alembic
# puissent supprimer ou renommer une contrainte de façon reproductible
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def engine_options(database_url: str) -> tuple[str, dict[str, Any]]:
    """Adapte une URL PostgreSQL « standard » (celle de Neon, Render…) au pilote asyncpg.

    - `postgresql://` devient `postgresql+asyncpg://`
    - `sslmode` et `channel_binding`, inconnus d'asyncpg, sont traduits en option `ssl`
    - derrière un pooler PgBouncer (hôte `-pooler`), le cache de requêtes préparées est
      désactivé : il est incompatible avec le mode transaction du pooler
    """
    url = make_url(database_url)
    if url.drivername in ("postgresql", "postgres"):
        url = url.set(drivername="postgresql+asyncpg")
    query = dict(url.query)
    connect_args: dict[str, Any] = {}
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode in ("require", "verify-ca", "verify-full"):
        connect_args["ssl"] = "require" if sslmode == "require" else sslmode
    if url.host and "-pooler" in url.host:
        # Configuration SQLAlchemy recommandée pour PgBouncer en mode transaction : aucun
        # cache de requêtes préparées, et des noms uniques pour celles qui restent
        query["prepared_statement_cache_size"] = "0"
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_name_func"] = _unique_statement_name
    return url.set(query=query).render_as_string(hide_password=False), connect_args


def _unique_statement_name() -> str:
    return f"__asyncpg_{uuid.uuid4()}__"


def create_engine(database_url: str) -> AsyncEngine:
    url, connect_args = engine_options(database_url)
    # pool_pre_ping : évite de servir une connexion coupée par le serveur ou un proxy
    return create_async_engine(url, pool_pre_ping=True, connect_args=connect_args)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Dépendance FastAPI : une session par requête."""
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        yield session


def expected_migration_head(alembic_ini_path: Path) -> str | None:
    script = ScriptDirectory.from_config(Config(str(alembic_ini_path)))
    return script.get_current_head()


async def current_migration_revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as connection:
        table_exists = await connection.scalar(text("SELECT to_regclass('alembic_version')"))
        if table_exists is None:
            return None
        revision: str | None = await connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )
        return revision


async def role_bypasses_rls(engine: AsyncEngine) -> bool:
    async with engine.connect() as connection:
        bypasses: bool = await connection.scalar(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
        return bypasses


async def check_database(engine: AsyncEngine, alembic_ini_path: Path) -> dict[str, str]:
    """Vérifie que la base répond, que la RLS s'applique et que le schéma est à jour."""
    try:
        current = await current_migration_revision(engine)
        bypasses_rls = await role_bypasses_rls(engine)
    except (SQLAlchemyError, OSError) as exc:
        return {"status": "error", "detail": f"base injoignable ({type(exc).__name__})"}

    if bypasses_rls:
        # Un superuser ignore la Row Level Security : l'isolation entre clients serait inopérante
        return {
            "status": "error",
            "detail": "le rôle PostgreSQL est superuser ou BYPASSRLS : isolation des tenants inopérante",
        }

    expected = expected_migration_head(alembic_ini_path)
    if current != expected:
        return {
            "status": "error",
            "detail": f"migrations non à jour (base={current}, attendu={expected})",
        }
    return {"status": "ok"}
