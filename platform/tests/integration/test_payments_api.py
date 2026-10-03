"""Paiement manuel : obligatoire pour gagner une vente, tracé, reçu au client, vente directe."""

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

ORANGE = {"method": "ORANGE_MONEY", "amount": 109900, "reference": "OM-250930-7781"}


@pytest.fixture
def db_app(test_database_url: str, outbox: RecordingEmailSender) -> FastAPI:
    settings = make_settings(database_url=test_database_url, sales_alert_emails=["v@b.test"])
    return create_app(settings, email_sender=outbox)


@pytest.fixture
def http_client(db_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(db_app) as client:
        yield client


def _request_start(api: ApiClient) -> tuple[dict[str, Any], str]:
    org = _client_with_diagnostic(api)
    request_id = api.post(
        f"/orgs/{org['id']}/purchase-requests",
        {"product_code": "DIGITAL_START", "channel": "EMAIL"},
    ).json()["id"]
    return org, request_id


def _entitlement(api: ApiClient, org_id: str, key: str) -> Any:
    body = api.get(f"/orgs/{org_id}/entitlements").json()
    return next(item["value"] for item in body["entitlements"] if item["key"] == key)


# ── « Gagnée » exige le paiement ──


def test_should_refuse_won_without_payment_and_keep_request_open(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org, request_id = _request_start(api)
    _staff_member(second_api, test_database_url, "MANAGER")

    response = second_api.patch(f"/admin/purchase-requests/{request_id}", {"status": "WON"})

    assert response.status_code == 422
    assert response.json()["code"] == "PAYMENT_REQUIRED"
    assert _entitlement(api, org["id"], "WEBSITE") is False


def test_should_require_reference_except_for_cash(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    _, request_id = _request_start(api)
    _staff_member(second_api, test_database_url, "MANAGER")
    url = f"/admin/purchase-requests/{request_id}"
    no_reference = {"method": "WAVE", "amount": 109900}

    refused = second_api.patch(url, {"status": "WON", "payment": no_reference})
    cash = second_api.patch(url, {"status": "WON", "payment": {"method": "CASH", "amount": 109900}})

    assert refused.status_code == 400
    assert cash.status_code == 200


def test_should_record_payment_and_show_it_to_sales_team(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org, request_id = _request_start(api)
    _staff_member(second_api, test_database_url, "MANAGER")

    won = second_api.patch(
        f"/admin/purchase-requests/{request_id}", {"status": "WON", "payment": ORANGE}
    ).json()

    assert won["status"] == "WON"
    assert won["payment"]["method"] == "ORANGE_MONEY"
    assert won["payment"]["reference"] == "OM-250930-7781"
    assert won["payment"]["amount"] == 109900
    assert won["payment"]["channel"] == "MANUAL"
    assert _entitlement(api, org["id"], "WEBSITE") is True


def test_should_email_receipt_with_payment_details_to_client(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    outbox: RecordingEmailSender,
    test_database_url: str,
) -> None:
    _, request_id = _request_start(api)
    _staff_member(second_api, test_database_url, "MANAGER")
    run_pending_jobs(http_client)
    outbox.sent.clear()

    second_api.patch(f"/admin/purchase-requests/{request_id}", {"status": "WON", "payment": ORANGE})
    run_pending_jobs(http_client)

    [receipt] = [m for m in outbox.sent if m.subject.startswith("Reçu de paiement REC-")]
    assert receipt.to == [api.get("/me").json()["user"]["email"]]
    assert "109 900 FCFA HT" in receipt.text
    assert "Orange Money" in receipt.text
    assert "OM-250930-7781" in receipt.text
    assert "ne remplace pas une facture" in receipt.text


def test_should_count_amount_actually_received_in_revenue(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    _staff_member(second_api, test_database_url, "MANAGER")

    def xof_total() -> int:
        revenue = second_api.get("/admin/dashboard").json()["sales"]["revenue"]
        return next((r["total"] for r in revenue if r["currency"] == "XOF"), 0)

    before = xof_total()
    _, request_id = _request_start(api)
    discounted = {**ORANGE, "amount": 100000}  # geste commercial
    second_api.patch(
        f"/admin/purchase-requests/{request_id}", {"status": "WON", "payment": discounted}
    )

    assert xof_total() == before + 100000


# ── Vente directe (le client n'a pas fait de demande) ──


def test_should_record_direct_sale_and_activate_everything(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    outbox: RecordingEmailSender,
    test_database_url: str,
) -> None:
    org = _client_with_diagnostic(api)
    _staff_member(second_api, test_database_url, "MANAGER")
    run_pending_jobs(http_client)
    outbox.sent.clear()

    response = second_api.post(
        f"/admin/organizations/{org['id']}/sales",
        {"product_code": "DIGITAL_START", "payment": ORANGE, "note": "Payé en agence"},
    )
    run_pending_jobs(http_client)

    assert response.status_code == 201
    sale = response.json()
    assert sale["status"] == "WON"
    assert sale["payment"]["reference"] == "OM-250930-7781"
    assert sale["staff_note"] == "Payé en agence"
    assert _entitlement(api, org["id"], "WEBSITE") is True
    assert api.get(f"/orgs/{org['id']}").json()["status"] == "ACTIVE"
    assert api.get(f"/orgs/{org['id']}/website-project").json()["status"] == "BRIEF_PENDING"
    subjects = [m.subject for m in outbox.sent]
    assert any(s.startswith("Reçu de paiement") for s in subjects)
    assert any(s.startswith("Votre offre Start est activée") for s in subjects)


def test_should_send_team_to_existing_request_instead_of_duplicating_sale(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org, _ = _request_start(api)
    _staff_member(second_api, test_database_url, "MANAGER")

    response = second_api.post(
        f"/admin/organizations/{org['id']}/sales",
        {"product_code": "DIGITAL_START", "payment": ORANGE},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "OPEN_REQUEST_EXISTS"


def test_should_reserve_direct_sale_to_sales_team(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    _staff_member(second_api, test_database_url, "FINANCE")
    body = {"product_code": "DIGITAL_START", "payment": ORANGE}

    assert api.post(f"/admin/organizations/{org['id']}/sales", body).status_code == 403
    assert second_api.post(f"/admin/organizations/{org['id']}/sales", body).status_code == 403


def test_should_answer_404_for_direct_sale_to_unknown_organization(
    second_api: ApiClient, test_database_url: str
) -> None:
    _staff_member(second_api, test_database_url, "MANAGER")

    response = second_api.post(
        "/admin/organizations/00000000-0000-7000-8000-000000000000/sales",
        {"product_code": "DIGITAL_START", "payment": ORANGE},
    )

    assert response.status_code == 404


def test_should_record_offer_price_when_amount_is_left_empty(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    _staff_member(second_api, test_database_url, "MANAGER")

    sale = second_api.post(
        f"/admin/organizations/{org['id']}/sales",
        {"product_code": "DIGITAL_START", "payment": {"method": "CASH"}},
    ).json()

    assert sale["payment"]["amount"] == 109900
    assert sale["payment"]["currency"] == "XOF"
