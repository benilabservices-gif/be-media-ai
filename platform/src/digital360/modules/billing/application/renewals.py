"""Abonnements payés hors ligne : échéances, rappels et renouvellement.

Un abonnement (Essential, Growth, Performance) payé manuellement couvre 31 jours (366 en
annuel) : `payments.covers_until`. Un contrôle quotidien prévient le client et l'équipe
`REMINDER_DAYS` jours avant l'échéance. L'équipe renouvelle après encaissement : la
nouvelle période part de l'échéance (aucun jour perdu si le client paie en avance).
"""

import html
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.actor import Actor
from digital360.core.audit import record_audit
from digital360.core.email import EmailMessage, EmailSender
from digital360.core.errors import AppError
from digital360.core.jobs import JobRegistry, enqueue, publish
from digital360.core.tenancy import staff_transaction
from digital360.modules.billing.application.service import (
    PAYMENT_RECORDED_EVENT,
    SUBSCRIPTION_PERIOD,
    PaymentInput,
    format_price,
    grant_product_entitlements,
    mark_organization_active,
    owner_emails,
)
from digital360.modules.billing.infrastructure.models import Payment, PaymentChannel
from digital360.modules.catalog.application.service import current_catalog
from digital360.modules.organizations.infrastructure.models import Organization

REMINDER_DAYS = 5
RENEWAL_SWEEP_JOB = "billing.renewal_sweep"
# Heure du contrôle quotidien (UTC = heure d'Abidjan et de Dakar)
SWEEP_TIME = time(7, 0)


@dataclass(frozen=True)
class SubscriptionView:
    organization_id: uuid.UUID
    organization_name: str
    product_code: str
    product_name: str
    covers_until: datetime
    # ACTIVE, EXPIRING (échéance dans moins de REMINDER_DAYS jours) ou EXPIRED
    status: str
    days_left: int
    last_payment: dict[str, Any]


def _status(covers_until: datetime, now: datetime) -> tuple[str, int]:
    days_left = (covers_until.date() - now.date()).days
    if covers_until <= now:
        return "EXPIRED", days_left
    if covers_until - now <= timedelta(days=REMINDER_DAYS):
        return "EXPIRING", days_left
    return "ACTIVE", days_left


async def _latest_payments(
    session: AsyncSession, organization_id: uuid.UUID | None = None
) -> list[Payment]:
    """Dernier paiement d'abonnement par (entreprise, offre) : c'est lui qui fixe l'échéance."""
    statement = (
        select(Payment)
        .where(Payment.covers_until.is_not(None))
        .order_by(Payment.covers_until.desc())
    )
    if organization_id is not None:
        statement = statement.where(Payment.organization_id == organization_id)
    latest: dict[tuple[uuid.UUID, str], Payment] = {}
    for payment in (await session.execute(statement)).scalars():
        latest.setdefault((payment.organization_id, payment.product_code), payment)
    return list(latest.values())


async def _views(
    session: AsyncSession, payments: list[Payment], now: datetime
) -> list[SubscriptionView]:
    if not payments:
        return []
    catalog = await current_catalog(session)
    names = dict(
        (
            await session.execute(
                select(Organization.id, Organization.commercial_name).where(
                    Organization.id.in_({payment.organization_id for payment in payments})
                )
            )
        )
        .tuples()
        .all()
    )
    views = []
    for payment in payments:
        if payment.covers_until is None:
            continue
        status, days_left = _status(payment.covers_until, now)
        product = catalog.product(payment.product_code)
        views.append(
            SubscriptionView(
                organization_id=payment.organization_id,
                organization_name=names.get(payment.organization_id, ""),
                product_code=payment.product_code,
                product_name=product.name if product else payment.product_code,
                covers_until=payment.covers_until,
                status=status,
                days_left=days_left,
                last_payment={
                    "amount": payment.amount,
                    "currency": payment.currency,
                    "method": payment.method,
                    "reference": payment.reference,
                    "received_on": payment.received_on.isoformat(),
                },
            )
        )
    return sorted(views, key=lambda view: view.covers_until)


