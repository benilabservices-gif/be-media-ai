"""Contrat entre le paiement en ligne et le fournisseur de paiement (aujourd'hui SasPay)."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


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
    # Opérateur réellement utilisé et référence de la transaction chez lui
    provider: str | None
    provider_reference: str | None


class PaymentGatewayError(Exception):
    """Fournisseur de paiement injoignable ou requête refusée."""


class PaymentGateway(Protocol):
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
    ) -> GatewaySession: ...

    async def get_status(self, session_id: str) -> GatewaySessionStatus: ...
