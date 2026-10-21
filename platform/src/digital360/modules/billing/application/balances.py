"""Ventes payées en plusieurs fois : rappels du solde, suspension et reste dû côté client.

L'équipe enregistre un acompte (montant libre) : l'offre s'active tout de suite et le solde est
dû sous `BALANCE_DUE_DAYS` jours. Le contrôle quotidien prévient le client une semaine avant
l'échéance, le jour même, puis une semaine après : à ce dernier rappel, les droits de l'offre
sont suspendus jusqu'au paiement complet (voir `PurchaseRequestService.admin_record_payment`).
"""

import html
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from digital360.core.email import EmailMessage, EmailSender
from digital360.modules.billing.application.service import (
    format_price,
    owner_emails,
    remaining_balance,
    suspend_offer,
)
from digital360.modules.billing.infrastructure.models import Payment, PurchaseRequest
from digital360.modules.catalog.application.service import current_catalog
from digital360.modules.organizations.infrastructure.models import Organization

REMINDER_BEFORE = timedelta(days=7)
SUSPEND_AFTER = timedelta(days=7)
# Étapes de rappel (purchase_requests.balance_reminder_stage)
STAGE_SOON, STAGE_DUE, STAGE_LATE = 1, 2, 3


@dataclass(frozen=True)
class BalanceView:
    purchase_request_id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    product_code: str
    product_name: str
    currency: str
    # Unité mineure, HT
    total: int
    paid: int
    remaining: int
    due_on: date
    suspended: bool


async def _open_balances(
    session: AsyncSession, organization_id: uuid.UUID | None = None
) -> list[tuple[PurchaseRequest, int]]:
    statement = (
        select(PurchaseRequest, func.coalesce(func.sum(Payment.amount), 0))
        .outerjoin(Payment, Payment.purchase_request_id == PurchaseRequest.id)
        .where(PurchaseRequest.balance_due_on.is_not(None))
        .group_by(PurchaseRequest.id)
        .order_by(PurchaseRequest.balance_due_on)
    )
    if organization_id is not None:
        statement = statement.where(PurchaseRequest.organization_id == organization_id)
    return [(request, int(paid)) for request, paid in (await session.execute(statement)).all()]


async def _views(
    session: AsyncSession, rows: list[tuple[PurchaseRequest, int]]
) -> list[BalanceView]:
    if not rows:
        return []
    catalog = await current_catalog(session)
    names = dict(
        (
            await session.execute(
                select(Organization.id, Organization.commercial_name).where(
                    Organization.id.in_({request.organization_id for request, _ in rows})
                )
            )
        )
        .tuples()
        .all()
    )
    views = []
    for request, paid in rows:
        remaining = remaining_balance(request, paid)
        if not remaining or request.balance_due_on is None:
            continue
        product = catalog.product(request.product_code)
        views.append(
            BalanceView(
                purchase_request_id=request.id,
                organization_id=request.organization_id,
                organization_name=names.get(request.organization_id, ""),
                product_code=request.product_code,
                product_name=product.name if product else request.product_code,
                currency=request.currency,
                total=request.amount or 0,
                paid=paid,
                remaining=remaining,
                due_on=request.balance_due_on,
                suspended=request.suspended_at is not None,
            )
        )
    return views


async def client_balances(session: AsyncSession, organization_id: uuid.UUID) -> list[BalanceView]:
    """Espace client : ce qu'il reste à payer sur les offres achetées en plusieurs fois."""
    return await _views(session, await _open_balances(session, organization_id))


def stage_for(due_on: date, today: date) -> int:
    if today >= due_on + SUSPEND_AFTER:
        return STAGE_LATE
    if today >= due_on:
        return STAGE_DUE
    if today >= due_on - REMINDER_BEFORE:
        return STAGE_SOON
    return 0


def _amount(view: BalanceView) -> str:
    return format_price({"amount": view.remaining, "currency": view.currency, "period": "NONE"})


