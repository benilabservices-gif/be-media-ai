"""Le contrat OpenAPI publié pour Kilo doit toujours correspondre au code."""

from pathlib import Path

import pytest

from digital360.cli import OPENAPI_PATH, openapi_document


def test_should_keep_published_openapi_contract_up_to_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # create_app() lit la configuration : une URL factice suffit, rien ne se connecte
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://contract:contract@127.0.0.1:1/x")
    from digital360.core.config import get_settings

    get_settings.cache_clear()
    try:
        current = openapi_document()
    finally:
        get_settings.cache_clear()

    published = Path(OPENAPI_PATH).read_text(encoding="utf-8")
    assert published == current, (
        "contracts/openapi.json est obsolète : lancer `python -m digital360.cli export-openapi` "
        "puis prévenir Kilo des changements (contracts/README.md)."
    )
