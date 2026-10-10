"""Demandes d'achat « Je veux démarrer » (passerelle manuelle avant le paiement en ligne, M5).

Le client choisit une offre ; la demande fige le prix HT du catalogue dans la devise de
son pays et prévient l'équipe commerciale. L'équipe encaisse hors ligne, passe la demande
à WON, puis active l'offre par un droit manuel (entitlement override).
"""

import html
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.actor import Actor
from digital360.core.audit import record_audit
from digital360.core.email import EmailMessage, EmailSender
from digital360.core.errors import AppError
from digital360.core.jobs import JobRegistry, publish
from digital360.core.pagination import PageInfo, PageParams, build_page_info
from digital360.core.permissions import ClientRole
from digital360.core.tenancy import TenantContext, staff_transaction, tenant_transaction
from digital360.modules.billing.infrastructure.models import (
    OPEN_STATUSES,
    ContactChannel,
    Payment,
    PaymentChannel,
    PaymentMethod,
    PurchaseRequest,
    PurchaseRequestStatus,
)
from digital360.modules.catalog.application.service import current_catalog
from digital360.modules.catalog.domain.models import Currency, Product
from digital360.modules.catalog.infrastructure.models import EntitlementOverride
from digital360.modules.diagnostics.infrastructure.models import ActionPlanItem, PlanItemStatus
from digital360.modules.identity.infrastructure.models import Membership, MembershipStatus, User
from digital360.modules.organizations.infrastructure.models import (
    Organization,
    OrganizationStatus,
)

PURCHASE_REQUEST_CREATED_EVENT = "purchase_request.created"
ALERT_SALES_JOB = "billing.alert_sales_purchase_request"
SEND_RECEIPT_JOB = "billing.send_payment_receipt"
# Vente gagnée : les modules de livraison (projets) s'y abonnent
PURCHASE_REQUEST_WON_EVENT = "purchase_request.won"
# Paiement enregistré (manuel ou en ligne) : envoi du reçu au client
PAYMENT_RECORDED_EVENT = "payment.recorded"

S = PurchaseRequestStatus
# Traitement par l'équipe : WON et LOST sont définitifs (une nouvelle demande reste possible)
ALLOWED_TRANSITIONS: dict[PurchaseRequestStatus, set[PurchaseRequestStatus]] = {
    S.NEW: {S.CONTACTED, S.WON, S.LOST},
    S.CONTACTED: {S.WON, S.LOST},
    S.WON: set(),
    S.LOST: set(),
}
# Effet sur les recommandations de l'offre : gagnée = en cours de réalisation, perdue = de nouveau
# proposées
PLAN_ITEM_STATUS_ON_CLOSE = {S.WON: PlanItemStatus.IN_PROGRESS, S.LOST: PlanItemStatus.PROPOSED}


def _contact_number(
    channel: ContactChannel, given: str | None, organization: Organization, requester: User
) -> str | None:
    """Numéro à rappeler : celui saisi, sinon ceux déjà connus (fiche entreprise puis compte)."""
    if channel is ContactChannel.EMAIL:
        return None
    if channel is ContactChannel.WHATSAPP:
        known = [organization.whatsapp, organization.phone, requester.phone]
    else:
        known = [organization.phone, organization.whatsapp, requester.phone]
    number = next((candidate for candidate in [given, *known] if candidate), None)
    if number is None:
        raise AppError(
            "CONTACT_NUMBER_REQUIRED",
            "Indiquez un numéro pour être recontacté.",
            status=422,
            errors=[{"field": "contact_number", "reason": "required"}],
        )
    return number


async def _move_plan_items(
    session: AsyncSession,
    organization_id: uuid.UUID,
    product_code: str,
    *,
    from_statuses: set[PlanItemStatus],
    to: PlanItemStatus,
) -> None:
    """Fait avancer ensemble toutes les recommandations de l'entreprise couvertes par l'offre."""
    await session.execute(
        update(ActionPlanItem)
        .where(
            ActionPlanItem.organization_id == organization_id,
            ActionPlanItem.product_code == product_code,
            ActionPlanItem.status.in_([status.value for status in from_statuses]),
        )
        .values(status=to.value)
    )


