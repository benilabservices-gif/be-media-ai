import json
import logging

import pytest
from fastapi.testclient import TestClient

from digital360.core.logging import JsonFormatter, request_id_var


def test_should_generate_request_id_when_none_is_provided(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")

    request_id = response.headers["X-Request-ID"]
    assert len(request_id) == 32


def test_should_reuse_safe_incoming_request_id(client: TestClient) -> None:
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": "trace-abc.123"})

    assert response.headers["X-Request-ID"] == "trace-abc.123"


@pytest.mark.parametrize("unsafe_id", ["a" * 129, "id avec espaces", "<script>alert(1)</script>"])
def test_should_replace_unsafe_incoming_request_id(client: TestClient, unsafe_id: str) -> None:
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": unsafe_id})

    assert response.headers["X-Request-ID"] != unsafe_id
    assert len(response.headers["X-Request-ID"]) == 32


def test_should_format_log_as_json_with_request_id_and_extra_fields() -> None:
    token = request_id_var.set("req-42")
    try:
        record = logging.makeLogRecord(
            {"name": "digital360.test", "levelname": "INFO", "msg": "paiement %s", "args": ("ok",)}
        )
        record.organization_id = "org-1"

        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)

    assert payload["message"] == "paiement ok"
    assert payload["request_id"] == "req-42"
    assert payload["organization_id"] == "org-1"
    assert payload["level"] == "INFO"
