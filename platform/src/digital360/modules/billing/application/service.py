"""Demandes d'achat « Je veux démarrer » (passerelle manuelle avant le paiement en ligne, M5).

Le client choisit une offre ; la demande fige le prix HT du catalogue dans la devise de
son pays et prévient l'équipe commerciale. L'équipe encaisse hors ligne, passe la demande
à WON, puis active l'offre par un droit manuel (entitlement override).
"""

import html
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.actor import Actor
from digital360.core.audit import record_audit
from digital360.core.email import EmailMessage, EmailSender
from digital360.core.errors import AppError
from digital360.core.jobs import JobRegistry, publish
from digital360.core.pagination import PageInfo, PageParams, build_page_info
from digital360.core.tenancy import TenantContext, staff_transaction, tenant_transaction
from digital360.modules.billing.infrastructure.models import (
    OPEN_STATUSES,
    ContactChannel,
    PurchaseRequest,
    PurchaseRequestStatus,
)
from digital360.modules.catalog.application.service import current_catalog
from digital360.modules.catalog.infrastructure.models import EntitlementOverride
from digital360.modules.diagnostics.infrastructure.models import ActionPlanItem, PlanItemStatus
from digital360.modules.identity.infrastructure.models import User
from digital360.modules.organizations.infrastructure.models import Organization

PURCHASE_REQUEST_CREATED_EVENT = "purchase_request.created"
ALERT_SALES_JOB = "billing.alert_sales_purchase_request"

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


def _view(
    request: PurchaseRequest, organization: Organization, user: User, product_name: str
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

    def product_name(code: str) -> str:
        product = catalog.product(code)
        return product.name if product else code

    return [
        _view(
            item,
            organizations[item.organization_id],
            users[item.requested_by],
            product_name(item.product_code),
        )
        for item in requests
    ]


def _product_not_available(detail: str) -> AppError:
    return AppError("PRODUCT_NOT_AVAILABLE", detail, status=422)


# Durée d'un droit accordé pour un abonnement payé hors ligne : sans renouvellement payé,
# l'accès s'arrête de lui-même (le paiement en ligne, M5, prendra le relais)
SUBSCRIPTION_PERIOD = {"MONTH": timedelta(days=31), "YEAR": timedelta(days=366)}


async def _activate_offer(
    session: AsyncSession, request: PurchaseRequest, staff_user_id: uuid.UUID
) -> None:
    """Vente gagnée : accorde les droits de l'offre achetée, dans la même transaction."""
    catalog = await current_catalog(session)
    product = catalog.product(request.product_code)
    if product is None or not product.entitlements:
        return
    period = SUBSCRIPTION_PERIOD.get(request.period or "")
    expires_at = datetime.now(UTC) + period if period else None
    reason = f"Offre {product.name} : demande d'achat {request.id} gagnée"
    for key, value in product.entitlements.items():
        session.add(
            EntitlementOverride(
                organization_id=request.organization_id,
                entitlement_key=key,
                value=value,
                reason=reason,
                granted_by=staff_user_id,
                expires_at=expires_at,
            )
        )
    await record_audit(
        session,
        actor=Actor.user(staff_user_id),
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


class PurchaseRequestService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], registry: JobRegistry
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry

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
            currency = catalog.currency_for_country(organization.country)
            price = catalog.price_for(product, currency)
            if price is None:
                raise _product_not_available(
                    "Cette offre n'est pas encore proposée dans votre devise."
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

    async def admin_update(
        self,
        request_id: uuid.UUID,
        staff_user_id: uuid.UUID,
        *,
        status: PurchaseRequestStatus | None,
        staff_note: str | None,
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
                request.status = status.value
                request.handled_by = staff_user_id
                if status in PLAN_ITEM_STATUS_ON_CLOSE:
                    await _move_plan_items(
                        session,
                        request.organization_id,
                        request.product_code,
                        from_statuses={PlanItemStatus.PROPOSED, PlanItemStatus.ACCEPTED},
                        to=PLAN_ITEM_STATUS_ON_CLOSE[status],
                    )
                if status is S.WON:
                    await _activate_offer(session, request, staff_user_id)
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

    @staticmethod
    async def _open_request(
        session: AsyncSession, organization_id: uuid.UUID, product_code: str
    ) -> PurchaseRequest | None:
        return (
            await session.execute(
                select(PurchaseRequest).where(
                    PurchaseRequest.organization_id == organization_id,
                    PurchaseRequest.product_code == product_code,
                    PurchaseRequest.status.in_([status.value for status in OPEN_STATUSES]),
                )
            )
        ).scalar_one_or_none()

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


def _format_price(price: dict[str, Any] | None) -> str:
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
        ("Prix", _format_price(view.price)),
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
