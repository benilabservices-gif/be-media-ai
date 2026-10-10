import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from digital360.core.db import create_engine, create_session_factory
from digital360.core.email import EmailMessage
from digital360.core.jobs import Job, Worker
from digital360.core.tenancy import staff_transaction
from digital360.main import create_app
from digital360.modules.configuration.infrastructure.models import ConfigKind
from digital360.modules.diagnostics.application import config_store
from digital360.modules.diagnostics.domain.seeds import (
    SEEDS_DIR,
    load_catalog,
    load_questionnaire,
    load_rule_set,
    load_scoring_model,
)
from tests.conftest import make_settings
from tests.integration.api_client import ApiClient


class RecordingEmailSender:
    """Garde les emails en mémoire au lieu de les envoyer."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


@pytest.fixture
def outbox() -> RecordingEmailSender:
    return RecordingEmailSender()


@pytest.fixture
def db_app(test_database_url: str, outbox: RecordingEmailSender) -> FastAPI:
    return create_app(make_settings(database_url=test_database_url), email_sender=outbox)


def run_pending_jobs(client: TestClient) -> None:
    """Vide la file comme le ferait le worker, dans la boucle d'événements de l'application."""
    app: Any = client.app
    worker = Worker(app.state.session_factory, app.state.job_registry, worker_id="test-worker")

    async def drain() -> None:
        while await worker.run_once():
            pass

    client.portal.call(drain)


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


@pytest.fixture
def published_config(test_database_url: str) -> None:
    """Publie la configuration v1 (idempotent), comme `cli seed-config` en production.

    Synchrone : utilisable par les tests d'API, qui passent par TestClient.
    """

    async def publish_all() -> None:
        engine = create_engine(test_database_url)
        try:
            async with staff_transaction(create_session_factory(engine)) as session:
                await config_store.publish(
                    session,
                    ConfigKind.QUESTIONNAIRE,
                    load_questionnaire(SEEDS_DIR / "questionnaire.v3.yaml"),
                )
                await config_store.publish(
                    session,
                    ConfigKind.SCORING_MODEL,
                    load_scoring_model(SEEDS_DIR / "scoring.v1.yaml"),
                )
                await config_store.publish(
                    session, ConfigKind.RULE_SET, load_rule_set(SEEDS_DIR / "rules.v1.yaml")
                )
                await config_store.publish(
                    session, ConfigKind.CATALOG, load_catalog(SEEDS_DIR / "catalog.v6.yaml")
                )
        finally:
            await engine.dispose()

    asyncio.run(publish_all())