@dataclass(frozen=True)
class PurchaseRequestView:
    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    product_code: str
    product_name: str
    plan_item_id: uuid.UUID | None
    price: dict[str, Any] | None
    channel: str
    contact_number: str | None
    message: str | None
    status: str
    staff_note: str | None
    requested_by_name: str
    requested_by_email: str
    requested_by_phone: str | None
    created_at: datetime
    updated_at: datetime
    # Encaissement de la vente gagnée : {amount, currency, method, reference, received_on}
    payment: dict[str, Any] | None = None


def _payment_payload(payment: Payment | None) -> dict[str, Any] | None:
    if payment is None:
        return None
    return {
        "amount": payment.amount,
        "currency": payment.currency,
        "method": payment.method,
        "channel": payment.channel,
        "reference": payment.reference,
        "received_on": payment.received_on.isoformat(),
    }


def _view(
    request: PurchaseRequest,
    organization: Organization,
    user: User,
    product_name: str,
    payment: Payment | None = None,
) -> PurchaseRequestView:
    price = (
        {"amount": request.amount, "currency": request.currency, "period": request.period}
        if request.amount is not None
        else None
    )
    return PurchaseRequestView(
        id=request.id,
        organization_id=request.organization_id,
        organization_name=organization.commercial_name,
        product_code=request.product_code,
        product_name=product_name,
        plan_item_id=request.plan_item_id,
        price=price,
        channel=request.channel,
        contact_number=request.contact_number,
        message=request.message,
        status=request.status,
        staff_note=request.staff_note,
        requested_by_name=user.full_name,
        requested_by_email=user.email,
        requested_by_phone=user.phone,
        created_at=request.created_at,
        updated_at=request.updated_at,
        payment=_payment_payload(payment),
    )


async def _load_views(
    session: AsyncSession, requests: list[PurchaseRequest]
) -> list[PurchaseRequestView]:
    if not requests:
        return []
    catalog = await current_catalog(session)
    organizations = {
        org.id: org
        for org in (
            await session.execute(
                select(Organization).where(
                    Organization.id.in_({item.organization_id for item in requests})
                )
            )
        ).scalars()
    }
    users = {
        user.id: user
        for user in (
            await session.execute(
                select(User).where(User.id.in_({item.requested_by for item in requests}))
            )
        ).scalars()
    }

    payments = {
        payment.purchase_request_id: payment
        for payment in (
            await session.execute(
                select(Payment).where(
                    Payment.purchase_request_id.in_({item.id for item in requests})
                )
            )
        ).scalars()
    }

    def product_name(code: str) -> str:
        product = catalog.product(code)
        return product.name if product else code

    return [
        _view(
            item,
            organizations[item.organization_id],
            users[item.requested_by],
            product_name(item.product_code),
            payments.get(item.id),
        )
        for item in requests
    ]


async def open_request(
    session: AsyncSession, organization_id: uuid.UUID, product_code: str
) -> PurchaseRequest | None:
    """Demande « Je veux démarrer » encore en cours pour cette offre, s'il y en a une."""
    return (
        await session.execute(
            select(PurchaseRequest).where(
                PurchaseRequest.organization_id == organization_id,
                PurchaseRequest.product_code == product_code,
                PurchaseRequest.status.in_([status.value for status in OPEN_STATUSES]),
            )
        )
    ).scalar_one_or_none()


async def ensure_not_already_sold(
    session: AsyncSession, organization_id: uuid.UUID, product_code: str, period: str | None
) -> None:
    """Empêche de faire payer deux fois : une offre ponctuelle (Start) ne se vend qu'une fois,
    et un abonnement en cours se prolonge par un renouvellement, pas par une nouvelle vente."""
    if period in (None, "NONE"):
        paid = await session.scalar(
            select(Payment.id)
            .where(Payment.organization_id == organization_id, Payment.product_code == product_code)
            .limit(1)
        )
        if paid is not None:
            raise AppError(
                "ALREADY_PURCHASED",
                "Cette offre a déjà été payée pour cette entreprise.",
                status=409,
            )
        return
    covered_until = await session.scalar(
        select(func.max(Payment.covers_until)).where(
            Payment.organization_id == organization_id, Payment.product_code == product_code
        )
    )
    if covered_until is not None and covered_until > datetime.now(UTC):
        raise AppError(
            "SUBSCRIPTION_ACTIVE",
            f"Cet abonnement est déjà payé jusqu'au {covered_until.strftime('%d/%m/%Y')} : "
            "il se prolonge par un renouvellement, pas par une nouvelle vente.",
            status=409,
        )


