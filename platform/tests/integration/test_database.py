"""Tests contre un vrai PostgreSQL. La CI exécute `alembic upgrade head` avant pytest."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from digital360.core.db import check_database, create_engine
from digital360.main import create_app
from tests.conftest import make_settings

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path("alembic.ini")


@pytest.mark.anyio
async def test_should_report_database_ok_when_migrations_are_at_head(
    test_database_url: str,
) -> None:
    engine = create_engine(test_database_url)
    try:
        result = await check_database(engine, ALEMBIC_INI)
    finally:
        await engine.dispose()

    assert result == {"status": "ok"}


def test_should_return_200_on_readiness_with_real_database(test_database_url: str) -> None:
    app = create_app(make_settings(database_url=test_database_url))

    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 200, response.json()
