"""Paiement en plusieurs fois : acompte, solde, rappels, suspension et réactivation."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from digital360.core.tenancy import staff_transaction
from digital360.main import create_app
from digital360.modules.billing.application.balances import send_balance_reminders
from tests.conftest import make_settings
from tests.integration.api_client import ApiClient
from tests.integration.conftest import RecordingEmailSender, run_pending_jobs
from tests.integration.test_admin_api import _client_with_diagnostic, _staff_member

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("published_config")]

TEAM = "soldes@benilab.test"
# Digital Start : 109 900 FCFA HT
DEPOSIT = {
    "method": "ORANGE_MONEY",
    "amount": 50000,
    "reference": "OM-ACOMPTE-1",
    "installment": True,
}
BALANCE = 59900


@pytest.fixture
def db_app(test_database_url: str, outbox: RecordingEmailSender) -> FastAPI:
    settings = make_settings(database_url=test_database_url, sales_alert_emails=[TEAM])
    return create_app(settings, email_sender=outbox)


@pytest.fixture
def http_client(db_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(db_app) as client:
        yield client


def _sale(staff: ApiClient, org_id: str, payment: dict[str, Any]) -> Any:
    return staff.post(
        f"/admin/organizations/{org_id}/sales",
        {"product_code": "DIGITAL_START", "payment": payment},
    )


def _deposit_sale(
    api: ApiClient, staff: ApiClient, database_url: str, role: str = "MANAGER"
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Client qui a versé un acompte de 50 000 FCFA sur Start (109 900)."""
    org = _client_with_diagnostic(api)
    _staff_member(staff, database_url, role)
    response = _sale(staff, org["id"], DEPOSIT)
    assert response.status_code == 201, response.json()
    return org, response.json()


def _pay(staff: ApiClient, sale_id: str, amount: int, reference: str = "OM-SOLDE-1") -> Any:
    return staff.post(
        f"/admin/purchase-requests/{sale_id}/payments",
        {"method": "ORANGE_MONEY", "amount": amount, "reference": reference},
    )


def _entitlement(api: ApiClient, org_id: str, key: str) -> Any:
    body = api.get(f"/orgs/{org_id}/entitlements").json()
    return next(item["value"] for item in body["entitlements"] if item["key"] == key)


def _run_reminders(http_client: TestClient, outbox: RecordingEmailSender, on: date) -> int:
    """Contrôle quotidien des soldes, comme s'il tournait le jour `on`."""
    app: Any = http_client.app
    moment = datetime.combine(on, datetime.min.time(), tzinfo=UTC) + timedelta(hours=7)

    async def run() -> int:
        async with staff_transaction(app.state.session_factory) as session:
            return await send_balance_reminders(
                session,
                outbox,
                team=[TEAM],
                app_url="https://app.test/",
                admin_url="https://app.test/admin.html",
                now=moment,
            )

    sent: int = http_client.portal.call(run)
    return sent


def _sent_to(outbox: RecordingEmailSender, email: str) -> list[str]:
    return [m.subject for m in outbox.sent if email in m.to]


# ── Acompte ──


