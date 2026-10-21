"""Paiement en ligne SasPay : lancement, confirmation par interrogation, activation, reprises."""

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from digital360.main import create_app
from digital360.modules.billing.application.online_payments import CHECK_DELAYS_MINUTES
from digital360.modules.billing.infrastructure.payment_gateway import (
    GatewaySession,
    GatewaySessionStatus,
    GatewayStatus,
    PaymentGatewayError,
)
from tests.conftest import make_settings
from tests.integration.api_client import ApiClient
from tests.integration.conftest import RecordingEmailSender, run_pending_jobs
from tests.integration.test_admin_api import _client_with_diagnostic, _staff_member

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("published_config")]


class FakeSasPay:
    """SasPay simulé : chaque session reste en attente jusqu'à ce que le test la règle."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.statuses: dict[str, GatewaySessionStatus] = {}
        self.down = False

    async def create_session(self, **request: Any) -> GatewaySession:
        if self.down:
            raise PaymentGatewayError("SasPay injoignable")
        # Unique d'un lancement à l'autre : la base de test garde les sessions précédentes
        number = uuid.uuid4().hex[:12]
        session = GatewaySession(
            id=f"cs_test_{number}",
            url=f"https://pay.saspay.test/{number}",
            order_id=f"CS-{number}",
        )
        self.created.append({"session_id": session.id, **request})
        self.statuses[session.id] = self._status(GatewayStatus.PENDING, request["amount"])
        return session

    async def get_status(self, session_id: str) -> GatewaySessionStatus:
        if self.down:
            raise PaymentGatewayError("SasPay injoignable")
        return self.statuses[session_id]

    def settle(self, session_id: str, status: GatewayStatus, *, amount: int | None = None) -> None:
        current = self.statuses[session_id]
        self.statuses[session_id] = self._status(status, amount or current.amount or 0)

    @staticmethod
    def _status(status: GatewayStatus, amount: int) -> GatewaySessionStatus:
        return GatewaySessionStatus(
            status=status,
            paid=status is GatewayStatus.SUCCESS,
            amount=amount,
            currency="XOF",
            order_id=None,
            provider="fedapay",
            provider_reference="FDP-778899" if status is GatewayStatus.SUCCESS else None,
        )


@pytest.fixture
def saspay() -> FakeSasPay:
    return FakeSasPay()


@pytest.fixture
def db_app(test_database_url: str, outbox: RecordingEmailSender, saspay: FakeSasPay) -> FastAPI:
    settings = make_settings(
        database_url=test_database_url,
        sales_alert_emails=["ventes@benilab.test"],
        app_url="https://digital360.test/",
    )
    return create_app(settings, email_sender=outbox, payment_gateway=saspay)


@pytest.fixture
def http_client(db_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(db_app) as client:
        yield client


def _start(api: ApiClient, org_id: str, product_code: str = "DIGITAL_START") -> Any:
    return api.post(f"/orgs/{org_id}/checkouts", {"product_code": product_code})


def _entitlement(api: ApiClient, org_id: str, key: str) -> Any:
    body = api.get(f"/orgs/{org_id}/entitlements").json()
    return next(item["value"] for item in body["entitlements"] if item["key"] == key)


def _run_checks_now(database_url: str, http_client: TestClient) -> None:
    """Avance l'horloge des vérifications en arrière-plan, puis les exécute."""

    async def run() -> None:
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.scope', 'staff', true)"))
            await connection.execute(
                text(
                    "UPDATE jobs SET run_at = now() "
                    "WHERE name = 'billing.check_online_checkout' AND status = 'PENDING'"
                )
            )
        await engine.dispose()

    asyncio.run(run())
    run_pending_jobs(http_client)


# ── Lancement ──


def test_should_open_saspay_page_with_frozen_price_and_return_url(
    api: ApiClient, saspay: FakeSasPay
) -> None:
    org = _client_with_diagnostic(api)

    response = _start(api, org["id"])

    assert response.status_code == 201, response.json()
    checkout = response.json()
    assert checkout["status"] == "PENDING"
    assert checkout["checkout_url"].startswith("https://pay.saspay.test/")
    assert (checkout["amount"], checkout["currency"]) == (109900, "XOF")
    sent = saspay.created[-1]
    assert sent["amount"] == 109900
    assert sent["return_url"] == f"https://digital360.test/dashboard.html?paiement={checkout['id']}"
    # Pays présélectionné sur la page de paiement, paiement reconnaissable par son app
    assert sent["country"] == api.get(f"/orgs/{org['id']}").json()["country"]
    assert sent["metadata"]["app"] == "digital360"


def test_should_reuse_open_payment_page_on_double_click(api: ApiClient, saspay: FakeSasPay) -> None:
    org = _client_with_diagnostic(api)

    first = _start(api, org["id"]).json()
    second = _start(api, org["id"]).json()

    assert second["id"] == first["id"]
    assert second["checkout_url"] == first["checkout_url"]
    assert len([c for c in saspay.created if c["metadata"]["checkout_id"] == first["id"]]) == 1


