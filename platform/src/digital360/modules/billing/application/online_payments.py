"""Paiement en ligne Cartflox : le client paie seul, l'offre s'active dès que Cartflox confirme.

1. Le client lance le paiement : le prix HT est figé, Cartflox ouvre une page de paiement.
2. Le paiement est confirmé en interrogeant Cartflox (le webhook de l'espace SchoolConnect
   sert déjà un autre projet) : au retour du client sur son espace, puis par des
   vérifications en arrière-plan s'il ferme la page avant.
3. Paiement confirmé : même suite qu'un encaissement manuel (vente gagnée ou renouvellement,
   droits, client actif, reçu, projet de site pour Start).
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.actor import Actor
from digital360.core.audit import record_audit
from digital360.core.errors import AppError
from digital360.core.jobs import JobRegistry, enqueue
from digital360.core.tenancy import TenantContext, staff_transaction, tenant_transaction
from digital360.modules.billing.application.renewals import (
    extend_subscription,
    latest_subscription_payment,
)
from digital360.modules.billing.application.service import (
    SUBSCRIPTION_PERIOD,
    PaymentInput,
    close_sale_as_won,
    open_request,
    receipt_number,
)
from digital360.modules.billing.infrastructure.cartflox import (
    GatewaySessionStatus,
    GatewayStatus,
    PaymentGateway,
    PaymentGatewayError,
)
from digital360.modules.billing.infrastructure.models import (
    CheckoutStatus,
    ContactChannel,
    OnlineCheckout,
    Payment,
    PaymentChannel,
    PaymentMethod,
    PurchaseRequest,
    PurchaseRequestStatus,
)
from digital360.modules.catalog.application.service import current_catalog
from digital360.modules.catalog.domain.models import BillingPeriod
from digital360.modules.identity.infrastructure.models import User
from digital360.modules.organizations.infrastructure.models import Organization

logger = logging.getLogger("digital360.billing.online")

# Cartflox encaisse en FCFA de l'UEMOA ; les autres devises restent en paiement manuel
ONLINE_CURRENCIES = {"XOF"}
CHECK_CHECKOUT_JOB = "billing.check_online_checkout"
# Vérifications en arrière-plan après le lancement (minutes) ; sans confirmation au bout
# d'environ deux jours, le paiement est considéré comme abandonné
CHECK_DELAYS_MINUTES = (2, 5, 15, 30, 60, 180, 360, 720, 1440)
# Un double clic ou un retour arrière réutilise la page de paiement déjà ouverte
REUSE_WINDOW = timedelta(minutes=30)
PROVIDER_ACTOR = Actor.provider("cartflox")


@dataclass(frozen=True)
class CheckoutView:
    id: uuid.UUID
    product_code: str
    product_name: str
    amount: int
    currency: str
    period: str | None
    status: str
    # Page de paiement Cartflox, tant que le paiement est en attente
    checkout_url: str | None
    # Numéro du reçu une fois payé
    receipt_number: str | None
    created_at: datetime


async def _view(session: AsyncSession, checkout: OnlineCheckout) -> CheckoutView:
    product = (await current_catalog(session)).product(checkout.product_code)
    payment = await session.get(Payment, checkout.payment_id) if checkout.payment_id else None
    pending = checkout.status == CheckoutStatus.PENDING.value
    return CheckoutView(
        id=checkout.id,
        product_code=checkout.product_code,
        product_name=product.name if product else checkout.product_code,
        amount=checkout.amount,
        currency=checkout.currency,
        period=checkout.period,
        status=checkout.status,
        checkout_url=checkout.checkout_url if pending else None,
        receipt_number=receipt_number(payment) if payment else None,
        created_at=checkout.created_at,
    )


def _not_enabled() -> AppError:
    return AppError(
        "ONLINE_PAYMENT_UNAVAILABLE",
        "Le paiement en ligne n'est pas encore activé : utilisez « Je veux démarrer ».",
        status=503,
    )


async def _record_payment(
    session: AsyncSession, registry: JobRegistry, checkout: OnlineCheckout, now: datetime
) -> Payment:
    """Paiement confirmé : renouvellement d'un abonnement en cours, sinon vente gagnée."""
    payment = PaymentInput(
        method=PaymentMethod.CARTFLOX,
        amount=checkout.amount,
        reference=checkout.provider_order_id or checkout.provider_reference,
        received_on=now.date(),
    )
    product = (await current_catalog(session)).product(checkout.product_code)
    period = SUBSCRIPTION_PERIOD.get(checkout.period or "")
    previous = (
        await latest_subscription_payment(session, checkout.organization_id, checkout.product_code)
        if period
        else None
    )
    if previous is not None and product is not None and period is not None:
        return await extend_subscription(
            session,
            registry,
            previous,
            product,
            amount=checkout.amount,
            currency=checkout.currency,
            period=period,
            payment=payment,
            actor=PROVIDER_ACTOR,
            granted_by=checkout.requested_by,
            recorded_by=None,
            channel=PaymentChannel.ONLINE,
            now=now,
        )
    # Une demande « Je veux démarrer » en cours pour cette offre est close par ce paiement
    request = await open_request(session, checkout.organization_id, checkout.product_code)
    if request is None:
        request = PurchaseRequest(
            organization_id=checkout.organization_id,
            requested_by=checkout.requested_by,
            product_code=checkout.product_code,
            amount=checkout.amount,
            currency=checkout.currency,
            period=checkout.period,
            channel=ContactChannel.EMAIL.value,
            message="Paiement en ligne",
        )
        session.add(request)
    request.status = PurchaseRequestStatus.WON.value
    await session.flush()
    return await close_sale_as_won(
        session,
        registry,
        request,
        payment,
        actor=PROVIDER_ACTOR,
        granted_by=checkout.requested_by,
        recorded_by=None,
        channel=PaymentChannel.ONLINE,
    )