class RenewalService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], registry: JobRegistry
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry

    async def admin_list(self, *, status: str | None) -> list[SubscriptionView]:
        now = datetime.now(UTC)
        async with staff_transaction(self._session_factory) as session:
            views = await _views(session, await _latest_payments(session), now)
        return [view for view in views if status is None or view.status == status]

    async def count_due(self) -> int:
        """Abonnements à renouveler : échéance proche ou dépassée."""
        return len([view for view in await self.admin_list(status=None) if view.status != "ACTIVE"])

    async def renew(
        self,
        organization_id: uuid.UUID,
        staff_user_id: uuid.UUID,
        *,
        product_code: str,
        payment: PaymentInput,
    ) -> SubscriptionView:
        now = datetime.now(UTC)
        async with staff_transaction(self._session_factory) as session:
            previous = next(
                (
                    item
                    for item in await _latest_payments(session, organization_id)
                    if item.product_code == product_code
                ),
                None,
            )
            if previous is None or previous.covers_until is None:
                raise AppError(
                    "NO_SUBSCRIPTION",
                    "Ce client n'a pas encore cet abonnement : enregistrez une vente.",
                    status=404,
                )
            catalog = await current_catalog(session)
            product = catalog.product(product_code)
            organization = await session.get(Organization, organization_id)
            if product is None or organization is None:
                raise AppError("NOT_FOUND", "Offre ou entreprise introuvable.", status=404)
            price = catalog.price_for(product, catalog.currency_for_country(organization.country))
            period = SUBSCRIPTION_PERIOD.get(price.period.value if price else "")
            if price is None or period is None:
                raise AppError(
                    "VALIDATION_ERROR", "Cette offre n'est pas un abonnement.", status=422
                )
            # Paiement en avance : on prolonge depuis l'échéance ; en retard : depuis aujourd'hui
            covers_until = max(previous.covers_until, now) + period
            recorded = Payment(
                organization_id=organization_id,
                product_code=product_code,
                amount=payment.amount if payment.amount is not None else price.amount,
                currency=price.currency.value,
                method=payment.method.value,
                channel=PaymentChannel.MANUAL.value,
                reference=payment.reference,
                received_on=payment.received_on,
                recorded_by=staff_user_id,
                covers_until=covers_until,
            )
            session.add(recorded)
            await session.flush()
            await grant_product_entitlements(
                session,
                organization_id,
                product,
                expires_at=covers_until,
                reason=f"Offre {product.name} : renouvellement jusqu'au "
                f"{covers_until.strftime('%d/%m/%Y')}",
                staff_user_id=staff_user_id,
            )
            await mark_organization_active(session, organization_id)
            await record_audit(
                session,
                actor=Actor.user(staff_user_id),
                action="subscription.renew",
                entity_type="payment",
                entity_id=recorded.id,
                organization_id=organization_id,
                old_value={"covers_until": previous.covers_until.isoformat()},
                new_value={
                    "product_code": product_code,
                    "covers_until": covers_until.isoformat(),
                    "amount": recorded.amount,
                    "method": recorded.method,
                    "reference": recorded.reference,
                },
            )
            await publish(
                session,
                self._registry,
                PAYMENT_RECORDED_EVENT,
                {"payment_id": str(recorded.id)},
                organization_id=organization_id,
            )
            [view] = await _views(session, [recorded], now)
            return view


# ── Contrôle quotidien ──


def _french_date(moment: datetime) -> str:
    months = (
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    )  # fmt: skip
    return f"{moment.day} {months[moment.month - 1]} {moment.year}"


