"""Abonnements payés hors ligne : échéance, rappel avant expiration, renouvellement."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from digital360.core.tenancy import staff_transaction
from digital360.main import create_app
from digital360.modules.billing.application.renewals import send_due_reminders
from tests.conftest import make_settings
from tests.integration.api_client import ApiClient
from tests.integration.conftest import RecordingEmailSender
from tests.integration.test_admin_api import _client_with_diagnostic, _staff_member

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("published_config")]

TEAM = "abonnements@benilab.test"
WAVE = {"method": "WAVE", "reference": "WV-RENEW-01"}


@pytest.fixture
def db_app(test_database_url: str, outbox: RecordingEmailSender) -> FastAPI:
    settings = make_settings(database_url=test_database_url, sales_alert_emails=[TEAM])
    return create_app(settings, email_sender=outbox)


@pytest.fixture
def http_client(db_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(db_app) as client:
        yield client


def _subscribed(api: ApiClient, staff: ApiClient, database_url: str) -> dict[str, Any]:
    """Client abonné à Essential (vente directe encaissée aujourd'hui)."""
    org = _client_with_diagnostic(api)
    _staff_member(staff, database_url, "MANAGER")
    sale = staff.post(
        f"/admin/organizations/{org['id']}/sales",
        {"product_code": "DIGITAL_ESSENTIAL", "payment": WAVE},
    )
    assert sale.status_code == 201, sale.json()
    return org


def _subscription(staff: ApiClient, org_id: str) -> dict[str, Any]:
    listed = staff.get("/admin/subscriptions").json()["data"]
    return next(s for s in listed if s["organization_id"] == org_id)


def _shift_coverage(database_url: str, org_id: str, days_from_now: float) -> None:
    """Simule le temps qui passe : l'échéance tombe dans `days_from_now` jours."""

    async def run() -> None:
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            # La table est protégée par la RLS : on agit comme un job système (portée staff)
            await connection.execute(text("SELECT set_config('app.scope', 'staff', true)"))
            await connection.execute(
                text(
                    "UPDATE payments SET covers_until = now() + make_interval(secs => :secs) "
                    "WHERE organization_id = :org AND covers_until IS NOT NULL"
                ),
                {"secs": days_from_now * 86400, "org": org_id},
            )
        await engine.dispose()

    asyncio.run(run())


def _run_reminders(http_client: TestClient, outbox: RecordingEmailSender) -> int:
    app: Any = http_client.app

    async def run() -> int:
        async with staff_transaction(app.state.session_factory) as session:
            return await send_due_reminders(
                session,
                outbox,
                team=[TEAM],
                app_url="https://app.test/",
                admin_url="https://app.test/admin.html",
                now=datetime.now(UTC),
            )

    sent: int = http_client.portal.call(run)
    return sent


# ── Échéance ──


def test_should_cover_subscription_for_one_month_and_none_for_start(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _subscribed(api, second_api, test_database_url)
    second_api.post(
        f"/admin/organizations/{org['id']}/sales",
        {"product_code": "DIGITAL_START", "payment": WAVE},
    )

    subscriptions = [
        s
        for s in second_api.get("/admin/subscriptions").json()["data"]
        if s["organization_id"] == org["id"]
    ]

    assert [s["product_code"] for s in subscriptions] == ["DIGITAL_ESSENTIAL"]
    essential = subscriptions[0]
    assert essential["status"] == "ACTIVE"
    assert essential["days_left"] in (30, 31)
    assert essential["last_payment"]["reference"] == "WV-RENEW-01"


def test_should_flag_expiring_and_expired_subscriptions(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _subscribed(api, second_api, test_database_url)

    _shift_coverage(test_database_url, org["id"], 3)
    expiring = _subscription(second_api, org["id"])
    _shift_coverage(test_database_url, org["id"], -1)
    expired = _subscription(second_api, org["id"])

    assert expiring["status"] == "EXPIRING"
    assert expired["status"] == "EXPIRED"
    assert second_api.get("/admin/dashboard").json()["sales"]["renewals_due"] >= 1


# ── Rappel avant échéance ──


def test_should_remind_client_and_team_once_before_expiry(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    outbox: RecordingEmailSender,
    test_database_url: str,
) -> None:
    org = _subscribed(api, second_api, test_database_url)
    _shift_coverage(test_database_url, org["id"], 4)
    outbox.sent.clear()

    _run_reminders(http_client, outbox)
    first = list(outbox.sent)
    outbox.sent.clear()
    _run_reminders(http_client, outbox)

    owner = api.get("/me").json()["user"]["email"]
    [reminder] = [m for m in first if owner in m.to]
    assert reminder.subject == "Votre abonnement Essential arrive à échéance"
    assert "45 000 FCFA HT" in reminder.text
    [digest] = [m for m in first if TEAM in m.to]
    assert org["commercial_name"] in digest.text
    # Rappel unique par période : rien de nouveau pour ce client au second passage
    assert not [m for m in outbox.sent if owner in m.to]


def test_should_not_remind_when_expiry_is_far(
    api: ApiClient,
    second_api: ApiClient,
    http_client: TestClient,
    outbox: RecordingEmailSender,
    test_database_url: str,
) -> None:
    _subscribed(api, second_api, test_database_url)
    outbox.sent.clear()

    _run_reminders(http_client, outbox)

    owner = api.get("/me").json()["user"]["email"]
    assert not [m for m in outbox.sent if owner in m.to]


# ── Renouvellement ──


def test_should_extend_from_due_date_when_client_pays_in_advance(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _subscribed(api, second_api, test_database_url)
    _shift_coverage(test_database_url, org["id"], 4)
    due = datetime.fromisoformat(_subscription(second_api, org["id"])["covers_until"])

    renewed = second_api.post(
        f"/admin/organizations/{org['id']}/renewals",
        {"product_code": "DIGITAL_ESSENTIAL", "payment": WAVE},
    )

    assert renewed.status_code == 201
    new_due = datetime.fromisoformat(renewed.json()["covers_until"])
    assert new_due - due == timedelta(days=31)  # aucun jour perdu
    assert renewed.json()["status"] == "ACTIVE"


def test_should_restart_from_today_and_restore_access_after_expiry(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _subscribed(api, second_api, test_database_url)
    _shift_coverage(test_database_url, org["id"], -10)

    renewed = second_api.post(
        f"/admin/organizations/{org['id']}/renewals",
        {"product_code": "DIGITAL_ESSENTIAL", "payment": {"method": "CASH"}},
    ).json()

    new_due = datetime.fromisoformat(renewed["covers_until"])
    assert timedelta(days=30) < new_due - datetime.now(UTC) <= timedelta(days=31)
    entitlements = api.get(f"/orgs/{org['id']}/entitlements").json()["entitlements"]
    assert next(e["value"] for e in entitlements if e["key"] == "CONTENT_MONTHLY_LIMIT") == 10


def test_should_refuse_renewal_without_existing_subscription(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    _staff_member(second_api, test_database_url, "MANAGER")

    response = second_api.post(
        f"/admin/organizations/{org['id']}/renewals",
        {"product_code": "DIGITAL_ESSENTIAL", "payment": WAVE},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "NO_SUBSCRIPTION"


def test_should_reserve_subscriptions_to_sales_team(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    api.register()
    _staff_member(second_api, test_database_url, "FINANCE")

    assert api.get("/admin/subscriptions").status_code == 403
    assert second_api.get("/admin/subscriptions").status_code == 403


# ── Contrôle quotidien ──


def test_should_schedule_one_sweep_per_day_and_plan_the_next_one(
    http_client: TestClient, test_database_url: str
) -> None:
    from sqlalchemy import select

    from digital360.core.jobs import Job, Worker
    from digital360.modules.billing.application.renewals import (
        RENEWAL_SWEEP_JOB,
        schedule_daily_sweep,
    )

    app: Any = http_client.app
    day = datetime(2031, 3, 14, 6, 0, tzinfo=UTC)  # date fictive : pas de collision entre tests

    async def run() -> list[str]:
        await schedule_daily_sweep(app.state.session_factory, now=day)
        await schedule_daily_sweep(app.state.session_factory, now=day)  # redémarrage
        worker = Worker(app.state.session_factory, app.state.job_registry, worker_id="t")
        while await worker.run_once():
            pass
        async with staff_transaction(app.state.session_factory) as session:
            keys = await session.execute(select(Job.dedup_key).where(Job.name == RENEWAL_SWEEP_JOB))
            return [key for key in keys.scalars() if key]

    keys: list[str] = http_client.portal.call(run)

    assert keys.count("renewal-sweep:2031-03-14") == 1
    tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    assert f"renewal-sweep:{tomorrow}" in keys