def test_should_refuse_online_payment_outside_xof_zone(api: ApiClient) -> None:
    api.register()
    org = api.post("/orgs", {"commercial_name": "Atelier Lyon", "country": "FR"}).json()

    response = _start(api, org["id"])

    assert response.status_code == 422
    assert response.json()["code"] == "ONLINE_PAYMENT_UNAVAILABLE"
    assert api.get(f"/orgs/{org['id']}/billing").json()["online_payment_available"] is False


def test_should_report_provider_outage_and_mark_checkout_failed(
    api: ApiClient, saspay: FakeSasPay
) -> None:
    org = _client_with_diagnostic(api)
    saspay.down = True

    response = _start(api, org["id"])

    assert response.status_code == 502
    assert response.json()["code"] == "PAYMENT_PROVIDER_ERROR"
    saspay.down = False
    # Aucune page en attente à réutiliser : un nouvel essai ouvre une nouvelle session
    assert _start(api, org["id"]).status_code == 201


def test_should_forbid_starting_payment_for_another_organization(
    api: ApiClient, second_api: ApiClient
) -> None:
    org = _client_with_diagnostic(api)
    second_api.register()

    assert _start(second_api, org["id"]).status_code in (403, 404)


def test_should_answer_503_when_online_payment_is_not_configured(
    test_database_url: str, outbox: RecordingEmailSender
) -> None:
    app = create_app(make_settings(database_url=test_database_url), email_sender=outbox)
    with TestClient(app) as client:
        api = ApiClient(client)
        org = _client_with_diagnostic(api)

        assert api.get(f"/orgs/{org['id']}/billing").json()["online_payment_available"] is False
        response = _start(api, org["id"])

    assert response.status_code == 503
    assert response.json()["code"] == "ONLINE_PAYMENT_UNAVAILABLE"


# ── Confirmation au retour du client ──


def test_should_activate_start_when_saspay_confirms_on_return(
    api: ApiClient,
    http_client: TestClient,
    outbox: RecordingEmailSender,
    saspay: FakeSasPay,
) -> None:
    org = _client_with_diagnostic(api)
    run_pending_jobs(http_client)
    outbox.sent.clear()
    checkout = _start(api, org["id"]).json()
    saspay.settle(saspay.created[-1]["session_id"], GatewayStatus.SUCCESS)

    returned = api.get(f"/orgs/{org['id']}/checkouts/{checkout['id']}")
    run_pending_jobs(http_client)

    assert returned.status_code == 200
    body = returned.json()
    assert body["status"] == "PAID"
    assert body["checkout_url"] is None
    assert body["receipt_number"].startswith("REC-")
    assert _entitlement(api, org["id"], "WEBSITE") is True
    assert api.get(f"/orgs/{org['id']}").json()["status"] == "ACTIVE"
    assert api.get(f"/orgs/{org['id']}/website-project").json()["status"] == "BRIEF_PENDING"
    payment = api.get(f"/orgs/{org['id']}/billing").json()["payments"][0]
    assert (payment["method"], payment["amount"]) == ("SASPAY", 109900)
    assert payment["reference"] == saspay.created[-1]["session_id"].replace("cs_test_", "CS-")
    receipt = next(m for m in outbox.sent if m.subject.startswith("Reçu de paiement"))
    assert "Paiement en ligne (SasPay)" in receipt.text


def test_should_record_payment_once_when_client_returns_twice(
    api: ApiClient, saspay: FakeSasPay
) -> None:
    org = _client_with_diagnostic(api)
    checkout = _start(api, org["id"]).json()
    saspay.settle(saspay.created[-1]["session_id"], GatewayStatus.SUCCESS)

    api.get(f"/orgs/{org['id']}/checkouts/{checkout['id']}")
    api.get(f"/orgs/{org['id']}/checkouts/{checkout['id']}")

    assert len(api.get(f"/orgs/{org['id']}/billing").json()["payments"]) == 1


