import pytest
from pydantic import ValidationError

from tests.conftest import make_settings


def test_should_reject_database_url_without_asyncpg_driver() -> None:
    with pytest.raises(ValidationError, match=r"postgresql\+asyncpg"):
        make_settings(database_url="postgresql://user:pass@localhost/db")


def test_should_split_comma_separated_cors_origins() -> None:
    settings = make_settings(cors_allowed_origins="https://a.test, https://b.test,")

    assert settings.cors_allowed_origins == ["https://a.test", "https://b.test"]


def test_should_disable_api_docs_in_production() -> None:
    from fastapi.testclient import TestClient

    from digital360.main import create_app

    with TestClient(create_app(make_settings(app_env="production"))) as client:
        assert client.get("/api/v1/docs").status_code == 404
        assert client.get("/api/v1/openapi.json").status_code == 200
