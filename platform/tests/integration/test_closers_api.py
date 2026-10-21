"""Closer 3.0 : inscription, parrainage, 20 % sur 12 mois, relevé mensuel et versement."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from digital360.core.jobs import enqueue
from digital360.core.tenancy import staff_transaction
from digital360.main import create_app
from digital360.modules.referrals.application.service import MONTHLY_STATEMENTS_JOB
from tests.conftest import make_settings
from tests.integration.api_client import ApiClient
from tests.integration.conftest import RecordingEmailSender, run_pending_jobs
from tests.integration.test_admin_api import _client_with_diagnostic, _staff_member

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("published_config")]

PAYOUT = {"payout_method": "MTN_MOMO", "payout_account": "+22997000000"}
JOIN = {**PAYOUT, "accept_terms": True}


@pytest.fixture
def db_app(test_database_url: str, outbox: RecordingEmailSender) -> FastAPI:
    settings = make_settings(database_url=test_database_url, app_url="https://digital360.test/")
    return create_app(settings, email_sender=outbox)


@pytest.fixture
def http_client(db_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(db_app) as client:
        yield client


@pytest.fixture
def staff(http_client: TestClient, test_database_url: str) -> ApiClient:
    member = ApiClient(http_client)
    _staff_member(member, test_database_url, "MANAGER")
    return member


def _sell(
    staff: ApiClient, org_id: str, product: str = "DIGITAL_START", received_on: date | None = None
) -> None:
    payment: dict[str, Any] = {"method": "WAVE", "reference": f"WV-{org_id[-6:]}-{product}"}
    if received_on is not None:
        payment["received_on"] = received_on.isoformat()
    response = staff.post(
        f"/admin/organizations/{org_id}/sales", {"product_code": product, "payment": payment}
    )
    assert response.status_code == 201, response.json()


def _closer(api: ApiClient, staff: ApiClient) -> dict[str, Any]:
    """Client qui a payé Start, devenu closer."""
    org = _client_with_diagnostic(api)
    _sell(staff, org["id"])
    response = api.post("/me/closer", JOIN)
    assert response.status_code == 200, response.json()
    closer: dict[str, Any] = response.json()["closer"]
    return closer


def _referred_client(http_client: TestClient, code: str | None) -> tuple[ApiClient, dict[str, Any]]:
    client = ApiClient(http_client)
    client.register()
    body: dict[str, Any] = {"commercial_name": "Maquis Chez Tanti", "country": "CI"}
    if code is not None:
        body["referral_code"] = code
    response = client.post("/orgs", body)
    assert response.status_code == 201, response.json()
    return client, response.json()


def _space(api: ApiClient) -> dict[str, Any]:
    body: dict[str, Any] = api.get("/me/closer").json()
    return body


# ── Devenir closer ──


def test_should_refuse_closer_program_to_client_who_never_paid(api: ApiClient) -> None:
    _client_with_diagnostic(api)

    space = _space(api)
    response = api.post("/me/closer", JOIN)

    assert space["eligible"] is False and space["closer"] is None
    assert response.status_code == 403
    assert response.json()["code"] == "NOT_ELIGIBLE"


def test_should_give_paying_client_a_code_and_referral_link(
    api: ApiClient, staff: ApiClient
) -> None:
    closer = _closer(api, staff)

    again = api.post("/me/closer", JOIN).json()["closer"]

    assert len(closer["code"]) == 6
    assert closer["referral_link"] == f"https://digital360.test/?ref={closer['code']}"
    assert closer["status"] == "ACTIVE"
    assert again["code"] == closer["code"]


def test_should_require_terms_acceptance(api: ApiClient, staff: ApiClient) -> None:
    org = _client_with_diagnostic(api)
    _sell(staff, org["id"])

    response = api.post("/me/closer", {**PAYOUT, "accept_terms": False})

    assert response.status_code == 400


def test_should_update_payout_account(api: ApiClient, staff: ApiClient) -> None:
    _closer(api, staff)

    response = api.patch(
        "/me/closer", {"payout_method": "WAVE", "payout_account": "+2250700000000"}
    )

    assert response.status_code == 200
    assert response.json()["closer"]["payout_method"] == "WAVE"


# ── Commissions ──


def test_should_earn_twenty_percent_of_referred_client_payment(
    api: ApiClient, staff: ApiClient, http_client: TestClient
) -> None:
    closer = _closer(api, staff)
    _, client_org = _referred_client(http_client, closer["code"].lower())

    _sell(staff, client_org["id"])
    run_pending_jobs(http_client)

    space = _space(api)
    assert [r["organization_name"] for r in space["referrals"]] == ["Maquis Chez Tanti"]
    assert space["referrals"][0]["commissions_total"] == 21980  # 20 % de 109 900
    assert space["pending"] == [{"currency": "XOF", "amount": 21980, "count": 1}]


def test_should_pay_commission_on_renewals_within_first_twelve_months_only(
    api: ApiClient, staff: ApiClient, http_client: TestClient
) -> None:
    closer = _closer(api, staff)
    _, client_org = _referred_client(http_client, closer["code"])
    today = datetime.now(UTC).date()

    _sell(staff, client_org["id"], "DIGITAL_START", received_on=today - timedelta(days=400))
    _sell(staff, client_org["id"], "DIGITAL_ESSENTIAL", received_on=today)
    run_pending_jobs(http_client)

    # Seul le premier paiement est dans les 12 mois : le second n'est pas commissionné
    assert _space(api)["referrals"][0]["commissions_total"] == 21980


def test_should_ignore_own_code_and_unknown_code_without_blocking_signup(
    api: ApiClient, staff: ApiClient, http_client: TestClient
) -> None:
    closer = _closer(api, staff)
    own = api.post(
        "/orgs",
        {"commercial_name": "Ma 2e boutique", "country": "CI", "referral_code": closer["code"]},
    )
    _, unknown = _referred_client(http_client, "ZZZZZZ")

    assert own.status_code == 201
    assert unknown["id"]
    assert _space(api)["referrals"] == []


def test_should_not_earn_commission_while_suspended(
    api: ApiClient, staff: ApiClient, http_client: TestClient
) -> None:
    closer = _closer(api, staff)
    _, client_org = _referred_client(http_client, closer["code"])

    assert staff.patch(f"/admin/closers/{closer['id']}", {"status": "SUSPENDED"}).status_code == 204
    _sell(staff, client_org["id"])
    run_pending_jobs(http_client)

    assert _space(api)["pending"] == []


def test_should_attach_client_manually_and_refuse_self_referral(
    api: ApiClient, staff: ApiClient, http_client: TestClient
) -> None:
    closer = _closer(api, staff)
    _, client_org = _referred_client(http_client, None)
    own_org_id = api.get("/me").json()["memberships"][0]["organization_id"]

    attached = staff.request(
        "PUT", f"/admin/organizations/{client_org['id']}/closer", {"closer_code": closer["code"]}
    )
    own = staff.request(
        "PUT", f"/admin/organizations/{own_org_id}/closer", {"closer_code": closer["code"]}
    )
    _sell(staff, client_org["id"])
    run_pending_jobs(http_client)

    assert attached.status_code == 200
    assert own.status_code == 422
    assert own.json()["code"] == "SELF_REFERRAL"
    assert _space(api)["referrals"][0]["commissions_total"] == 21980


# ── Relevé mensuel et versement ──


def _run_monthly_statements(http_client: TestClient) -> None:
    app: Any = http_client.app

    async def run() -> None:
        async with staff_transaction(app.state.session_factory) as session:
            await enqueue(session, MONTHLY_STATEMENTS_JOB, {})

    http_client.portal.call(run)  # type: ignore[union-attr]
    run_pending_jobs(http_client)


def test_should_build_monthly_statement_and_let_team_pay_it(
    api: ApiClient,
    staff: ApiClient,
    http_client: TestClient,
    outbox: RecordingEmailSender,
) -> None:
    closer = _closer(api, staff)
    _, client_org = _referred_client(http_client, closer["code"])
    last_month = datetime.now(UTC).date().replace(day=1) - timedelta(days=3)
    _sell(staff, client_org["id"], received_on=last_month)
    run_pending_jobs(http_client)
    outbox.sent.clear()

    _run_monthly_statements(http_client)
    _run_monthly_statements(http_client)  # relancé : aucun doublon

    [statement] = _space(api)["statements"]
    assert statement["status"] == "DUE"
    assert (statement["total_amount"], statement["commission_count"]) == (21980, 1)
    assert statement["period"] == last_month.replace(day=1).isoformat()
    assert _space(api)["pending"] == []
    email = api.get("/me").json()["user"]["email"]
    statements_sent = [m for m in outbox.sent if m.subject.startswith("Votre relevé Closer 3.0")]
    assert [m.to for m in statements_sent if email in m.to] == [[email]]

    due = staff.get("/admin/commission-statements?status=DUE").json()["data"]
    mine = next(item for item in due if item["closer_code"] == closer["code"])
    assert mine["closer_payout_account"] == "+22997000000"
    paid = staff.post(
        f"/admin/commission-statements/{mine['id']}/pay",
        {"method": "MTN_MOMO", "reference": "MTN-PAYOUT-01"},
    )
    again = staff.post(
        f"/admin/commission-statements/{mine['id']}/pay",
        {"method": "MTN_MOMO", "reference": "MTN-PAYOUT-01"},
    )
    run_pending_jobs(http_client)

    assert paid.status_code == 200
    assert paid.json()["status"] == "PAID"
    assert again.status_code == 409
    assert _space(api)["statements"][0]["payout_reference"] == "MTN-PAYOUT-01"
    assert any(m.subject.startswith("Vos commissions de") for m in outbox.sent)


def test_should_keep_current_month_commissions_for_next_statement(
    api: ApiClient, staff: ApiClient, http_client: TestClient
) -> None:
    closer = _closer(api, staff)
    _, client_org = _referred_client(http_client, closer["code"])
    _sell(staff, client_org["id"])
    run_pending_jobs(http_client)

    _run_monthly_statements(http_client)

    assert _space(api)["statements"] == []
    assert _space(api)["pending"][0]["amount"] == 21980


# ── Accès de l'équipe ──


def test_should_show_closers_and_totals_to_team(
    api: ApiClient, staff: ApiClient, http_client: TestClient
) -> None:
    closer = _closer(api, staff)
    _, client_org = _referred_client(http_client, closer["code"])
    _sell(staff, client_org["id"])
    run_pending_jobs(http_client)

    body = staff.get("/admin/closers").json()
    row = next(item for item in body["data"] if item["code"] == closer["code"])

    assert (row["referred_clients"], row["paying_clients"]) == (1, 1)
    assert row["earned"] == [{"amount": 21980, "currency": "XOF"}]
    assert row["payout_account"] == "+22997000000"


def test_should_forbid_admin_closer_routes_to_clients(api: ApiClient, staff: ApiClient) -> None:
    _closer(api, staff)

    assert api.get("/admin/closers").status_code == 403
    assert api.get("/admin/commission-statements").status_code == 403


def test_should_forbid_payout_to_content_manager(
    second_api: ApiClient, test_database_url: str
) -> None:
    _staff_member(second_api, test_database_url, "CONTENT_MANAGER")

    response = second_api.post(
        "/admin/commission-statements/01a10000-0000-7000-8000-000000000000/pay",
        {"method": "WAVE", "reference": "X"},
    )

    assert response.status_code == 403
