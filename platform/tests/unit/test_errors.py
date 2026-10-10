from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from digital360.core.errors import PROBLEM_CONTENT_TYPE, AppError


class _Payload(BaseModel):
    email: str
    quantity: int = Field(gt=0)


@pytest.fixture
def client_with_routes(app: FastAPI) -> Iterator[TestClient]:
    @app.post("/test/validate")
    async def validate(payload: _Payload) -> dict[str, str]:
        return {"email": payload.email}

    @app.get("/test/app-error")
    async def raise_app_error() -> None:
        raise AppError(
            "TRANSITION_GUARD_FAILED",
            "Des éléments obligatoires manquent.",
            status=422,
            errors=[{"field": "checklist", "reason": "missing", "items": ["logo"]}],
        )

    @app.get("/test/crash")
    async def crash() -> None:
        raise RuntimeError("secret technique : mot de passe=hunter2")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_should_return_problem_json_with_not_found_code_for_unknown_route(
    client_with_routes: TestClient,
) -> None:
    response = client_with_routes.get("/api/v1/inexistant")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_should_return_400_validation_error_with_field_details(
    client_with_routes: TestClient,
) -> None:
    response = client_with_routes.post("/test/validate", json={"quantity": 0})

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    fields = {error["field"] for error in body["errors"]}
    assert fields == {"body.email", "body.quantity"}


def test_should_convert_app_error_to_problem_json_with_stable_code(
    client_with_routes: TestClient,
) -> None:
    response = client_with_routes.get("/test/app-error")

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "TRANSITION_GUARD_FAILED"
    assert body["type"].endswith("/transition-guard-failed")
    assert body["errors"][0]["items"] == ["logo"]


def test_should_hide_technical_details_on_unexpected_error(
    client_with_routes: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    response = client_with_routes.get("/test/crash")

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert "hunter2" not in response.text
    assert body["request_id"]
    # Le détail technique doit en revanche être dans les logs, pour le diagnostic
    assert any("hunter2" in (record.exc_text or "") for record in caplog.records)