def test_should_activate_offer_on_deposit_and_show_balance_due(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org, sale = _deposit_sale(api, second_api, test_database_url)

    assert _entitlement(api, org["id"], "WEBSITE") is True
    assert (sale["paid_amount"], sale["balance_amount"]) == (50000, BALANCE)
    assert sale["balance_due_on"] == (date.today() + timedelta(days=30)).isoformat()
    assert sale["suspended"] is False
    [balance] = api.get(f"/orgs/{org['id']}/billing").json()["balances"]
    assert (balance["total"], balance["paid"], balance["remaining"]) == (109900, 50000, BALANCE)
    assert balance["product_name"] == "Start"
    [request] = api.get(f"/orgs/{org['id']}/purchase-requests").json()["data"]
    assert request["balance_amount"] == BALANCE


def test_should_treat_lower_amount_without_deposit_as_discount(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    _staff_member(second_api, test_database_url, "MANAGER")
    discounted = {"method": "WAVE", "amount": 90000, "reference": "WV-REMISE"}

    sale = _sale(second_api, org["id"], discounted).json()

    assert (sale["balance_amount"], sale["balance_due_on"]) == (0, None)
    assert api.get(f"/orgs/{org['id']}/billing").json()["balances"] == []
    refused = _pay(second_api, sale["id"], 1000)
    assert refused.status_code == 409
    assert refused.json()["code"] == "NO_BALANCE_DUE"


@pytest.mark.parametrize("amount", [None, 0, 109900, 150000])
def test_should_refuse_deposit_outside_zero_and_price(
    api: ApiClient, second_api: ApiClient, test_database_url: str, amount: int | None
) -> None:
    org = _client_with_diagnostic(api)
    _staff_member(second_api, test_database_url, "MANAGER")

    response = _sale(second_api, org["id"], {**DEPOSIT, "amount": amount})

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_DEPOSIT"
    assert _entitlement(api, org["id"], "WEBSITE") is False


# ── Solde ──


def test_should_settle_balance_in_two_payments_with_a_receipt_each(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    outbox: RecordingEmailSender,
    test_database_url: str,
) -> None:
    org, sale = _deposit_sale(api, second_api, test_database_url)
    run_pending_jobs(http_client)
    outbox.sent.clear()

    partial = _pay(second_api, sale["id"], 30000, "OM-SOLDE-A").json()
    final = _pay(second_api, sale["id"], BALANCE - 30000, "OM-SOLDE-B").json()
    run_pending_jobs(http_client)

    assert partial["balance_amount"] == BALANCE - 30000
    assert (final["paid_amount"], final["balance_amount"]) == (109900, 0)
    assert final["balance_due_on"] is None
    assert [p["reference"] for p in final["payments"]] == [
        "OM-ACOMPTE-1",
        "OM-SOLDE-A",
        "OM-SOLDE-B",
    ]
    assert api.get(f"/orgs/{org['id']}/billing").json()["balances"] == []
    receipts = [m for m in outbox.sent if m.subject.startswith("Reçu de paiement")]
    assert len(receipts) == 2
    assert "29 900 FCFA HT" in receipts[-1].text


def test_should_refuse_payment_above_remaining_balance(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    _, sale = _deposit_sale(api, second_api, test_database_url)

    response = _pay(second_api, sale["id"], BALANCE + 1)

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_AMOUNT"
    assert "59 900 FCFA HT" in response.json()["detail"]


def test_should_reserve_balance_payment_to_sales_team(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    _, sale = _deposit_sale(api, second_api, test_database_url)

    assert _pay(api, sale["id"], 1000).status_code == 403


# ── Rappels et suspension ──


def test_should_remind_a_week_before_on_due_date_and_once_per_step(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    outbox: RecordingEmailSender,
    test_database_url: str,
) -> None:
    org, sale = _deposit_sale(api, second_api, test_database_url)
    owner = api.get("/me").json()["user"]["email"]
    due = date.fromisoformat(sale["balance_due_on"])
    outbox.sent.clear()

    _run_reminders(http_client, outbox, due - timedelta(days=8))
    assert _sent_to(outbox, owner) == []

    _run_reminders(http_client, outbox, due - timedelta(days=7))
    _run_reminders(http_client, outbox, due - timedelta(days=6))
    _run_reminders(http_client, outbox, due)
    _run_reminders(http_client, outbox, due + timedelta(days=1))

    assert _sent_to(outbox, owner) == [
        f"Solde de votre offre Start : échéance le {due.strftime('%d/%m/%Y')}",
        "Le solde de votre offre Start est dû aujourd'hui",
    ]
    assert any(TEAM in m.to and "solde(s) à encaisser" in m.subject for m in outbox.sent)
    assert _entitlement(api, org["id"], "WEBSITE") is True


def test_should_suspend_a_week_after_due_date_and_restore_once_paid(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    outbox: RecordingEmailSender,
    test_database_url: str,
) -> None:
    org, sale = _deposit_sale(api, second_api, test_database_url)
    owner = api.get("/me").json()["user"]["email"]
    due = date.fromisoformat(sale["balance_due_on"])
    outbox.sent.clear()

    _run_reminders(http_client, outbox, due + timedelta(days=7))

    assert _sent_to(outbox, owner) == ["Vos services Start sont suspendus"]
    assert _entitlement(api, org["id"], "WEBSITE") is False
    [balance] = api.get(f"/orgs/{org['id']}/billing").json()["balances"]
    assert balance["suspended"] is True
    # Un acompte partiel du solde ne suffit pas : tout doit être payé
    still = _pay(second_api, sale["id"], 10000, "OM-SOLDE-PARTIEL").json()
    assert still["suspended"] is True
    assert _entitlement(api, org["id"], "WEBSITE") is False

    paid = _pay(second_api, sale["id"], BALANCE - 10000, "OM-SOLDE-FIN").json()

    assert (paid["suspended"], paid["balance_amount"]) == (False, 0)
    assert _entitlement(api, org["id"], "WEBSITE") is True
    # Plus de solde : plus de rappel
    outbox.sent.clear()
    assert _run_reminders(http_client, outbox, due + timedelta(days=8)) == 0


# ── Garde-fous ──


def test_should_refuse_paying_a_renewal_in_installments(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    _staff_member(second_api, test_database_url, "MANAGER")

    response = second_api.post(
        f"/admin/organizations/{org['id']}/renewals",
        {"product_code": "DIGITAL_ESSENTIAL", "payment": {**DEPOSIT, "amount": 1000}},
    )

    assert response.status_code == 400


def test_should_not_void_a_sale_paid_in_installments_as_a_duplicate(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    _, sale = _deposit_sale(api, second_api, test_database_url, role="ADMIN")
    _pay(second_api, sale["id"], 10000)

    response = second_api.post(
        f"/admin/purchase-requests/{sale['id']}/void-duplicate", {"reason": "Essai"}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "MULTIPLE_PAYMENTS"


def test_should_show_balances_due_to_team_on_admin_dashboard(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    _deposit_sale(api, second_api, test_database_url)

    balances = second_api.get("/admin/dashboard").json()["sales"]["balances_due"]

    xof = next(item for item in balances if item["currency"] == "XOF")
    assert xof["count"] >= 1
    assert xof["total"] >= BALANCE
