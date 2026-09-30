from collections.abc import AsyncIterator
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Request
from sqlalchemy import MetaData, text
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


def create_engine(database_url: str) -> AsyncEngine:
    # pool_pre_ping : évite de servir une connexion coupée par le serveur ou un proxy
    return create_async_engine(database_url, pool_pre_ping=True)


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


async def check_database(engine: AsyncEngine, alembic_ini_path: Path) -> dict[str, str]:
    """Vérifie que la base répond et que son schéma est à la dernière migration."""
    try:
        current = await current_migration_revision(engine)
    except (SQLAlchemyError, OSError) as exc:
        return {"status": "error", "detail": f"base injoignable ({type(exc).__name__})"}

    expected = expected_migration_head(alembic_ini_path)
    if current != expected:
        return {
            "status": "error",
            "detail": f"migrations non à jour (base={current}, attendu={expected})",
        }
    return {"status": "ok"}
