"""Passerelle de paiement en ligne SasPay (mobile money et carte, Afrique de l'Ouest et du Centre).

Le client paie sur la page hébergée SasPay (`POST /checkout-sessions/`). Le paiement est
confirmé en interrogeant SasPay : `GET /checkout-sessions/{id}/status/` revérifie l'état réel
auprès de l'opérateur, puis `GET /payments/{transaction_id}/verify/` doit répondre SUCCESS
avant toute activation (règle de SasPay). Documentation : https://docs.saspay.me/llms.txt
"""

import asyncio
import json
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Any

from digital360.core.config import Settings
from digital360.modules.billing.infrastructure.payment_gateway import (
    GatewaySession,
    GatewaySessionStatus,
    GatewayStatus,
    PaymentGateway,
    PaymentGatewayError,
)

SASPAY_API_URL = "https://api.saspay.me/api/v1"
# Nos montants sont en unité mineure ; le franc CFA n'a pas de centimes
ZERO_DECIMAL_CURRENCIES = {"XOF", "XAF"}


def to_decimal_amount(amount: int, currency: str) -> str:
    """Montant SasPay : chaîne décimale en unité principale (« 15000.00 »), jamais un flottant."""
    if currency.upper() in ZERO_DECIMAL_CURRENCIES:
        return f"{amount}.00"
    return f"{Decimal(amount) / 100:.2f}"


def to_minor_amount(value: object, currency: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if (currency or "").upper() in ZERO_DECIMAL_CURRENCIES:
        return int(amount)
    return int(amount * 100)


def parse_status(
    session_status: dict[str, Any], transaction: dict[str, Any] | None
) -> GatewaySessionStatus:
    """Traduit la réponse SasPay. Seule une transaction vérifiée SUCCESS vaut paiement.

    Un échec de la transaction ne clôt pas la session : le client peut réessayer sur la page
    SasPay, le paiement reste donc en attente tant que la session n'est ni payée ni close.
    """
    state = str(session_status.get("status", "")).upper()
    if state == "PAID" and transaction is not None:
        if str(transaction.get("status", "")).upper() == "SUCCESS":
            currency = transaction.get("currency")
            return GatewaySessionStatus(
                status=GatewayStatus.SUCCESS,
                paid=True,
                # Montant demandé (hors frais SasPay), à comparer au prix figé chez nous
                amount=to_minor_amount(transaction.get("requested_amount"), currency),
                currency=currency,
                order_id=transaction.get("reference")
                or session_status.get("transaction_reference"),
                provider="saspay",
                provider_reference=transaction.get("external_reference") or None,
            )
    elif state in ("CANCELLED", "EXPIRED"):
        return _without_payment(GatewayStatus.CANCELLED)
    elif state not in ("PENDING", "PAID"):
        raise PaymentGatewayError(f"statut SasPay inconnu : {state!r}")
    # Session payée mais transaction pas encore vérifiée SUCCESS : on revérifiera plus tard
    return _without_payment(GatewayStatus.PENDING)


def _without_payment(status: GatewayStatus) -> GatewaySessionStatus:
    return GatewaySessionStatus(
        status=status,
        paid=False,
        amount=None,
        currency=None,
        order_id=None,
        provider="saspay",
        provider_reference=None,
    )


class SasPayGateway:
    def __init__(self, secret_key: str, *, timeout: float = 15.0) -> None:
        self._secret_key = secret_key
        self._timeout = timeout

    async def create_session(
        self,
        *,
        amount: int,
        currency: str,
        description: str,
        customer_email: str,
        customer_name: str,
        country: str | None,
        return_url: str,
        metadata: dict[str, str],
    ) -> GatewaySession:
        # Pas de customer_phone : le client choisit son réseau et saisit son numéro sur la page
        body: dict[str, Any] = {
            "amount": to_decimal_amount(amount, currency),
            "currency": currency,
            "description": description,
            "customer_email": customer_email,
            "customer_name": customer_name,
            # SasPay ne renvoie vers cette adresse qu'après un paiement réussi
            "return_url": return_url,
            "metadata": metadata,
        }
        if country:
            body["country"] = country
        answer = await self._call("POST", "/checkout-sessions/", body)
        if not answer.get("id") or not answer.get("checkout_url"):
            raise PaymentGatewayError("SasPay n'a pas renvoyé de page de paiement")
        return GatewaySession(
            id=str(answer["id"]), url=str(answer["checkout_url"]), order_id=answer.get("slug")
        )

    async def get_status(self, session_id: str) -> GatewaySessionStatus:
        session_status = await self._call("GET", f"/checkout-sessions/{session_id}/status/")
        transaction = None
        transaction_id = session_status.get("transaction_id")
        if str(session_status.get("status", "")).upper() == "PAID" and transaction_id:
            transaction = await self._call("GET", f"/payments/{transaction_id}/verify/")
        return parse_status(session_status, transaction)

    async def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        request = urllib.request.Request(  # noqa: S310 (URL fixe en https)
            SASPAY_API_URL + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self._secret_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method=method,
        )
        # urllib est bloquant : exécuté hors de la boucle d'événements
        return await asyncio.to_thread(self._send, request)

    def _send(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(  # noqa: S310 (URL fixe)
                request, timeout=self._timeout
            ) as response:
                return unwrap(json.loads(response.read() or b"{}"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise PaymentGatewayError(
                f"SasPay a refusé la requête ({exc.code}) : {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise PaymentGatewayError(f"SasPay injoignable : {exc}") from exc


def unwrap(answer: Any) -> dict[str, Any]:
    """SasPay enveloppe ses réponses ({success, data, code}) ; certains exemples en sont dépourvus."""
    if isinstance(answer, dict) and "success" in answer and isinstance(answer.get("data"), dict):
        data: dict[str, Any] = answer["data"]
        return data
    if not isinstance(answer, dict):
        raise PaymentGatewayError("réponse SasPay illisible")
    return answer


def build_payment_gateway(settings: Settings) -> PaymentGateway | None:
    """Sans clé, le paiement en ligne est désactivé : seul le paiement manuel reste proposé."""
    if settings.saspay_secret_key is None:
        return None
    return SasPayGateway(settings.saspay_secret_key.get_secret_value())
