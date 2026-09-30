import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from digital360.core.config import get_settings
from digital360.core.db import Base, create_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Les modèles des modules seront importés ici au fil de leur création (M1+),
# pour qu'`alembic revision --autogenerate` les voie
target_metadata = Base.metadata


def _database_url() -> str:
    return str(get_settings().database_url)


def run_migrations_offline() -> None:
    """Génère le SQL sans se connecter (`alembic upgrade head --sql`)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
