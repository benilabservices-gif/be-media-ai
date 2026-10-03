"""« Je veux démarrer » : demande d'achat du client, traitement par l'équipe, alerte e-mail."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
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

# Encaissement manuel : obligatoire pour passer une demande à « Gagnée »
PAYMENT = {"method": "ORANGE_MONEY", "amount": 109900, "reference": "OM-TEST-0001"}
WON_WITH_PAYMENT = {"status": "WON", "payment": PAYMENT}

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
    run_pending_jobs(http_client)  # file vidée : seules les alertes de ce test restent
    outbox.sent.clear()

    response = _request(api, org["id"], plan_item_id=item["id"], message="Rappelez-moi le matin")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "NEW"
    assert body["product_name"]
    assert body["price"] == {"amount": 109900, "currency": "XOF", "period": "NONE"}
    assert "staff_note" not in body  # réservé à l'équipe
    assert _website_item(api, org["id"])["status"] == "ACCEPTED"

    run_pending_jobs(http_client)
    [alert] = [m for m in outbox.sent if m.subject.startswith("Demande d'achat")]
    assert alert.to == [SALES]
    assert "109 900 FCFA HT" in alert.text
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

    body = _request(api, org["id"], channel="EMAIL").json()

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
    won = second_api.patch(f"/admin/purchase-requests/{request_id}", WON_WITH_PAYMENT)

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
    second_api.patch(f"/admin/purchase-requests/{request_id}", WON_WITH_PAYMENT)

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
    response = second_api.patch(f"/admin/purchase-requests/{request_id}", WON_WITH_PAYMENT)
    assert response.status_code == 403


# ── Activation de l'offre quand la vente est gagnée ──


def _entitlements(api: ApiClient, org_id: str) -> dict[str, Any]:
    body = api.get(f"/orgs/{org_id}/entitlements").json()
    return {item["key"]: item for item in body["entitlements"]}


def test_should_activate_one_time_offer_without_expiry_when_request_is_won(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    assert _entitlements(api, org["id"])["WEBSITE"]["value"] is False
    request_id = _request(api, org["id"]).json()["id"]
    _staff_member(second_api, test_database_url, "MANAGER")

    second_api.patch(f"/admin/purchase-requests/{request_id}", WON_WITH_PAYMENT)

    website = _entitlements(api, org["id"])["WEBSITE"]
    assert website["value"] is True
    [source] = website["sources"]
    assert request_id in source["reason"]
    assert source["expires_at"] is None


def test_should_activate_subscription_for_one_month_when_request_is_won(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    request_id = _request(api, org["id"], product_code="DIGITAL_ESSENTIAL").json()["id"]
    _staff_member(second_api, test_database_url, "MANAGER")

    second_api.patch(f"/admin/purchase-requests/{request_id}", WON_WITH_PAYMENT)

    granted = _entitlements(api, org["id"])
    assert granted["MAINTENANCE"]["value"] is True
    assert granted["CONTENT_MONTHLY_LIMIT"]["value"] == 10
    expires_at = datetime.fromisoformat(granted["MAINTENANCE"]["sources"][0]["expires_at"])
    assert timedelta(days=30) < expires_at - datetime.now(UTC) <= timedelta(days=31)


def test_should_not_activate_anything_when_request_is_lost(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    request_id = _request(api, org["id"]).json()["id"]
    _staff_member(second_api, test_database_url, "MANAGER")

    second_api.patch(f"/admin/purchase-requests/{request_id}", {"status": "LOST"})

    assert _entitlements(api, org["id"])["WEBSITE"]["value"] is False


def test_should_record_offer_activation_in_audit_log(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    request_id = _request(api, org["id"]).json()["id"]
    _staff_member(second_api, test_database_url, "ADMIN")
    second_api.patch(f"/admin/purchase-requests/{request_id}", WON_WITH_PAYMENT)

    entries = second_api.get(
        f"/admin/audit-logs?organization_id={org['id']}&action=purchase_request.activate_offer"
    ).json()["data"]

    [entry] = entries
    assert entry["new_value"]["entitlements"] == {"WEBSITE": True}


# ── Numéro à rappeler ──


def test_should_use_known_whatsapp_number_of_organization(api: ApiClient) -> None:
    org = _client_with_diagnostic(api)  # le diagnostic a renseigné le WhatsApp de l'entreprise

    body = _request(api, org["id"], channel="WHATSAPP").json()

    assert body["contact_number"] == "+2250500000000"


def test_should_prefer_number_given_in_request(api: ApiClient) -> None:
    org = _client_with_diagnostic(api)

    body = _request(api, org["id"], channel="PHONE", contact_number="+2250102030405").json()

    assert body["contact_number"] == "+2250102030405"


def test_should_require_a_number_when_none_is_known(api: ApiClient) -> None:
    api.register()
    org = api.create_organization()  # ni téléphone ni WhatsApp

    response = _request(api, org["id"], channel="WHATSAPP")

    assert response.status_code == 422
    assert response.json()["code"] == "CONTACT_NUMBER_REQUIRED"
    assert _request(api, org["id"], channel="EMAIL").status_code == 201


def test_should_refuse_malformed_contact_number(api: ApiClient) -> None:
    org = _client_with_diagnostic(api)

    response = _request(api, org["id"], channel="PHONE", contact_number="07 00 00 00")

    assert response.status_code == 400


def test_should_show_contact_number_to_sales_team_and_in_alert(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    outbox: RecordingEmailSender,
    test_database_url: str,
) -> None:
    org = _client_with_diagnostic(api)
    run_pending_jobs(http_client)  # file vidée : seules les alertes de ce test restent
    outbox.sent.clear()
    request_id = _request(api, org["id"], channel="WHATSAPP").json()["id"]
    _staff_member(second_api, test_database_url, "MANAGER")

    listed = second_api.get("/admin/purchase-requests?limit=100").json()["data"]
    run_pending_jobs(http_client)

    assert next(r for r in listed if r["id"] == request_id)["contact_number"] == "+2250500000000"
    [alert] = [m for m in outbox.sent if m.subject.startswith("Demande d'achat")]
    assert "Numéro à contacter : +2250500000000" in alert.text


# ── Une offre couvre toutes ses recommandations ──


def _statuses_for(api: ApiClient, org_id: str, product_code: str) -> set[str]:
    items = api.get(f"/orgs/{org_id}/action-plan").json()["items"]
    return {item["status"] for item in items if item["product_code"] == product_code}


def test_should_move_every_recommendation_of_the_offer_together(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    growth = [
        item
        for item in api.get(f"/orgs/{org['id']}/action-plan").json()["items"]
        if item["product_code"] == "DIGITAL_GROWTH"
    ]
    assert len(growth) >= 2  # sinon le test ne prouve rien
    request_id = _request(
        api, org["id"], product_code="DIGITAL_GROWTH", plan_item_id=growth[0]["id"]
    ).json()["id"]

    assert _statuses_for(api, org["id"], "DIGITAL_GROWTH") == {"ACCEPTED"}
    assert "PROPOSED" in _statuses_for(api, org["id"], "DIGITAL_START")  # autre offre intacte

    _staff_member(second_api, test_database_url, "MANAGER")
    second_api.patch(f"/admin/purchase-requests/{request_id}", WON_WITH_PAYMENT)

    assert _statuses_for(api, org["id"], "DIGITAL_GROWTH") == {"IN_PROGRESS"}


# ── Indicateurs de vente et statut client ──


def _sales(api: ApiClient) -> dict[str, Any]:
    body: dict[str, Any] = api.get("/admin/dashboard").json()["sales"]
    return body


def _xof_revenue(sales: dict[str, Any]) -> dict[str, int]:
    return next(
        (entry for entry in sales["revenue"] if entry["currency"] == "XOF"),
        {"total": 0, "last_30_days": 0},
    )


def test_should_count_requests_and_won_revenue_in_dashboard(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    _staff_member(second_api, test_database_url, "MANAGER")
    before = _sales(second_api)
    org = _client_with_diagnostic(api)
    won_id = _request(api, org["id"]).json()["id"]  # Start : 109 900 FCFA HT
    lost_id = _request(api, org["id"], product_code="DIGITAL_GROWTH").json()["id"]

    middle = _sales(second_api)
    second_api.patch(f"/admin/purchase-requests/{won_id}", WON_WITH_PAYMENT)
    second_api.patch(f"/admin/purchase-requests/{lost_id}", {"status": "LOST"})
    after = _sales(second_api)

    assert middle["new"] == before["new"] + 2
    assert after["new"] == before["new"]
    assert after["won"] == before["won"] + 1
    assert after["lost"] == before["lost"] + 1
    assert after["won_last_30_days"] == before["won_last_30_days"] + 1
    assert _xof_revenue(after)["total"] == _xof_revenue(before)["total"] + 109900
    assert _xof_revenue(after)["last_30_days"] == _xof_revenue(before)["last_30_days"] + 109900
    assert 0 < after["win_rate"] < 1


def test_should_turn_lead_into_active_client_on_first_won_request(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    assert api.get(f"/orgs/{org['id']}").json()["status"] == "LEAD"
    request_id = _request(api, org["id"]).json()["id"]
    _staff_member(second_api, test_database_url, "MANAGER")

    second_api.patch(f"/admin/purchase-requests/{request_id}", WON_WITH_PAYMENT)

    assert api.get(f"/orgs/{org['id']}").json()["status"] == "ACTIVE"


def test_should_keep_lead_status_when_request_is_lost(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    request_id = _request(api, org["id"]).json()["id"]
    _staff_member(second_api, test_database_url, "MANAGER")

    second_api.patch(f"/admin/purchase-requests/{request_id}", {"status": "LOST"})

    assert api.get(f"/orgs/{org['id']}").json()["status"] == "LEAD"