async def apply_gateway_status(
    session: AsyncSession,
    registry: JobRegistry,
    checkout: OnlineCheckout,
    status: GatewaySessionStatus,
    *,
    now: datetime,
) -> None:
    """Reporte l'état Cartflox sur le paiement. Idempotent : seul un paiement en attente bouge.

    `checkout` doit être verrouillé (SELECT … FOR UPDATE) : le retour du client et la
    vérification en arrière-plan peuvent arriver en même temps.
    """
    if checkout.status != CheckoutStatus.PENDING.value:
        return
    old_status = checkout.status
    if status.status is GatewayStatus.SUCCESS and status.paid:
        currency_ok = status.currency is None or status.currency.upper() == checkout.currency
        amount_ok = status.amount is None or status.amount >= checkout.amount
        if not (currency_ok and amount_ok):
            # Ne devrait jamais arriver (le montant est fixé par nous) : l'équipe vérifie à la main
            logger.error(
                "paiement Cartflox incohérent",
                extra={
                    "checkout_id": str(checkout.id),
                    "expected": checkout.amount,
                    "received": status.amount,
                    "currency": status.currency,
                },
            )
            checkout.status = CheckoutStatus.FAILED.value
        else:
            checkout.provider = status.provider
            checkout.provider_reference = status.provider_reference
            checkout.provider_order_id = status.order_id or checkout.provider_order_id
            payment = await _record_payment(session, registry, checkout, now)
            checkout.payment_id = payment.id
            checkout.status = CheckoutStatus.PAID.value
    elif status.status is GatewayStatus.FAILED:
        checkout.status = CheckoutStatus.FAILED.value
    elif status.status in (GatewayStatus.CANCELLED, GatewayStatus.REFUNDED):
        checkout.status = CheckoutStatus.CANCELLED.value
    if checkout.status != old_status:
        await record_audit(
            session,
            actor=PROVIDER_ACTOR,
            action="online_checkout.status_changed",
            entity_type="online_checkout",
            entity_id=checkout.id,
            organization_id=checkout.organization_id,
            old_value={"status": old_status},
            new_value={
                "status": checkout.status,
                "provider_status": status.status.value,
                "provider": status.provider,
            },
        )
    await session.flush()


async def _locked(session: AsyncSession, checkout_id: uuid.UUID) -> OnlineCheckout | None:
    return (
        await session.execute(
            select(OnlineCheckout).where(OnlineCheckout.id == checkout_id).with_for_update()
        )
    ).scalar_one_or_none()


