import os
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from digital360.core.config import AppEnv, Settings
from digital360.main import create_app

# Base volontairement injoignable pour les tests unitaires : aucun test unitaire ne doit
# dépendre d'une vraie base. Les tests d'intégration utilisent TEST_DATABASE_URL.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://test:test@127.0.0.1:1/unreachable"


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": AppEnv.TEST,
        "database_url": UNREACHABLE_DATABASE_URL,
        "cors_allowed_origins": ["https://app.example.test"],
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture
def app() -> FastAPI:
    return create_app(make_settings())


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # raise_server_exceptions=False : on veut observer la réponse 500, pas l'exception
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL non défini : test d'intégration ignoré")
    return url
