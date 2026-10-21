"""Client SasPay : montants, lecture des statuts et requêtes envoyées (sans réseau)."""

import asyncio
from typing import Any

import pytest

from digital360.modules.billing.infrastructure.payment_gateway import (
    GatewayStatus,
    PaymentGatewayError,
)
from digital360.modules.billing.infrastructure.saspay import (
    SasPayGateway,
    parse_status,
    to_decimal_amount,
    to_minor_amount,
    unwrap,
)

VERIFIED = {
    "id": "9c3f2a10-4b7e-4f1a-9d2e-9b6a7c1e4a02",
    "reference": "TXN-20261016-000512",
    "requested_amount": "109900.00",
    "net_amount": "107976.75",
    "currency": "XOF",
    "status": "SUCCESS",
    "external_reference": "PWP-8827311",
}


def _session(status: str, transaction_status: str | None = None) -> dict[str, Any]:
    return {
        "id": "c9a17e2c-4b1a-4f0e-9c3d-2a1b3c4d5e6f",
        "status": status,
        "transaction_id": VERIFIED["id"] if transaction_status else None,
        "transaction_status": transaction_status,
        "transaction_reference": "b1f2c3d4",
    }


class RecordingSasPay(SasPayGateway):
    """Remplace l'appel HTTP par des réponses préparées, en gardant la trace des requêtes."""

    def __init__(self, answers: dict[str, dict[str, Any]]) -> None:
        super().__init__("sk_test_x")
        self.answers = answers
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((method, path, body))
        return self.answers[path]


# ── Montants ──


def test_should_send_cfa_amount_as_decimal_string_without_minor_unit() -> None:
    assert to_decimal_amount(109900, "XOF") == "109900.00"
    assert to_decimal_amount(15000, "XAF") == "15000.00"


def test_should_convert_euro_cents_to_decimal_string() -> None:
    assert to_decimal_amount(2550, "EUR") == "25.50"


def test_should_read_decimal_amount_back_in_minor_unit() -> None:
    assert to_minor_amount("109900.00", "XOF") == 109900
    assert to_minor_amount("25.50", "EUR") == 2550
    assert to_minor_amount(None, "XOF") is None
    assert to_minor_amount("abc", "XOF") is None


# ── Statuts ──


def test_should_report_success_only_when_session_paid_and_transaction_verified() -> None:
    status = parse_status(_session("PAID", "SUCCESS"), VERIFIED)

    assert status.status is GatewayStatus.SUCCESS
    assert status.paid is True
    # Montant demandé, pas le net après frais : c'est lui qu'on compare au prix figé
    assert (status.amount, status.currency) == (109900, "XOF")
    assert status.order_id == "TXN-20261016-000512"
    assert status.provider_reference == "PWP-8827311"


def test_should_stay_pending_when_paid_session_is_not_verified_yet() -> None:
    status = parse_status(_session("PAID", "SUCCESS"), {**VERIFIED, "status": "PENDING"})

    assert status.status is GatewayStatus.PENDING
    assert status.paid is False


def test_should_stay_pending_after_failed_attempt_since_client_can_retry() -> None:
    status = parse_status(_session("PENDING", "FAILED"), None)

    assert status.status is GatewayStatus.PENDING
    assert status.paid is False


@pytest.mark.parametrize("state", ["CANCELLED", "EXPIRED"])
def test_should_report_cancelled_when_session_is_closed(state: str) -> None:
    assert parse_status(_session(state), None).status is GatewayStatus.CANCELLED


def test_should_reject_unknown_session_status() -> None:
    with pytest.raises(PaymentGatewayError):
        parse_status(_session("WHATEVER"), None)


# ── Réponses ──


def test_should_unwrap_saspay_envelope() -> None:
    assert unwrap({"success": True, "data": {"id": "x"}, "code": 200}) == {"id": "x"}


def test_should_accept_bare_response() -> None:
    assert unwrap({"id": "x", "status": "PENDING"}) == {"id": "x", "status": "PENDING"}


def test_should_reject_unreadable_response() -> None:
    with pytest.raises(PaymentGatewayError):
        unwrap(["not", "an", "object"])


# ── Requêtes ──


def test_should_create_hosted_checkout_with_decimal_amount_and_return_url() -> None:
    gateway = RecordingSasPay(
        {
            "/checkout-sessions/": {
                "id": "c9a1",
                "slug": "kZ2v",
                "checkout_url": "https://pay.saspay.me/checkout/kZ2v",
                "status": "PENDING",
            }
        }
    )

    session = asyncio.run(
        gateway.create_session(
            amount=109900,
            currency="XOF",
            description="BENILAB Digital360 — Start",
            customer_email="client@example.com",
            customer_name="Awa Client",
            country="BJ",
            return_url="https://digital360.test/dashboard.html?paiement=1",
            metadata={"app": "digital360"},
        )
    )

    assert (session.id, session.url) == ("c9a1", "https://pay.saspay.me/checkout/kZ2v")
    method, path, body = gateway.calls[0]
    assert (method, path) == ("POST", "/checkout-sessions/")
    assert body is not None
    assert body["amount"] == "109900.00"
    assert body["country"] == "BJ"
    assert body["return_url"] == "https://digital360.test/dashboard.html?paiement=1"


def test_should_refuse_checkout_without_payment_page() -> None:
    gateway = RecordingSasPay({"/checkout-sessions/": {"id": "c9a1", "checkout_url": ""}})

    with pytest.raises(PaymentGatewayError):
        asyncio.run(
            gateway.create_session(
                amount=1,
                currency="XOF",
                description="x",
                customer_email="a@b.c",
                customer_name="x",
                country=None,
                return_url="https://x.test/",
                metadata={},
            )
        )


def test_should_verify_transaction_before_confirming_paid_session() -> None:
    session_id = "c9a1"
    gateway = RecordingSasPay(
        {
            f"/checkout-sessions/{session_id}/status/": _session("PAID", "SUCCESS"),
            f"/payments/{VERIFIED['id']}/verify/": VERIFIED,
        }
    )

    status = asyncio.run(gateway.get_status(session_id))

    assert status.status is GatewayStatus.SUCCESS
    assert [path for _, path, _ in gateway.calls] == [
        f"/checkout-sessions/{session_id}/status/",
        f"/payments/{VERIFIED['id']}/verify/",
    ]


def test_should_not_call_verify_while_session_is_pending() -> None:
    gateway = RecordingSasPay({"/checkout-sessions/c9a1/status/": _session("PENDING")})

    assert asyncio.run(gateway.get_status("c9a1")).status is GatewayStatus.PENDING
    assert len(gateway.calls) == 1