class OnlinePaymentService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        registry: JobRegistry,
        gateway: PaymentGateway | None,
        *,
        app_url: str,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry
        self._gateway = gateway
        self._app_url = app_url

    async def is_available(self, context: TenantContext) -> bool:
        """Paiement en ligne proposé à ce client (clé configurée et devise prise en charge)."""
        if self._gateway is None:
            return False
        async with tenant_transaction(self._session_factory, context) as session:
            catalog = await current_catalog(session)
            country = await session.scalar(
                select(Organization.country).where(Organization.id == context.organization_id)
            )
        return catalog.currency_for_country(country).value in ONLINE_CURRENCIES

    async def start(
        self, context: TenantContext, user_id: uuid.UUID, *, product_code: str
    ) -> CheckoutView:
        if self._gateway is None:
            raise _not_enabled()
        async with tenant_transaction(self._session_factory, context) as session:
            catalog = await current_catalog(session)
            product = catalog.product(product_code)
            if product is None or not product.public:
                raise AppError("PRODUCT_NOT_AVAILABLE", "Cette offre n'existe pas.", status=422)
            organization = (
                await session.execute(
                    select(Organization).where(Organization.id == context.organization_id)
                )
            ).scalar_one()
            currency = catalog.currency_for_country(organization.country)
            price = catalog.price_for(product, currency)
            if price is None:
                raise AppError(
                    "PRODUCT_NOT_AVAILABLE",
                    "Cette offre n'est pas encore proposée dans votre devise.",
                    status=422,
                )
            if currency.value not in ONLINE_CURRENCIES:
                raise AppError(
                    "ONLINE_PAYMENT_UNAVAILABLE",
                    "Le paiement en ligne n'est proposé qu'en FCFA (XOF) pour l'instant : "
                    "utilisez « Je veux démarrer », un conseiller vous contacte.",
                    status=422,
                )
            if price.period is BillingPeriod.NONE and await self._already_paid(
                session, context.organization_id, product_code
            ):
                raise AppError("ALREADY_PURCHASED", "Vous avez déjà payé cette offre.", status=409)
            reusable = await self._recent_pending(session, context.organization_id, product_code)
            if reusable is not None:
                return await _view(session, reusable)

            customer = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
            checkout = OnlineCheckout(
                organization_id=context.organization_id,
                requested_by=user_id,
                product_code=product_code,
                amount=price.amount,
                currency=currency.value,
                period=price.period.value,
            )
            session.add(checkout)
            await session.flush()
            await record_audit(
                session,
                actor=Actor.user(user_id),
                action="online_checkout.start",
                entity_type="online_checkout",
                entity_id=checkout.id,
                organization_id=context.organization_id,
                new_value={"product_code": product_code, "amount": price.amount},
            )
            checkout_id = checkout.id
            request: dict[str, Any] = {
                "amount": price.amount,
                "currency": currency.value,
                "description": f"BENILAB Digital360 — {product.name}",
                "customer_email": customer.email,
                "customer_name": customer.full_name,
            }

        # Appel réseau hors transaction : la ligne existe déjà, une panne la passe en FAILED
        return_url = f"{self._app_url}dashboard.html?paiement={checkout_id}"
        try:
            gateway_session = await self._gateway.create_session(
                **request,
                success_url=return_url,
                cancel_url=return_url + "&annule=1",
                # app : permet aux autres projets du même espace Cartflox d'ignorer ce paiement
                metadata={
                    "app": "digital360",
                    "checkout_id": str(checkout_id),
                    "organization_id": str(context.organization_id),
                },
                idempotency_key=f"digital360-{checkout_id}",
            )
        except PaymentGatewayError:
            logger.exception("création de la session Cartflox impossible")
            async with tenant_transaction(self._session_factory, context) as session:
                failed = await session.get(OnlineCheckout, checkout_id)
                if failed is not None:
                    failed.status = CheckoutStatus.FAILED.value
            raise AppError(
                "PAYMENT_PROVIDER_ERROR",
                "Le service de paiement ne répond pas. Réessayez dans un instant ou utilisez "
                "« Je veux démarrer ».",
                status=502,
            ) from None

        async with tenant_transaction(self._session_factory, context) as session:
            checkout = (
                await session.execute(
                    select(OnlineCheckout).where(OnlineCheckout.id == checkout_id)
                )
            ).scalar_one()
            checkout.provider_session_id = gateway_session.id
            checkout.provider_order_id = gateway_session.order_id
            checkout.checkout_url = gateway_session.url
            await _schedule_check(session, checkout, attempt=0)
            await session.flush()
            return await _view(session, checkout)

    async def refresh(self, context: TenantContext, checkout_id: uuid.UUID) -> CheckoutView:
        """Retour du client sur son espace : Cartflox est interrogé si le paiement est en attente."""
        async with tenant_transaction(self._session_factory, context) as session:
            checkout = (
                await session.execute(
                    select(OnlineCheckout).where(
                        OnlineCheckout.id == checkout_id,
                        OnlineCheckout.organization_id == context.organization_id,
                    )
                )
            ).scalar_one_or_none()
            if checkout is None:
                raise AppError("NOT_FOUND", "Paiement introuvable.", status=404)
            session_id = checkout.provider_session_id
            if checkout.status != CheckoutStatus.PENDING.value or session_id is None:
                return await _view(session, checkout)
        if self._gateway is not None:
            try:
                status = await self._gateway.get_status(session_id)
            except PaymentGatewayError:
                # Le paiement reste en attente : la vérification en arrière-plan prendra le relais
                logger.warning("statut Cartflox indisponible", exc_info=True)
            else:
                async with staff_transaction(self._session_factory) as session:
                    locked = await _locked(session, checkout_id)
                    if locked is not None:
                        await apply_gateway_status(
                            session, self._registry, locked, status, now=datetime.now(UTC)
                        )
        async with tenant_transaction(self._session_factory, context) as session:
            checkout = (
                await session.execute(
                    select(OnlineCheckout).where(OnlineCheckout.id == checkout_id)
                )
            ).scalar_one()
            return await _view(session, checkout)

    @staticmethod
    async def _already_paid(
        session: AsyncSession, organization_id: uuid.UUID, product_code: str
    ) -> bool:
        paid = await session.scalar(
            select(Payment.id)
            .where(Payment.organization_id == organization_id, Payment.product_code == product_code)
            .limit(1)
        )
        return paid is not None

    @staticmethod
    async def _recent_pending(
        session: AsyncSession, organization_id: uuid.UUID, product_code: str
    ) -> OnlineCheckout | None:
        return (
            await session.execute(
                select(OnlineCheckout)
                .where(
                    OnlineCheckout.organization_id == organization_id,
                    OnlineCheckout.product_code == product_code,
                    OnlineCheckout.status == CheckoutStatus.PENDING.value,
                    OnlineCheckout.checkout_url.is_not(None),
                    OnlineCheckout.created_at >= datetime.now(UTC) - REUSE_WINDOW,
                )
                .order_by(OnlineCheckout.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def _schedule_check(session: AsyncSession, checkout: OnlineCheckout, *, attempt: int) -> None:
    await enqueue(
        session,
        CHECK_CHECKOUT_JOB,
        {"checkout_id": str(checkout.id), "attempt": attempt},
        dedup_key=f"checkout-check:{checkout.id}:{attempt}",
        run_at=datetime.now(UTC) + timedelta(minutes=CHECK_DELAYS_MINUTES[attempt]),
        organization_id=checkout.organization_id,
    )


def register_jobs(registry: JobRegistry, gateway: PaymentGateway | None) -> None:
    if gateway is None:
        return

    @registry.job(CHECK_CHECKOUT_JOB)
    async def check_online_checkout(session: AsyncSession, payload: dict[str, Any]) -> None:
        """Vérification en arrière-plan d'un paiement encore en attente."""
        checkout = await _locked(session, uuid.UUID(payload["checkout_id"]))
        if checkout is None or checkout.status != CheckoutStatus.PENDING.value:
            return
        if checkout.provider_session_id is not None:
            try:
                status = await gateway.get_status(checkout.provider_session_id)
            except PaymentGatewayError:
                logger.warning("statut Cartflox indisponible", exc_info=True)
            else:
                await apply_gateway_status(
                    session, registry, checkout, status, now=datetime.now(UTC)
                )
        if checkout.status != CheckoutStatus.PENDING.value:
            return
        next_attempt = int(payload["attempt"]) + 1
        if next_attempt < len(CHECK_DELAYS_MINUTES):
            await _schedule_check(session, checkout, attempt=next_attempt)
            return
        checkout.status = CheckoutStatus.EXPIRED.value
        await record_audit(
            session,
            actor=Actor.system(CHECK_CHECKOUT_JOB),
            action="online_checkout.expired",
            entity_type="online_checkout",
            entity_id=checkout.id,
            organization_id=checkout.organization_id,
            old_value={"status": CheckoutStatus.PENDING.value},
            new_value={"status": checkout.status},
        )