def _client_reminder(view: SubscriptionView, to: list[str], app_url: str) -> EmailMessage:
    due = _french_date(view.covers_until)
    amount = format_price({**view.last_payment, "period": "MONTH"})
    paragraphs = [
        "Bonjour,",
        f"Votre abonnement {view.product_name} arrive à échéance le {due}.",
        f"Pour ne pas interrompre vos services, renouvelez-le avant cette date ({amount}). "
        "Répondez à cet e-mail ou contactez votre conseiller BENILAB : nous enregistrons votre "
        "paiement et votre abonnement est prolongé d'un mois.",
    ]
    link = app_url + "dashboard.html"
    text = "\n\n".join(paragraphs) + f"\n\nMon espace : {link}\n\nL'équipe BENILAB Digital360"
    body = "".join(f"<p>{html.escape(item)}</p>" for item in paragraphs)
    body += f'<p><a href="{html.escape(link, quote=True)}">Ouvrir mon espace</a></p>'
    body += "<p>L'équipe BENILAB Digital360</p>"
    return EmailMessage(
        to=to,
        subject=f"Votre abonnement {view.product_name} arrive à échéance",
        text=text,
        html=body,
    )


def _team_digest(views: list[SubscriptionView], to: list[str], admin_url: str) -> EmailMessage:
    lines = [
        f"{view.organization_name} · {view.product_name} · échéance le "
        f"{view.covers_until.strftime('%d/%m/%Y')}"
        for view in views
    ]
    text = (
        "Abonnements à renouveler dans les prochains jours :\n\n"
        + "\n".join(f"- {line}" for line in lines)
        + f"\n\nRenouveler depuis l'admin : {admin_url}\n"
    )
    items = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    body = (
        f"<p>Abonnements à renouveler dans les prochains jours :</p><ul>{items}</ul>"
        f'<p><a href="{html.escape(admin_url, quote=True)}">Renouveler depuis l\'admin</a></p>'
    )
    return EmailMessage(
        to=to, subject=f"{len(views)} abonnement(s) à renouveler", text=text, html=body
    )


async def send_due_reminders(
    session: AsyncSession,
    sender: EmailSender,
    *,
    team: list[str],
    app_url: str,
    admin_url: str,
    now: datetime,
) -> int:
    """Un rappel par période d'abonnement qui arrive à échéance ; renvoie le nombre envoyé."""
    due = [
        payment
        for payment in await _latest_payments(session)
        if payment.reminder_sent_at is None
        and payment.covers_until is not None
        and now < payment.covers_until <= now + timedelta(days=REMINDER_DAYS)
    ]
    views = []
    for payment in due:
        [view] = await _views(session, [payment], now)
        views.append(view)
        owners = await owner_emails(session, payment.organization_id)
        if owners:
            await sender.send(_client_reminder(view, owners, app_url))
        payment.reminder_sent_at = now
    if views and team:
        await sender.send(_team_digest(views, team, admin_url))
    await session.flush()
    return len(views)


async def schedule_daily_sweep(
    session_factory: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> None:
    """Enfile le contrôle du jour (idempotent : une seule tâche par date)."""
    moment = now or datetime.now(UTC)
    async with staff_transaction(session_factory) as session:
        await enqueue(
            session,
            RENEWAL_SWEEP_JOB,
            {},
            dedup_key=f"renewal-sweep:{moment.date().isoformat()}",
        )


def register_jobs(
    registry: JobRegistry,
    sender: EmailSender,
    *,
    team: list[str],
    app_url: str,
    admin_url: str,
) -> None:
    @registry.job(RENEWAL_SWEEP_JOB)
    async def renewal_sweep(session: AsyncSession, _: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        await send_due_reminders(
            session, sender, team=team, app_url=app_url, admin_url=admin_url, now=now
        )
        tomorrow = now.date() + timedelta(days=1)
        await enqueue(
            session,
            RENEWAL_SWEEP_JOB,
            {},
            dedup_key=f"renewal-sweep:{tomorrow.isoformat()}",
            run_at=datetime.combine(tomorrow, SWEEP_TIME, tzinfo=UTC),
        )
