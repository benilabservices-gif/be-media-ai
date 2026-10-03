from fastapi import FastAPI
from fastapi.testclient import TestClient

from digital360.core.health import ReadinessCheck, get_readiness_checks


def _override_checks(app: FastAPI, results: dict[str, dict[str, str]]) -> None:
    def fake_checks() -> dict[str, ReadinessCheck]:
        async def make_result(result: dict[str, str]) -> dict[str, str]:
            return result

        return {name: (lambda r=result: make_result(r)) for name, result in results.items()}

    app.dependency_overrides[get_readiness_checks] = fake_checks


def test_should_return_ok_on_liveness_without_database(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_should_return_200_on_readiness_when_all_checks_pass(
    app: FastAPI, client: TestClient
) -> None:
    _override_checks(app, {"database": {"status": "ok"}})

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": {"status": "ok"}}}


def test_should_return_503_on_readiness_when_a_check_fails(
    app: FastAPI, client: TestClient
) -> None:
    _override_checks(
        app,
        {"database": {"status": "ok"}, "other": {"status": "error", "detail": "indisponible"}},
    )

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "error"


def test_should_return_503_on_readiness_when_database_is_unreachable(client: TestClient) -> None:
    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    database = response.json()["checks"]["database"]
    assert database["status"] == "error"
    assert "injoignable" in database["detail"]