@dataclass(frozen=True)
class PaymentInput:
    method: PaymentMethod
    # Montant réellement reçu (HT, unité mineure de la devise de la vente) ; None = prix de l'offre
    amount: int | None
    reference: str | None
    received_on: date


def _payment_required() -> AppError:
    return AppError(
        "PAYMENT_REQUIRED",
        "Indiquez le paiement reçu (moyen, montant, référence) pour valider la vente.",
        status=422,
        errors=[{"field": "payment", "reason": "required"}],
    )


def _product_not_available(detail: str) -> AppError:
    return AppError("PRODUCT_NOT_AVAILABLE", detail, status=422)


# Durée d'un droit accordé pour un abonnement payé hors ligne : sans renouvellement payé,
# l'accès s'arrête de lui-même (le paiement en ligne, M5, prendra le relais)
SUBSCRIPTION_PERIOD = {"MONTH": timedelta(days=31), "YEAR": timedelta(days=366)}


async def mark_organization_active(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """Premier achat : le prospect (LEAD) devient client (ACTIVE). Les autres statuts ne bougent pas."""
    await session.execute(
        update(Organization)
        .where(
            Organization.id == organization_id,
            Organization.status == OrganizationStatus.LEAD.value,
        )
        .values(status=OrganizationStatus.ACTIVE.value)
    )


async def grant_product_entitlements(
    session: AsyncSession,
    organization_id: uuid.UUID,
    product: Product,
    *,
    expires_at: datetime | None,
    reason: str,
    staff_user_id: uuid.UUID,
) -> None:
    """Accorde les droits d'une offre (vente gagnée ou renouvellement), dans la transaction."""
    for key, value in product.entitlements.items():
        session.add(
            EntitlementOverride(
                organization_id=organization_id,
                entitlement_key=key,
                value=value,
                reason=reason,
                granted_by=staff_user_id,
                expires_at=expires_at,
            )
        )


async def _activate_offer(
    session: AsyncSession, request: PurchaseRequest, *, actor: Actor, granted_by: uuid.UUID
) -> datetime | None:
    """Vente gagnée : accorde les droits de l'offre. Renvoie la fin de la période payée
    (abonnement), ou None pour une offre ponctuelle."""
    catalog = await current_catalog(session)
    product = catalog.product(request.product_code)
    period = SUBSCRIPTION_PERIOD.get(request.period or "")
    expires_at = datetime.now(UTC) + period if period else None
    if product is None or not product.entitlements:
        return expires_at
    await grant_product_entitlements(
        session,
        request.organization_id,
        product,
        expires_at=expires_at,
        reason=f"Offre {product.name} : demande d'achat {request.id} gagnée",
        staff_user_id=granted_by,
    )
    await record_audit(
        session,
        actor=actor,
        action="purchase_request.activate_offer",
        entity_type="purchase_request",
        entity_id=request.id,
        organization_id=request.organization_id,
        new_value={
            "product_code": product.code,
            "entitlements": dict(product.entitlements),
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )
    return expires_at


async def close_sale_as_won(
    session: AsyncSession,
    registry: JobRegistry,
    request: PurchaseRequest,
    payment: PaymentInput,
    *,
    actor: Actor,
    granted_by: uuid.UUID,
    recorded_by: uuid.UUID | None,
    channel: PaymentChannel,
) -> Payment:
    """Vente gagnée : paiement enregistré, offre activée, client actif, livraison lancée.

    Commun à l'encaissement manuel (l'équipe) et au paiement en ligne (confirmé par Cartflox).
    """
    await _move_plan_items(
        session,
        request.organization_id,
        request.product_code,
        from_statuses={PlanItemStatus.PROPOSED, PlanItemStatus.ACCEPTED},
        to=PlanItemStatus.IN_PROGRESS,
    )
    recorded = Payment(
        organization_id=request.organization_id,
        purchase_request_id=request.id,
        product_code=request.product_code,
        amount=payment.amount if payment.amount is not None else (request.amount or 0),
        currency=request.currency,
        method=payment.method.value,
        channel=channel.value,
        reference=payment.reference,
        received_on=payment.received_on,
        recorded_by=recorded_by,
    )
    session.add(recorded)
    await session.flush()
    recorded.covers_until = await _activate_offer(
        session, request, actor=actor, granted_by=granted_by
    )
    await mark_organization_active(session, request.organization_id)
    await record_audit(
        session,
        actor=actor,
        action="payment.record",
        entity_type="payment",
        entity_id=recorded.id,
        organization_id=request.organization_id,
        new_value={
            "amount": recorded.amount,
            "currency": request.currency,
            "method": payment.method.value,
            "channel": channel.value,
            "reference": payment.reference,
        },
    )
    await publish(
        session,
        registry,
        PURCHASE_REQUEST_WON_EVENT,
        {
            "purchase_request_id": str(request.id),
            "organization_id": str(request.organization_id),
            "product_code": request.product_code,
        },
        organization_id=request.organization_id,
    )
    await publish(
        session,
        registry,
        PAYMENT_RECORDED_EVENT,
        {"payment_id": str(recorded.id)},
        organization_id=request.organization_id,
    )
    return recorded


class PurchaseRequestService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], registry: JobRegistry
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry

    async def billing_currency(self, context: TenantContext) -> Currency:
        """Devise imposée à l'entreprise : prix Europe dès qu'un de ses numéros est hors
        d'Afrique, sinon devise de son pays. Le client ne la choisit jamais."""
        async with tenant_transaction(self._session_factory, context) as session:
            catalog = await current_catalog(session)
            organization = (
                await session.execute(
                    select(Organization).where(Organization.id == context.organization_id)
                )
            ).scalar_one()
        return catalog.currency_for(
            organization.country, (organization.phone, organization.whatsapp)
        )

    async def create(
        self,
        context: TenantContext,
        user_id: uuid.UUID,
        *,
        product_code: str,
        plan_item_id: uuid.UUID | None,
        channel: ContactChannel,
        contact_number: str | None,
        message: str | None,
    ) -> tuple[PurchaseRequestView, bool]:
        """Renvoie la demande et True si elle vient d'être créée (False : déjà en cours)."""
        actor = Actor.user(user_id)
        async with tenant_transaction(self._session_factory, context) as session:
            existing = await self._open_request(session, context.organization_id, product_code)
            if existing is not None:
                return (await _load_views(session, [existing]))[0], False

            catalog = await current_catalog(session)
            product = catalog.product(product_code)
            if product is None or not product.public:
                raise _product_not_available("Cette offre n'existe pas.")
            organization = (
                await session.execute(
                    select(Organization).where(Organization.id == context.organization_id)
                )
            ).scalar_one()
            currency = catalog.currency_for(
                organization.country, (organization.phone, organization.whatsapp)
            )
            price = catalog.price_for(product, currency)
            if price is None:
                raise _product_not_available(
                    "Cette offre n'est pas encore proposée dans votre devise."
                )
            await ensure_not_already_sold(
                session, context.organization_id, product_code, price.period.value
            )

            requester = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
            number = _contact_number(channel, contact_number, organization, requester)

            if plan_item_id is not None:
                await self._check_plan_item(session, context, plan_item_id, product_code)
            # Une offre couvre toutes ses recommandations : elles avancent ensemble
            await _move_plan_items(
                session,
                context.organization_id,
                product_code,
                from_statuses={PlanItemStatus.PROPOSED},
                to=PlanItemStatus.ACCEPTED,
            )

            request = PurchaseRequest(
                organization_id=context.organization_id,
                requested_by=user_id,
                product_code=product_code,
                plan_item_id=plan_item_id,
                amount=price.amount,
                currency=price.currency.value,
                period=price.period.value,
                channel=channel.value,
                contact_number=number,
                message=message,
            )
            try:
                # Point de sauvegarde : un double clic simultané heurte l'index unique
                async with session.begin_nested():
                    session.add(request)
                    await session.flush()
            except IntegrityError:
                existing = await self._open_request(session, context.organization_id, product_code)
                if existing is None:
                    raise
                return (await _load_views(session, [existing]))[0], False

            await record_audit(
                session,
                actor=actor,
                action="purchase_request.create",
                entity_type="purchase_request",
                entity_id=request.id,
                organization_id=context.organization_id,
                new_value={"product_code": product_code, "amount": price.amount},
            )
            await publish(
                session,
                self._registry,
                PURCHASE_REQUEST_CREATED_EVENT,
                {"purchase_request_id": str(request.id)},
                organization_id=context.organization_id,
            )
            await session.refresh(request)
            return (await _load_views(session, [request]))[0], True

    async def list_for_organization(self, context: TenantContext) -> list[PurchaseRequestView]:
        async with tenant_transaction(self._session_factory, context) as session:
            requests = (
                await session.execute(
                    select(PurchaseRequest)
                    .where(PurchaseRequest.organization_id == context.organization_id)
                    .order_by(PurchaseRequest.id.desc())
                )
            ).scalars()
            return await _load_views(session, list(requests))

    async def admin_list(
        self, params: PageParams, *, status: PurchaseRequestStatus | None
    ) -> tuple[list[PurchaseRequestView], PageInfo]:
        statement = (
            select(PurchaseRequest).order_by(PurchaseRequest.id.desc()).limit(params.limit + 1)
        )
        if params.after is not None:
            statement = statement.where(PurchaseRequest.id < params.after)
        if status is not None:
            statement = statement.where(PurchaseRequest.status == status.value)
        async with staff_transaction(self._session_factory) as session:
            requests = list((await session.execute(statement)).scalars())
            kept, page = build_page_info([item.id for item in requests], params)
            return await _load_views(session, requests[:kept]), page

    async def admin_stats(self) -> dict[str, Any]:
        """Entonnoir de vente et chiffre d'affaires gagné (montants HT figés à la demande)."""
        month_ago = datetime.now(UTC) - timedelta(days=30)
        async with staff_transaction(self._session_factory) as session:
            by_status = dict(
                (
                    await session.execute(
                        select(PurchaseRequest.status, func.count()).group_by(
                            PurchaseRequest.status
                        )
                    )
                )
                .tuples()
                .all()
            )
            won = PurchaseRequest.status == S.WON.value
            won_last_30_days = (
                await session.scalar(
                    select(func.count())
                    .select_from(PurchaseRequest)
                    .where(won, PurchaseRequest.updated_at >= month_ago)
                )
                or 0
            )
            # Montants réellement encaissés (un geste commercial réduit le chiffre d'affaires)
            revenue = (
                await session.execute(
                    select(
                        Payment.currency,
                        func.sum(Payment.amount),
                        func.sum(Payment.amount).filter(Payment.received_on >= month_ago.date()),
                    )
                    .group_by(Payment.currency)
                    .order_by(Payment.currency)
                )
            ).all()
        won_count = by_status.get(S.WON.value, 0)
        closed = won_count + by_status.get(S.LOST.value, 0)
        return {
            "new": by_status.get(S.NEW.value, 0),
            "contacted": by_status.get(S.CONTACTED.value, 0),
            "won": won_count,
            "lost": by_status.get(S.LOST.value, 0),
            "won_last_30_days": won_last_30_days,
            # Part des demandes closes qui ont abouti à une vente (0 à 1), null si aucune close
            "win_rate": round(won_count / closed, 3) if closed else None,
            "revenue": [
                {"currency": currency, "total": int(total or 0), "last_30_days": int(recent or 0)}
                for currency, total, recent in revenue
            ],
        }

    async def admin_update(
        self,
        request_id: uuid.UUID,
        staff_user_id: uuid.UUID,
        *,
        status: PurchaseRequestStatus | None,
        staff_note: str | None,
        payment: PaymentInput | None = None,
    ) -> PurchaseRequestView:
        actor = Actor.user(staff_user_id)
        async with staff_transaction(self._session_factory) as session:
            request = (
                await session.execute(
                    select(PurchaseRequest)
                    .where(PurchaseRequest.id == request_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if request is None:
                raise AppError("NOT_FOUND", "Demande introuvable.", status=404)
            old = {"status": request.status, "staff_note": request.staff_note}

            if status is not None and status.value != request.status:
                current = PurchaseRequestStatus(request.status)
                if status not in ALLOWED_TRANSITIONS[current]:
                    raise AppError(
                        "INVALID_TRANSITION",
                        f"Une demande {current.value} ne peut pas passer à {status.value}.",
                        status=409,
                    )
                if status is S.WON and payment is None:
                    raise _payment_required()
                if status is S.WON:
                    await ensure_not_already_sold(
                        session, request.organization_id, request.product_code, request.period
                    )
                request.status = status.value
                request.handled_by = staff_user_id
                if status is S.WON and payment is not None:
                    await self._close_as_won(session, request, staff_user_id, payment)
                elif status in PLAN_ITEM_STATUS_ON_CLOSE:
                    await _move_plan_items(
                        session,
                        request.organization_id,
                        request.product_code,
                        from_statuses={PlanItemStatus.PROPOSED, PlanItemStatus.ACCEPTED},
                        to=PLAN_ITEM_STATUS_ON_CLOSE[status],
                    )
            if staff_note is not None:
                request.staff_note = staff_note or None

            await record_audit(
                session,
                actor=actor,
                action="purchase_request.update",
                entity_type="purchase_request",
                entity_id=request.id,
                organization_id=request.organization_id,
                old_value=old,
                new_value={"status": request.status, "staff_note": request.staff_note},
            )
            await session.flush()
            await session.refresh(request)
            return (await _load_views(session, [request]))[0]

    async def admin_void_duplicate(
        self, request_id: uuid.UUID, staff_user_id: uuid.UUID, *, reason: str
    ) -> PurchaseRequestView:
        """Annule le paiement d'une vente payée en double (offre déjà payée par ailleurs).

        Le paiement est retiré (le chiffre d'affaires est corrigé), la demande passe à LOST
        et un projet de site en double pas encore démarré est supprimé. Tout est tracé.
        """
        async with staff_transaction(self._session_factory) as session:
            request = (
                await session.execute(
                    select(PurchaseRequest)
                    .where(PurchaseRequest.id == request_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if request is None:
                raise AppError("NOT_FOUND", "Demande introuvable.", status=404)
            payment = await session.scalar(
                select(Payment).where(Payment.purchase_request_id == request.id)
            )
            if request.status != S.WON.value or payment is None:
                raise AppError(
                    "NOT_PAID", "Cette demande n'a pas de paiement à annuler.", status=409
                )
            other = await session.scalar(
                select(func.count())
                .select_from(Payment)
                .where(
                    Payment.organization_id == request.organization_id,
                    Payment.product_code == request.product_code,
                    Payment.id != payment.id,
                )
            )
            if not other:
                raise AppError(
                    "NOT_A_DUPLICATE",
                    "C'est le seul paiement de cette offre pour ce client : ce n'est pas un doublon.",
                    status=409,
                )
            snapshot = _payment_payload(payment)
            await session.delete(payment)
            request.status = S.LOST.value
            previous_note = (request.staff_note + " | ") if request.staff_note else ""
            request.staff_note = previous_note + f"Paiement en double annulé : {reason}"
            request.handled_by = staff_user_id
            # Projet de site créé pour cette vente en double, s'il n'a pas démarré
            removed_projects = (
                await session.execute(
                    text(
                        "DELETE FROM website_projects WHERE purchase_request_id = :id "
                        "AND status = 'BRIEF_PENDING' RETURNING id"
                    ),
                    {"id": request.id},
                )
            ).all()
            await record_audit(
                session,
                actor=Actor.user(staff_user_id),
                action="payment.void_duplicate",
                entity_type="purchase_request",
                entity_id=request.id,
                organization_id=request.organization_id,
                old_value={"status": S.WON.value, "payment": snapshot},
                new_value={
                    "status": request.status,
                    "reason": reason,
                    "website_projects_removed": len(removed_projects),
                },
            )
            await session.flush()
            await session.refresh(request)
            return (await _load_views(session, [request]))[0]

    async def admin_direct_sale(
        self,
        organization_id: uuid.UUID,
        staff_user_id: uuid.UUID,
        *,
        product_code: str,
        payment: PaymentInput,
        note: str | None,
    ) -> PurchaseRequestView:
        """Client qui paie sans être passé par « Je veux démarrer » (téléphone, agence) : la vente
        est enregistrée directement comme gagnée, avec la même activation qu'une demande."""
        async with staff_transaction(self._session_factory) as session:
            organization = await session.get(Organization, organization_id)
            if organization is None or organization.deleted_at is not None:
                raise AppError("NOT_FOUND", "Entreprise introuvable.", status=404)
            if await self._open_request(session, organization_id, product_code) is not None:
                raise AppError(
                    "OPEN_REQUEST_EXISTS",
                    "Ce client a déjà une demande en cours pour cette offre : passez-la à "
                    "« Gagnée » dans l'onglet Demandes.",
                    status=409,
                )
            catalog = await current_catalog(session)
            product = catalog.product(product_code)
            if product is None or not product.public:
                raise _product_not_available("Cette offre n'existe pas.")
            price = catalog.price_for(
                product,
                catalog.currency_for(
                    organization.country, (organization.phone, organization.whatsapp)
                ),
            )
            if price is None:
                raise _product_not_available(
                    "Cette offre n'est pas proposée dans la devise du client."
                )
            await ensure_not_already_sold(
                session, organization_id, product_code, price.period.value
            )
            request = PurchaseRequest(
                organization_id=organization_id,
                requested_by=staff_user_id,
                product_code=product_code,
                amount=price.amount,
                currency=price.currency.value,
                period=price.period.value,
                channel=ContactChannel.EMAIL.value,
                message="Vente enregistrée directement par l'équipe",
                status=S.WON.value,
                staff_note=note,
                handled_by=staff_user_id,
            )
            session.add(request)
            await session.flush()
            await self._close_as_won(session, request, staff_user_id, payment)
            await record_audit(
                session,
                actor=Actor.user(staff_user_id),
                action="purchase_request.direct_sale",
                entity_type="purchase_request",
                entity_id=request.id,
                organization_id=organization_id,
                new_value={"product_code": product_code, "amount": request.amount},
            )
            await session.flush()
            await session.refresh(request)
            return (await _load_views(session, [request]))[0]

    async def _close_as_won(
        self,
        session: AsyncSession,
        request: PurchaseRequest,
        staff_user_id: uuid.UUID,
        payment: PaymentInput,
    ) -> None:
        await close_sale_as_won(
            session,
            self._registry,
            request,
            payment,
            actor=Actor.user(staff_user_id),
            granted_by=staff_user_id,
            recorded_by=staff_user_id,
            channel=PaymentChannel.MANUAL,
        )

    @staticmethod
    async def _open_request(
        session: AsyncSession, organization_id: uuid.UUID, product_code: str
    ) -> PurchaseRequest | None:
        return await open_request(session, organization_id, product_code)

    @staticmethod
    async def _check_plan_item(
        session: AsyncSession, context: TenantContext, item_id: uuid.UUID, product_code: str
    ) -> None:
        item = (
            await session.execute(
                select(ActionPlanItem).where(
                    ActionPlanItem.id == item_id,
                    ActionPlanItem.organization_id == context.organization_id,
                )
            )
        ).scalar_one_or_none()
        if item is None:
            raise AppError("NOT_FOUND", "Recommandation introuvable.", status=404)
        if item.product_code != product_code:
            raise AppError(
                "VALIDATION_ERROR",
                "Cette recommandation ne correspond pas à l'offre choisie.",
                status=422,
                errors=[{"field": "plan_item_id", "reason": "product_mismatch"}],
            )


# ── Alerte à l'équipe commerciale ──


def format_price(price: dict[str, Any] | None) -> str:
    if price is None:
        return "—"
    amount = price["amount"]
    text = (
        f"{amount / 100:,.2f} € HT".replace(",", " ").replace(".", ",")
        if price["currency"] == "EUR"
        else f"{amount:,} FCFA HT".replace(",", " ")
    )
    return text + (" / mois" if price["period"] == "MONTH" else "")


CHANNEL_LABELS = {"WHATSAPP": "WhatsApp", "PHONE": "Téléphone", "EMAIL": "Email"}


def _alert_email(view: PurchaseRequestView, recipients: list[str], admin_url: str) -> EmailMessage:
    lines = [
        ("Entreprise", view.organization_name),
        ("Offre", view.product_name),
        ("Prix", format_price(view.price)),
        ("Contact", view.requested_by_name),
        ("Email", view.requested_by_email),
        ("Téléphone", view.requested_by_phone or "—"),
        ("Être recontacté par", CHANNEL_LABELS.get(view.channel, view.channel)),
        ("Numéro à contacter", view.contact_number or "—"),
        ("Message", view.message or "—"),
    ]
    text = (
        "Un client veut démarrer une offre.\n\n"
        + "\n".join(f"{label} : {value}" for label, value in lines)
        + f"\n\nTraiter la demande : {admin_url}\n"
    )
    # Valeurs saisies par le client : échappées dans la version HTML
    rows = "".join(
        f"<tr><td><strong>{html.escape(label)}</strong></td><td>{html.escape(value)}</td></tr>"
        for label, value in lines
    )
    body = (
        "<p>Un client veut démarrer une offre.</p>"
        f"<table>{rows}</table>"
        f'<p><a href="{html.escape(admin_url, quote=True)}">Traiter la demande</a></p>'
    )
    return EmailMessage(
        to=recipients,
        subject=f"Demande d'achat : {view.product_name} — {view.organization_name}",
        text=text,
        html=body,
    )


def receipt_number(payment: Payment) -> str:
    return "REC-" + payment.received_on.strftime("%Y%m") + "-" + payment.id.hex[-6:].upper()


METHOD_LABELS = {
    "ORANGE_MONEY": "Orange Money",
    "MTN_MOMO": "MTN Mobile Money",
    "MOOV_MONEY": "Moov Money",
    "WAVE": "Wave",
    "BANK_TRANSFER": "Virement bancaire",
    "CASH": "Espèces",
    "CARTFLOX": "Paiement en ligne (Cartflox)",
}


async def owner_emails(session: AsyncSession, organization_id: uuid.UUID) -> list[str]:
    rows = await session.execute(
        select(User.email)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.organization_id == organization_id,
            Membership.role == ClientRole.CLIENT_OWNER.value,
            Membership.status == MembershipStatus.ACTIVE.value,
            User.deleted_at.is_(None),
        )
    )
    return list(rows.scalars())


def _receipt_email(
    payment: Payment, organization: Organization, product_name: str, to: list[str]
) -> EmailMessage:
    number = receipt_number(payment)
    amount = format_price(
        {"amount": payment.amount, "currency": payment.currency, "period": "NONE"}
    )
    lines = [
        ("Reçu n°", number),
        ("Client", organization.legal_name or organization.commercial_name),
        ("Offre", product_name),
        ("Montant reçu", amount),
        ("Moyen de paiement", METHOD_LABELS.get(payment.method, payment.method)),
        ("Référence de la transaction", payment.reference or "—"),
        ("Date du paiement", payment.received_on.strftime("%d/%m/%Y")),
    ]
    intro = "Nous confirmons la réception de votre paiement. Merci pour votre confiance."
    note = "Ce reçu atteste de votre paiement ; il ne remplace pas une facture."
    text = (
        "Bonjour,\n\n"
        + intro
        + "\n\n"
        + "\n".join(f"{label} : {value}" for label, value in lines)
        + "\n\n"
        + note
        + "\n\nL'équipe BENILAB Digital360"
    )
    rows = "".join(
        f"<tr><td><strong>{html.escape(label)}</strong></td><td>{html.escape(value)}</td></tr>"
        for label, value in lines
    )
    body = (
        f"<p>Bonjour,</p><p>{html.escape(intro)}</p><table>{rows}</table>"
        f'<p style="color:#666;font-size:12px">{html.escape(note)}</p>'
        "<p>L'équipe BENILAB Digital360</p>"
    )
    return EmailMessage(
        to=to, subject=f"Reçu de paiement {number} : {product_name}", text=text, html=body
    )


def register_jobs(
    registry: JobRegistry, sender: EmailSender, *, recipients: list[str], admin_url: str
) -> None:
    @registry.on(PURCHASE_REQUEST_CREATED_EVENT, name=ALERT_SALES_JOB)
    async def alert_sales(session: AsyncSession, payload: dict[str, Any]) -> None:
        if not recipients:
            return
        request = await session.get(PurchaseRequest, uuid.UUID(payload["purchase_request_id"]))
        if request is None:
            return
        [view] = await _load_views(session, [request])
        await sender.send(_alert_email(view, recipients, admin_url))

    @registry.on(PAYMENT_RECORDED_EVENT, name=SEND_RECEIPT_JOB)
    async def send_receipt(session: AsyncSession, payload: dict[str, Any]) -> None:
        payment = await session.get(Payment, uuid.UUID(payload["payment_id"]))
        if payment is None:
            return
        organization = await session.get(Organization, payment.organization_id)
        owners = await owner_emails(session, payment.organization_id)
        if organization is None or not owners:
            return
        product = (await current_catalog(session)).product(payment.product_code)
        name = product.name if product else payment.product_code
        await sender.send(_receipt_email(payment, organization, name, owners))
