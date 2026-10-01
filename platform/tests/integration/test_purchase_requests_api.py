"""« Je veux démarrer » : demande d'achat du client, traitement par l'équipe, alerte e-mail."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from digital360.main import create_app
from tests.conftest import make_settings
from tests.integration.api_client import ApiClient
from tests.integration.conftest import RecordingEmailSender, run_pending_jobs
from tests.integration.test_admin_api import _client_with_diagnostic, _staff_member

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("published_config")]

SALES = "ventes@benilab.test"


@pytest.fixture
def db_app(test_database_url: str, outbox: RecordingEmailSender) -> FastAPI:
    settings = make_settings(database_url=test_database_url, sales_alert_emails=[SALES])
    return create_app(settings, email_sender=outbox)


@pytest.fixture
def http_client(db_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(db_app) as client:
        yield client


def _website_item(api: ApiClient, org_id: str) -> dict[str, Any]:
    items = api.get(f"/orgs/{org_id}/action-plan").json()["items"]
    return next(item for item in items if item["product_code"] == "DIGITAL_START")


def _request(api: ApiClient, org_id: str, **body: Any) -> Any:
    return api.post(
        f"/orgs/{org_id}/purchase-requests",
        {"product_code": "DIGITAL_START", "channel": "WHATSAPP", **body},
    )


# ── Côté client ──


def test_should_create_request_from_recommendation_and_alert_sales(
    api: ApiClient, http_client: TestClient, outbox: RecordingEmailSender
) -> None:
    org = _client_with_diagnostic(api)
    item = _website_item(api, org["id"])

    response = _request(api, org["id"], plan_item_id=item["id"], message="Rappelez-moi le matin")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "NEW"
    assert body["product_name"]
    assert body["price"] == {"amount": 89900, "currency": "XOF", "period": "NONE"}
    assert "staff_note" not in body  # réservé à l'équipe
    assert _website_item(api, org["id"])["status"] == "ACCEPTED"

    run_pending_jobs(http_client)
    [alert] = [
        m
        for m in outbox.sent
        if m.subject.startswith("Demande d'achat") and org["commercial_name"] in m.subject
    ]
    assert alert.to == [SALES]
    assert "89 900 FCFA HT" in alert.text
    assert "Rappelez-moi le matin" in alert.text


def test_should_return_open_request_instead_of_duplicating(api: ApiClient) -> None:
    org = _client_with_diagnostic(api)
    first = _request(api, org["id"])

    again = _request(api, org["id"], channel="PHONE")

    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert len(api.get(f"/orgs/{org['id']}/purchase-requests").json()["data"]) == 1


def test_should_price_request_in_currency_of_organization_country(api: ApiClient) -> None:
    api.register()
    org = api.post("/orgs", {"commercial_name": "Boulangerie Lyon", "country": "FR"}).json()

    body = _request(api, org["id"]).json()

    assert body["price"]["currency"] == "EUR"
    assert body["price"]["amount"] > 0


@pytest.mark.parametrize("product_code", ["PRODUIT_INCONNU", "ANNUAL_RENEWAL"])
def test_should_refuse_unknown_or_internal_product(api: ApiClient, product_code: str) -> None:
    org = _client_with_diagnostic(api)

    response = _request(api, org["id"], product_code=product_code)

    assert response.status_code == 422
    assert response.json()["code"] == "PRODUCT_NOT_AVAILABLE"


def test_should_refuse_recommendation_for_another_product(api: ApiClient) -> None:
    org = _client_with_diagnostic(api)
    item = _website_item(api, org["id"])

    response = _request(api, org["id"], product_code="DIGITAL_GROWTH", plan_item_id=item["id"])

    assert response.status_code == 422


def test_should_not_accept_recommendation_of_another_organization(
    api: ApiClient, second_api: ApiClient
) -> None:
    other_item = _website_item(second_api, _client_with_diagnostic(second_api)["id"])
    org = _client_with_diagnostic(api)

    response = _request(api, org["id"], plan_item_id=other_item["id"])

    assert response.status_code == 404


def test_should_hide_requests_from_non_members(api: ApiClient, second_api: ApiClient) -> None:
    org = _client_with_diagnostic(api)
    _request(api, org["id"])
    second_api.register()

    assert second_api.get(f"/orgs/{org['id']}/purchase-requests").status_code == 404
    assert _request(second_api, org["id"]).status_code == 404


def test_should_require_csrf_to_create_request(api: ApiClient) -> None:
    org = _client_with_diagnostic(api)

    response = api.post_without_csrf(
        f"/orgs/{org['id']}/purchase-requests",
        {"product_code": "DIGITAL_START", "channel": "EMAIL"},
    )

    assert response.status_code == 403


# ── Côté équipe ──


def test_should_let_sales_team_process_request_until_won(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    item = _website_item(api, org["id"])
    request_id = _request(api, org["id"], plan_item_id=item["id"]).json()["id"]
    _staff_member(second_api, test_database_url, "MANAGER")

    listed = second_api.get("/admin/purchase-requests?status=NEW&limit=100").json()["data"]
    mine = next(entry for entry in listed if entry["id"] == request_id)
    assert mine["organization_name"] == org["commercial_name"]
    assert mine["requested_by_email"]

    contacted = second_api.patch(
        f"/admin/purchase-requests/{request_id}",
        {"status": "CONTACTED", "staff_note": "Appelé, paiement Orange Money demain"},
    )
    won = second_api.patch(f"/admin/purchase-requests/{request_id}", {"status": "WON"})

    assert contacted.json()["staff_note"] == "Appelé, paiement Orange Money demain"
    assert won.status_code == 200
    assert won.json()["status"] == "WON"
    assert _website_item(api, org["id"])["status"] == "IN_PROGRESS"
    # Une fois la demande close, une nouvelle demande pour la même offre est possible
    assert _request(api, org["id"]).status_code == 201


def test_should_put_recommendation_back_when_request_is_lost(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    item = _website_item(api, org["id"])
    request_id = _request(api, org["id"], plan_item_id=item["id"]).json()["id"]
    _staff_member(second_api, test_database_url, "MANAGER")

    second_api.patch(f"/admin/purchase-requests/{request_id}", {"status": "LOST"})

    assert _website_item(api, org["id"])["status"] == "PROPOSED"


def test_should_refuse_reopening_a_closed_request(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    request_id = _request(api, org["id"]).json()["id"]
    _staff_member(second_api, test_database_url, "MANAGER")
    second_api.patch(f"/admin/purchase-requests/{request_id}", {"status": "WON"})

    response = second_api.patch(f"/admin/purchase-requests/{request_id}", {"status": "LOST"})

    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_TRANSITION"


def test_should_reserve_request_processing_to_sales_roles(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    request_id = _request(api, org["id"]).json()["id"]
    _staff_member(second_api, test_database_url, "FINANCE")

    assert api.get("/admin/purchase-requests").status_code == 403
    assert second_api.get("/admin/purchase-requests").status_code == 403
    response = second_api.patch(f"/admin/purchase-requests/{request_id}", {"status": "WON"})
    assert response.status_code == 403
