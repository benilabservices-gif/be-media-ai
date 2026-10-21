import pytest
from pydantic import ValidationError

from digital360.core.db import engine_options
from tests.conftest import make_settings


def test_should_reject_database_url_with_incompatible_driver() -> None:
    with pytest.raises(ValidationError, match=r"postgresql\+asyncpg"):
        make_settings(database_url="postgresql+psycopg://user:pass@localhost/db")


def test_should_accept_standard_postgres_url_as_given_by_neon() -> None:
    settings = make_settings(
        database_url="postgresql://app:secret@ep-x.eu-central-1.aws.neon.tech/neondb?sslmode=require"
    )

    assert settings.database_url.scheme == "postgresql"


def test_should_adapt_neon_url_for_asyncpg() -> None:
    url, connect_args = engine_options(
        "postgresql://app:s3cr%40t@ep-x-pooler.eu-central-1.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )

    assert url == (
        "postgresql+asyncpg://app:s3cr%40t@ep-x-pooler.eu-central-1.aws.neon.tech/neondb"
        "?prepared_statement_cache_size=0"
    )
    assert connect_args["ssl"] == "require"
    assert connect_args["statement_cache_size"] == 0
    first, second = (connect_args["prepared_statement_name_func"]() for _ in range(2))
    assert first != second


def test_should_leave_local_asyncpg_url_untouched() -> None:
    url, connect_args = engine_options("postgresql+asyncpg://u:p@127.0.0.1:5433/db")

    assert (url, connect_args) == ("postgresql+asyncpg://u:p@127.0.0.1:5433/db", {})


def test_should_split_comma_separated_cors_origins() -> None:
    settings = make_settings(cors_allowed_origins="https://a.test, https://b.test,")

    assert settings.cors_allowed_origins == ["https://a.test", "https://b.test"]


def test_should_disable_api_docs_in_production() -> None:
    from fastapi.testclient import TestClient

    from digital360.main import create_app

    with TestClient(create_app(make_settings(app_env="production"))) as client:
        assert client.get("/api/v1/docs").status_code == 404
        assert client.get("/api/v1/openapi.json").status_code == 200