def _client_email(view: BalanceView, stage: int, to: list[str], app_url: str) -> EmailMessage:
    due = view.due_on.strftime("%d/%m/%Y")
    pay = (
        "Pour le régler, répondez à cet e-mail ou contactez votre conseiller BENILAB "
        "(mobile money, virement ou espèces) : nous enregistrons votre paiement et vous "
        "envoyons un reçu."
    )
    if stage == STAGE_LATE:
        subject = f"Vos services {view.product_name} sont suspendus"
        paragraphs = [
            f"Le solde de votre offre {view.product_name} ({_amount(view)}) devait être réglé "
            f"le {due}. Vos services liés à cette offre sont suspendus.",
            "Ils sont réactivés dès le paiement complet du solde. " + pay,
        ]
    elif stage == STAGE_DUE:
        subject = f"Le solde de votre offre {view.product_name} est dû aujourd'hui"
        paragraphs = [
            f"Il reste {_amount(view)} à régler aujourd'hui pour votre offre {view.product_name}.",
            pay,
            "Sans paiement dans les 7 jours, vos services liés à cette offre seront suspendus.",
        ]
    else:
        subject = f"Solde de votre offre {view.product_name} : échéance le {due}"
        paragraphs = [
            f"Il reste {_amount(view)} à régler pour votre offre {view.product_name}, "
            f"avant le {due}.",
            pay,
        ]
    paragraphs = ["Bonjour,", *paragraphs]
    link = app_url + "dashboard.html"
    text = "\n\n".join(paragraphs) + f"\n\nMon espace : {link}\n\nL'équipe BENILAB Digital360"
    body = "".join(f"<p>{html.escape(item)}</p>" for item in paragraphs)
    body += f'<p><a href="{html.escape(link, quote=True)}">Ouvrir mon espace</a></p>'
    body += "<p>L'équipe BENILAB Digital360</p>"
    return EmailMessage(to=to, subject=subject, text=text, html=body)


STAGE_LABELS = {
    STAGE_SOON: "échéance dans 7 jours",
    STAGE_DUE: "échéance aujourd'hui",
    STAGE_LATE: "en retard, services suspendus",
}


def _team_digest(
    items: list[tuple[BalanceView, int]], to: list[str], admin_url: str
) -> EmailMessage:
    lines = [
        f"{view.organization_name} · {view.product_name} · reste {_amount(view)} · "
        f"{STAGE_LABELS[stage]} ({view.due_on.strftime('%d/%m/%Y')})"
        for view, stage in items
    ]
    text = (
        "Soldes à encaisser :\n\n"
        + "\n".join(f"- {line}" for line in lines)
        + f"\n\nEnregistrer un paiement depuis l'admin : {admin_url}\n"
    )
    rows = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    body = (
        f"<p>Soldes à encaisser :</p><ul>{rows}</ul>"
        f'<p><a href="{html.escape(admin_url, quote=True)}">Enregistrer un paiement</a></p>'
    )
    return EmailMessage(to=to, subject=f"{len(items)} solde(s) à encaisser", text=text, html=body)


async def send_balance_reminders(
    session: AsyncSession,
    sender: EmailSender,
    *,
    team: list[str],
    app_url: str,
    admin_url: str,
    now: datetime,
) -> int:
    """Un seul rappel par étape et par vente ; au rappel « en retard », l'offre est suspendue.

    Renvoie le nombre de ventes rappelées. Une vente découverte en retard (ex. contrôle manqué)
    reçoit directement le rappel de son étape actuelle, jamais les précédents.
    """
    today = now.date()
    reminded: list[tuple[BalanceView, int]] = []
    for request, paid in await _open_balances(session):
        if request.balance_due_on is None or not remaining_balance(request, paid):
            continue
        stage = stage_for(request.balance_due_on, today)
        if stage <= request.balance_reminder_stage:
            continue
        if stage == STAGE_LATE and request.suspended_at is None:
            await suspend_offer(session, request, now=now)
        request.balance_reminder_stage = stage
        [view] = await _views(session, [(request, paid)])
        owners = await owner_emails(session, request.organization_id)
        if owners:
            await sender.send(_client_email(view, stage, owners, app_url))
        reminded.append((view, stage))
    if reminded and team:
        await sender.send(_team_digest(reminded, team, admin_url))
    await session.flush()
    return len(reminded)