def test_should_close_open_purchase_request_when_paid_online(
    api: ApiClient, second_api: ApiClient, saspay: FakeSasPay, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    request = api.post(
        f"/orgs/{org['id']}/purchase-requests",
        {"product_code": "DIGITAL_START", "channel": "EMAIL"},
    ).json()
    checkout = _start(api, org["id"]).json()
    saspay.settle(saspay.created[-1]["session_id"], GatewayStatus.SUCCESS)

    api.get(f"/orgs/{org['id']}/checkouts/{checkout['id']}")

    _staff_member(second_api, test_database_url, "MANAGER")
    listed = second_api.get("/admin/purchase-requests").json()["data"]
    closed = next(item for item in listed if item["id"] == request["id"])
    assert closed["status"] == "WON"
    assert closed["payment"]["channel"] == "ONLINE"
    assert [item for item in listed if item["organization_id"] == org["id"]] == [closed]


def test_should_keep_offer_inactive_when_payment_is_refused(
    api: ApiClient, saspay: FakeSasPay
) -> None:
    org = _client_with_diagnostic(api)
    checkout = _start(api, org["id"]).json()
    saspay.settle(saspay.created[-1]["session_id"], GatewayStatus.FAILED)

    body = api.get(f"/orgs/{org['id']}/checkouts/{checkout['id']}").json()

    assert body["status"] == "FAILED"
    assert _entitlement(api, org["id"], "WEBSITE") is False
    assert api.get(f"/orgs/{org['id']}/billing").json()["payments"] == []


def test_should_refuse_payment_when_amount_is_lower_than_price(
    api: ApiClient, saspay: FakeSasPay
) -> None:
    org = _client_with_diagnostic(api)
    checkout = _start(api, org["id"]).json()
    saspay.settle(saspay.created[-1]["session_id"], GatewayStatus.SUCCESS, amount=100)

    body = api.get(f"/orgs/{org['id']}/checkouts/{checkout['id']}").json()

    assert body["status"] == "FAILED"
    assert _entitlement(api, org["id"], "WEBSITE") is False


def test_should_stay_pending_when_saspay_is_unreachable_on_return(
    api: ApiClient, saspay: FakeSasPay
) -> None:
    org = _client_with_diagnostic(api)
    checkout = _start(api, org["id"]).json()
    saspay.down = True

    response = api.get(f"/orgs/{org['id']}/checkouts/{checkout['id']}")

    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"


def test_should_refuse_paying_start_twice(api: ApiClient, saspay: FakeSasPay) -> None:
    org = _client_with_diagnostic(api)
    checkout = _start(api, org["id"]).json()
    saspay.settle(saspay.created[-1]["session_id"], GatewayStatus.SUCCESS)
    api.get(f"/orgs/{org['id']}/checkouts/{checkout['id']}")

    again = _start(api, org["id"])

    assert again.status_code == 409
    assert again.json()["code"] == "ALREADY_PURCHASED"


# ── Abonnement : achat puis renouvellement en ligne ──


def test_should_extend_subscription_from_due_date_when_renewed_online(
    api: ApiClient, saspay: FakeSasPay
) -> None:
    org = _client_with_diagnostic(api)
    first = _start(api, org["id"], "DIGITAL_ESSENTIAL").json()
    saspay.settle(saspay.created[-1]["session_id"], GatewayStatus.SUCCESS)
    api.get(f"/orgs/{org['id']}/checkouts/{first['id']}")
    covered = api.get(f"/orgs/{org['id']}/billing").json()["subscriptions"][0]

    renewal = _start(api, org["id"], "DIGITAL_ESSENTIAL").json()
    saspay.settle(saspay.created[-1]["session_id"], GatewayStatus.SUCCESS)
    assert api.get(f"/orgs/{org['id']}/checkouts/{renewal['id']}").json()["status"] == "PAID"

    billing = api.get(f"/orgs/{org['id']}/billing").json()
    assert len(billing["payments"]) == 2
    assert [s["product_code"] for s in billing["subscriptions"]] == ["DIGITAL_ESSENTIAL"]
    assert billing["subscriptions"][0]["days_left"] - covered["days_left"] in (30, 31)


# ── Vérification en arrière-plan (client qui ferme la page) ──


def test_should_activate_offer_in_background_when_client_never_returns(
    api: ApiClient, http_client: TestClient, saspay: FakeSasPay, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    checkout = _start(api, org["id"]).json()
    saspay.settle(saspay.created[-1]["session_id"], GatewayStatus.SUCCESS)

    _run_checks_now(test_database_url, http_client)

    assert _entitlement(api, org["id"], "WEBSITE") is True
    assert api.get(f"/orgs/{org['id']}/checkouts/{checkout['id']}").json()["status"] == "PAID"


def test_should_expire_abandoned_payment_after_last_check(
    api: ApiClient, http_client: TestClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    checkout = _start(api, org["id"]).json()

    for _ in CHECK_DELAYS_MINUTES:
        _run_checks_now(test_database_url, http_client)

    body = api.get(f"/orgs/{org['id']}/checkouts/{checkout['id']}").json()
    assert body["status"] == "EXPIRED"
    assert body["checkout_url"] is None


# ── L'équipe ne saisit pas de paiement en ligne à la main ──


def test_should_refuse_manual_payment_declared_as_online(
    api: ApiClient, second_api: ApiClient, test_database_url: str
) -> None:
    org = _client_with_diagnostic(api)
    _staff_member(second_api, test_database_url, "MANAGER")

    response = second_api.post(
        f"/admin/organizations/{org['id']}/sales",
        {
            "product_code": "DIGITAL_START",
            "payment": {"method": "SASPAY", "reference": "CS-FAUX"},
        },
    )

    assert response.status_code == 400
