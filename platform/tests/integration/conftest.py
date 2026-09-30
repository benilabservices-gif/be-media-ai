from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from digital360.core.db import create_engine, create_session_factory
from digital360.core.jobs import Job
from digital360.core.tenancy import staff_transaction
from digital360.main import create_app
from tests.conftest import make_settings
from tests.integration.api_client import ApiClient


@pytest.fixture
def db_app(test_database_url: str) -> FastAPI:
    return create_app(make_settings(database_url=test_database_url))


@pytest.fixture
def http_client(db_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(db_app) as client:
        yield client


@pytest.fixture
def api(http_client: TestClient) -> ApiClient:
    return ApiClient(http_client)


@pytest.fixture
def second_api(http_client: TestClient) -> ApiClient:
    """Un autre navigateur (cookies séparés) sur la même application."""
    return ApiClient(http_client)


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
