from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from digital360.core.db import create_engine, create_session_factory
from digital360.core.jobs import Job
from digital360.core.tenancy import staff_transaction


@pytest.fixture
async def engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(test_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
async def empty_job_queue(session_factory: async_sessionmaker[AsyncSession]) -> None:
    # Le worker prend n'importe quelle tâche en attente : on part d'une file vide
    async with staff_transaction(session_factory) as session:
        await session.execute(delete(Job))
