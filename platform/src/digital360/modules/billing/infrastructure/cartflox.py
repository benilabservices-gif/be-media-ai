"""Passerelle de paiement en ligne Cartflox (mobile money et carte, zone FCFA).

Digital360 utilise l'espace Cartflox « SchoolConnect », dont l'unique adresse webhook sert
déjà un autre projet. Le paiement est donc confirmé en interrogeant Cartflox
(`GET /checkout/sessions/{id}/status`), qui fait foi au même titre que le webhook.
"""

import asyncio
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from digital360.core.config import Settings

logger = logging.getLogger("digital360.billing.cartflox")

CARTFLOX_API_URL = "https://cartflox.com/api/v1"


class GatewayStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


@dataclass(frozen=True)
class GatewaySession:
    id: str
    url: str
    order_id: str | None


@dataclass(frozen=True)
class GatewaySessionStatus:
    status: GatewayStatus
    paid: bool
    amount: int | None
    currency: str | None
    order_id: str | None
    # Opérateur réellement utilisé (FedaPay…) et référence de la transaction chez lui
    provider: str | None
    provider_reference: str | None


class PaymentGatewayError(Exception):
    """Cartflox injoignable ou requête refusée."""


class PaymentGateway(Protocol):
    async def create_session(
        self,
        *,
        amount: int,
        currency: str,
        description: str,
        customer_email: str,
        customer_name: str,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> GatewaySession: ...

    async def get_status(self, session_id: str) -> GatewaySessionStatus: ...


class CartfloxGateway:
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
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> GatewaySession:
        # Pas de customer_phone : Cartflox refuse certains numéros béninois à 10 chiffres,
        # le client saisit son numéro sur la page de paiement
        body = {
            "amount": amount,
            "currency": currency,
            "description": description,
            "customer_email": customer_email,
            "customer_name": customer_name,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": metadata,
            "merchant_name": "BENILAB Digital360",
        }
        answer = await self._call(
            "POST", "/checkout/sessions", body, {"Idempotency-Key": idempotency_key}
        )
        if not answer.get("id") or not answer.get("url"):
            raise PaymentGatewayError("Cartflox n'a pas renvoyé de page de paiement")
        return GatewaySession(
            id=str(answer["id"]), url=str(answer["url"]), order_id=answer.get("order_id")
        )

    async def get_status(self, session_id: str) -> GatewaySessionStatus:
        answer = await self._call("GET", f"/checkout/sessions/{session_id}/status")
        raw_status = str(answer.get("status", "")).upper()
        try:
            status = GatewayStatus(raw_status)
        except ValueError as exc:
            raise PaymentGatewayError(f"statut Cartflox inconnu : {raw_status!r}") from exc
        amount = answer.get("amount")
        return GatewaySessionStatus(
            status=status,
            paid=bool(answer.get("paid")),
            amount=int(amount) if amount is not None else None,
            currency=answer.get("currency"),
            order_id=answer.get("order_id"),
            provider=answer.get("provider"),
            provider_reference=answer.get("provider_reference"),
        )

    async def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request = urllib.request.Request(  # noqa: S310 (URL fixe en https)
            CARTFLOX_API_URL + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self._secret_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                **(extra_headers or {}),
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
                answer: dict[str, Any] = json.loads(response.read() or b"{}")
                return answer
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise PaymentGatewayError(
                f"Cartflox a refusé la requête ({exc.code}) : {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise PaymentGatewayError(f"Cartflox injoignable : {exc}") from exc


def build_payment_gateway(settings: Settings) -> PaymentGateway | None:
    """Sans clé, le paiement en ligne est désactivé : seul le paiement manuel reste proposé."""
    if settings.cartflox_secret_key is None:
        return None
    return CartfloxGateway(settings.cartflox_secret_key.get_secret_value())
