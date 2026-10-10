"""Closer 3.0 : règles métier.

- Seul un client qui a déjà payé une offre (propriétaire d'une entreprise avec un paiement)
  peut devenir closer. Il reçoit un code et un lien de parrainage.
- Une entreprise créée avec ce code est rattachée au closer, définitivement (l'équipe peut
  corriger à la main). On ne se recommande pas soi-même.
- Chaque paiement encaissé d'un client rattaché, pendant ses 12 premiers mois (365 jours
  après son premier paiement), rapporte 20 % du montant HT au closer.
- Le 1er de chaque mois, les commissions pas encore relevées forment le relevé du mois
  écoulé ; l'équipe verse puis marque le relevé comme versé.
"""

import html
import logging
import secrets
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.actor import Actor
from digital360.core.audit import record_audit
from digital360.core.email import EmailMessage, EmailSender
from digital360.core.errors import AppError
from digital360.core.ids import uuid7
from digital360.core.jobs import JobRegistry, enqueue
from digital360.core.permissions import ClientRole
from digital360.core.tenancy import staff_transaction
from digital360.modules.billing.application.service import PAYMENT_RECORDED_EVENT, format_price
from digital360.modules.billing.infrastructure.models import Payment
from digital360.modules.identity.infrastructure.models import Membership, MembershipStatus, User
from digital360.modules.organizations.infrastructure.models import Organization
from digital360.modules.referrals.infrastructure.models import (
    Closer,
    CloserStatus,
    Commission,
    CommissionStatement,
    PayoutMethod,
    Referral,
    ReferralSource,
    StatementStatus,
)

logger = logging.getLogger("digital360.referrals")

COMMISSION_RATE_BPS = 2000  # 20 %
COMMISSION_WINDOW = timedelta(days=365)  # 12 premiers mois du client apporté
RECORD_COMMISSION_JOB = "referrals.record_commission"
MONTHLY_STATEMENTS_JOB = "referrals.monthly_statements"
STATEMENT_TIME = time(6, 0)
# Sans 0/O ni 1/I/L : un code dicté au téléphone reste lisible
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6

MONTHS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)  # fmt: skip


def commission_amount(base_amount: int) -> int:
    """20 % arrondi à l'unité mineure la plus proche."""
    return (base_amount * COMMISSION_RATE_BPS + 5000) // 10000


def _month_label(period: date) -> str:
    return f"{MONTHS[period.month - 1]} {period.year}"


def _first_of_month(moment: date) -> date:
    return moment.replace(day=1)


def _previous_month(moment: date) -> date:
    return (_first_of_month(moment) - timedelta(days=1)).replace(day=1)


def _next_month(moment: date) -> date:
    return (_first_of_month(moment) + timedelta(days=32)).replace(day=1)


def _not_found() -> AppError:
    return AppError("NOT_FOUND", "Closer introuvable.", status=404)


@dataclass(frozen=True)
class CloserSpace:
    eligible: bool
    closer: dict[str, Any] | None
    referrals: list[dict[str, Any]]
    # Commissions gagnées pas encore relevées, par devise
    pending: list[dict[str, Any]]
    statements: list[dict[str, Any]]


def _closer_payload(closer: Closer, app_url: str) -> dict[str, Any]:
    return {
        "id": closer.id,
        "code": closer.code,
        "referral_link": f"{app_url}?ref={closer.code}",
        "status": closer.status,
        "payout_method": closer.payout_method,
        "payout_account": closer.payout_account,
        "created_at": closer.created_at,
    }


def _statement_payload(statement: CommissionStatement) -> dict[str, Any]:
    return {
        "id": statement.id,
        "period": statement.period,
        "currency": statement.currency,
        "total_amount": statement.total_amount,
        "commission_count": statement.commission_count,
        "status": statement.status,
        "payout_method": statement.payout_method,
        "payout_reference": statement.payout_reference,
        "paid_on": statement.paid_on,
    }


async def _is_eligible(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """Propriétaire actif d'une entreprise qui a au moins un paiement."""
    paid = await session.scalar(
        select(Payment.id)
        .join(Membership, Membership.organization_id == Payment.organization_id)
        .where(
            Membership.user_id == user_id,
            Membership.role == ClientRole.CLIENT_OWNER.value,
            Membership.status == MembershipStatus.ACTIVE.value,
        )
        .limit(1)
    )
    return paid is not None


async def _is_member(session: AsyncSession, user_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
    member = await session.scalar(
        select(Membership.id).where(
            Membership.user_id == user_id, Membership.organization_id == organization_id
        )
    )
    return member is not None


async def _unique_code(session: AsyncSession) -> str:
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if await session.scalar(select(Closer.id).where(Closer.code == code)) is None:
            return code


class CloserService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        app_url: str,
    ) -> None:
        self._session_factory = session_factory
        self._app_url = app_url

    # ── Espace du closer ──

    async def space(self, user_id: uuid.UUID) -> CloserSpace:
        async with staff_transaction(self._session_factory) as session:
            closer = await session.scalar(select(Closer).where(Closer.user_id == user_id))
            if closer is None:
                return CloserSpace(
                    eligible=await _is_eligible(session, user_id),
                    closer=None,
                    referrals=[],
                    pending=[],
                    statements=[],
                )
            referrals = (
                await session.execute(
                    select(Referral, Organization.commercial_name)
                    .join(Organization, Organization.id == Referral.organization_id)
                    .where(Referral.closer_id == closer.id)
                    .order_by(Referral.created_at.desc())
                )
            ).all()
            earned = dict(
                (
                    await session.execute(
                        select(Commission.organization_id, func.sum(Commission.amount))
                        .where(Commission.closer_id == closer.id)
                        .group_by(Commission.organization_id)
                    )
                )
                .tuples()
                .all()
            )
            pending = (
                await session.execute(
                    select(Commission.currency, func.sum(Commission.amount), func.count())
                    .where(Commission.closer_id == closer.id, Commission.statement_id.is_(None))
                    .group_by(Commission.currency)
                )
            ).all()
            statements = (
                await session.scalars(
                    select(CommissionStatement)
                    .where(CommissionStatement.closer_id == closer.id)
                    .order_by(CommissionStatement.period.desc())
                )
            ).all()
            return CloserSpace(
                eligible=True,
                closer=_closer_payload(closer, self._app_url),
                referrals=[
                    {
                        "organization_name": name,
                        "joined_at": referral.created_at,
                        "commissions_total": int(earned.get(referral.organization_id, 0)),
                    }
                    for referral, name in referrals
                ],
                pending=[
                    {"currency": currency, "amount": int(total), "count": count}
                    for currency, total, count in pending
                ],
                statements=[_statement_payload(item) for item in statements],
            )

    async def join(
        self, user_id: uuid.UUID, *, payout_method: PayoutMethod, payout_account: str
    ) -> CloserSpace:
        async with staff_transaction(self._session_factory) as session:
            existing = await session.scalar(select(Closer.id).where(Closer.user_id == user_id))
            if existing is None:
                if not await _is_eligible(session, user_id):
                    raise AppError(
                        "NOT_ELIGIBLE",
                        "Le programme Closer 3.0 est réservé aux clients qui ont déjà payé une "
                        "offre Digital360.",
                        status=403,
                    )
                closer = Closer(
                    user_id=user_id,
                    code=await _unique_code(session),
                    payout_method=payout_method.value,
                    payout_account=payout_account,
                    terms_accepted_at=datetime.now(UTC),
                )
                session.add(closer)
                await session.flush()
                await record_audit(
                    session,
                    actor=Actor.user(user_id),
                    action="closer.join",
                    entity_type="closer",
                    entity_id=closer.id,
                    new_value={"code": closer.code, "payout_method": payout_method.value},
                )
        return await self.space(user_id)

    async def update_payout(
        self, user_id: uuid.UUID, *, payout_method: PayoutMethod, payout_account: str
    ) -> CloserSpace:
        async with staff_transaction(self._session_factory) as session:
            closer = await session.scalar(
                select(Closer).where(Closer.user_id == user_id).with_for_update()
            )
            if closer is None:
                raise _not_found()
            old = {"payout_method": closer.payout_method, "payout_account": closer.payout_account}
            closer.payout_method = payout_method.value
            closer.payout_account = payout_account
            await record_audit(
                session,
                actor=Actor.user(user_id),
                action="closer.update_payout",
                entity_type="closer",
                entity_id=closer.id,
                old_value=old,
                new_value={"payout_method": payout_method.value, "payout_account": payout_account},
            )
        return await self.space(user_id)

    async def attach_by_code(
        self, organization_id: uuid.UUID, user_id: uuid.UUID, code: str
    ) -> bool:
        """Inscription avec un code : rattache la nouvelle entreprise. Un code inconnu, suspendu
        ou le sien est ignoré sans bloquer l'inscription. Renvoie True si rattachée."""
        async with staff_transaction(self._session_factory) as session:
            closer = await session.scalar(select(Closer).where(Closer.code == code.upper()))
            if (
                closer is None
                or closer.status != CloserStatus.ACTIVE.value
                or closer.user_id == user_id
                or await _is_member(session, closer.user_id, organization_id)
            ):
                return False
            statement = (
                insert(Referral)
                .values(
                    id=uuid7(),
                    closer_id=closer.id,
                    organization_id=organization_id,
                    source=ReferralSource.CODE.value,
                )
                .on_conflict_do_nothing(index_elements=["organization_id"])
                .returning(Referral.id)
            )
            referral_id = await session.scalar(statement)
            if referral_id is None:
                return False
            await record_audit(
                session,
                actor=Actor.user(user_id),
                action="closer.referral",
                entity_type="organization",
                entity_id=organization_id,
                organization_id=organization_id,
                new_value={"closer_id": str(closer.id), "source": ReferralSource.CODE.value},
            )
            return True

    # ── Équipe ──

    async def admin_list(self) -> list[dict[str, Any]]:
        async with staff_transaction(self._session_factory) as session:
            rows = (
                await session.execute(
                    select(Closer, User.full_name, User.email)
                    .join(User, User.id == Closer.user_id)
                    .order_by(Closer.created_at.desc())
                )
            ).all()
            referred = dict(
                (
                    await session.execute(
                        select(Referral.closer_id, func.count()).group_by(Referral.closer_id)
                    )
                )
                .tuples()
                .all()
            )
            totals: dict[uuid.UUID, dict[str, list[dict[str, Any]]]] = defaultdict(
                lambda: {"earned": [], "due": [], "paid": []}
            )
            for closer_id, currency, earned in await session.execute(
                select(
                    Commission.closer_id, Commission.currency, func.sum(Commission.amount)
                ).group_by(Commission.closer_id, Commission.currency)
            ):
                totals[closer_id]["earned"].append({"currency": currency, "amount": int(earned)})
            for closer_id, currency, status, amount in await session.execute(
                select(
                    CommissionStatement.closer_id,
                    CommissionStatement.currency,
                    CommissionStatement.status,
                    func.sum(CommissionStatement.total_amount),
                ).group_by(
                    CommissionStatement.closer_id,
                    CommissionStatement.currency,
                    CommissionStatement.status,
                )
            ):
                key = "due" if status == StatementStatus.DUE.value else "paid"
                totals[closer_id][key].append({"currency": currency, "amount": int(amount)})
            paying = dict(
                (
                    await session.execute(
                        select(
                            Commission.closer_id,
                            func.count(func.distinct(Commission.organization_id)),
                        ).group_by(Commission.closer_id)
                    )
                )
                .tuples()
                .all()
            )
            return [
                {
                    **_closer_payload(closer, self._app_url),
                    "full_name": name,
                    "email": email,
                    "referred_clients": referred.get(closer.id, 0),
                    "paying_clients": paying.get(closer.id, 0),
                    **totals[closer.id],
                }
                for closer, name, email in rows
            ]

    async def admin_statements(self, *, status: StatementStatus | None) -> list[dict[str, Any]]:
        statement = (
            select(CommissionStatement, User.full_name, User.email, Closer)
            .join(Closer, Closer.id == CommissionStatement.closer_id)
            .join(User, User.id == Closer.user_id)
            .order_by(CommissionStatement.period.desc(), User.full_name)
        )
        if status is not None:
            statement = statement.where(CommissionStatement.status == status.value)
        async with staff_transaction(self._session_factory) as session:
            return [
                {
                    **_statement_payload(item),
                    "closer_id": closer.id,
                    "closer_name": name,
                    "closer_email": email,
                    "closer_code": closer.code,
                    "closer_payout_method": closer.payout_method,
                    "closer_payout_account": closer.payout_account,
                }
                for item, name, email, closer in (await session.execute(statement)).all()
            ]

    async def admin_pay(
        self,
        statement_id: uuid.UUID,
        staff_user_id: uuid.UUID,
        *,
        method: PayoutMethod,
        reference: str,
        paid_on: date,
    ) -> dict[str, Any]:
        async with staff_transaction(self._session_factory) as session:
            item = await session.scalar(
                select(CommissionStatement)
                .where(CommissionStatement.id == statement_id)
                .with_for_update()
            )
            if item is None:
                raise AppError("NOT_FOUND", "Relevé introuvable.", status=404)
            if item.status == StatementStatus.PAID.value:
                raise AppError("ALREADY_PAID", "Ce relevé est déjà marqué comme versé.", status=409)
            item.status = StatementStatus.PAID.value
            item.payout_method = method.value
            item.payout_reference = reference
            item.paid_on = paid_on
            item.paid_by = staff_user_id
            await record_audit(
                session,
                actor=Actor.user(staff_user_id),
                action="commission_statement.pay",
                entity_type="commission_statement",
                entity_id=item.id,
                old_value={"status": StatementStatus.DUE.value},
                new_value={
                    "status": item.status,
                    "amount": item.total_amount,
                    "currency": item.currency,
                    "method": method.value,
                    "reference": reference,
                },
            )
            await enqueue(session, SEND_PAYOUT_NOTICE_JOB, {"statement_id": str(item.id)})
            await session.flush()
            return _statement_payload(item)

    async def admin_set_status(
        self, closer_id: uuid.UUID, staff_user_id: uuid.UUID, status: CloserStatus
    ) -> None:
        async with staff_transaction(self._session_factory) as session:
            closer = await session.get(Closer, closer_id, with_for_update=True)
            if closer is None:
                raise _not_found()
            old = closer.status
            closer.status = status.value
            await record_audit(
                session,
                actor=Actor.user(staff_user_id),
                action="closer.set_status",
                entity_type="closer",
                entity_id=closer.id,
                old_value={"status": old},
                new_value={"status": status.value},
            )

    async def admin_attach(
        self, organization_id: uuid.UUID, staff_user_id: uuid.UUID, code: str | None
    ) -> str | None:
        """Rattache (ou détache, code None) un client à un closer. Renvoie le code retenu.
        Les commissions déjà calculées restent acquises ; le changement vaut pour la suite."""
        async with staff_transaction(self._session_factory) as session:
            organization = await session.get(Organization, organization_id)
            if organization is None or organization.deleted_at is not None:
                raise AppError("NOT_FOUND", "Entreprise introuvable.", status=404)
            current = await session.scalar(
                select(Referral).where(Referral.organization_id == organization_id)
            )
            old = str(current.closer_id) if current else None
            closer = None
            if code is not None:
                closer = await session.scalar(select(Closer).where(Closer.code == code.upper()))
                if closer is None:
                    raise AppError("NOT_FOUND", "Aucun closer avec ce code.", status=404)
                if await _is_member(session, closer.user_id, organization_id):
                    raise AppError(
                        "SELF_REFERRAL",
                        "Un closer ne peut pas être rattaché à sa propre entreprise.",
                        status=422,
                    )
            if current is not None and (closer is None or current.closer_id != closer.id):
                await session.delete(current)
                await session.flush()
            if closer is not None and (current is None or current.closer_id != closer.id):
                session.add(
                    Referral(
                        closer_id=closer.id,
                        organization_id=organization_id,
                        source=ReferralSource.MANUAL.value,
                        created_by=staff_user_id,
                    )
                )
            await record_audit(
                session,
                actor=Actor.user(staff_user_id),
                action="closer.referral_manual",
                entity_type="organization",
                entity_id=organization_id,
                organization_id=organization_id,
                old_value={"closer_id": old},
                new_value={"closer_id": str(closer.id) if closer else None},
            )
            return closer.code if closer else None

    async def referral_for(self, organization_id: uuid.UUID) -> dict[str, Any] | None:
        """Closer d'une entreprise, pour la fiche admin."""
        async with staff_transaction(self._session_factory) as session:
            row = (
                await session.execute(
                    select(Closer, User.full_name, Referral.source)
                    .join(Referral, Referral.closer_id == Closer.id)
                    .join(User, User.id == Closer.user_id)
                    .where(Referral.organization_id == organization_id)
                )
            ).one_or_none()
        if row is None:
            return None
        closer, name, source = row
        return {"code": closer.code, "full_name": name, "source": source}

    async def due_total(self) -> list[dict[str, Any]]:
        async with staff_transaction(self._session_factory) as session:
            rows = await session.execute(
                select(CommissionStatement.currency, func.sum(CommissionStatement.total_amount))
                .where(CommissionStatement.status == StatementStatus.DUE.value)
                .group_by(CommissionStatement.currency)
            )
            return [{"currency": currency, "amount": int(total)} for currency, total in rows]


# ── Calcul des commissions et relevés (tâches) ──


async def record_commission(session: AsyncSession, payment: Payment) -> bool:
    """Commission d'un paiement, si son client a un closer actif et est dans ses 12 premiers
    mois. Idempotent (un paiement ne rapporte qu'une fois) ; renvoie True si enregistrée."""
    closer = (
        await session.execute(
            select(Closer)
            .join(Referral, Referral.closer_id == Closer.id)
            .where(Referral.organization_id == payment.organization_id)
        )
    ).scalar_one_or_none()
    if closer is None or closer.status != CloserStatus.ACTIVE.value:
        return False
    first_payment = await session.scalar(
        select(func.min(Payment.received_on)).where(
            Payment.organization_id == payment.organization_id
        )
    )
    if first_payment is not None and payment.received_on > first_payment + COMMISSION_WINDOW:
        return False
    inserted = await session.scalar(
        insert(Commission)
        .values(
            id=uuid7(),
            closer_id=closer.id,
            organization_id=payment.organization_id,
            payment_id=payment.id,
            product_code=payment.product_code,
            base_amount=payment.amount,
            rate_bps=COMMISSION_RATE_BPS,
            amount=commission_amount(payment.amount),
            currency=payment.currency,
            earned_on=payment.received_on,
        )
        .on_conflict_do_nothing(index_elements=["payment_id"])
        .returning(Commission.id)
    )
    return inserted is not None


async def build_monthly_statements(
    session: AsyncSession, *, today: date
) -> list[CommissionStatement]:
    """Relève les commissions gagnées avant ce mois-ci et pas encore relevées, dans le relevé
    du mois écoulé (une commission arrivée en retard part dans le relevé suivant)."""
    period = _previous_month(today)
    rows = (
        await session.execute(
            select(Commission.closer_id, Commission.currency, Commission.id, Commission.amount)
            .where(
                Commission.statement_id.is_(None),
                Commission.earned_on < _first_of_month(today),
            )
            .with_for_update()
        )
    ).all()
    groups: dict[tuple[uuid.UUID, str], list[tuple[uuid.UUID, int]]] = defaultdict(list)
    for closer_id, currency, commission_id, amount in rows:
        groups[(closer_id, currency)].append((commission_id, amount))
    created = []
    for (closer_id, currency), items in groups.items():
        statement = await session.scalar(
            select(CommissionStatement).where(
                CommissionStatement.closer_id == closer_id,
                CommissionStatement.period == period,
                CommissionStatement.currency == currency,
            )
        )
        if statement is None:
            statement = CommissionStatement(
                closer_id=closer_id,
                period=period,
                currency=currency,
                total_amount=0,
                commission_count=0,
            )
            session.add(statement)
            await session.flush()
            created.append(statement)
        elif statement.status != StatementStatus.DUE.value:
            continue  # relevé déjà versé : ces commissions partiront le mois prochain
        statement.total_amount += sum(amount for _, amount in items)
        statement.commission_count += len(items)
        for commission_id, _ in items:
            commission = await session.get(Commission, commission_id)
            if commission is not None:
                commission.statement_id = statement.id
    await session.flush()
    return created


async def schedule_monthly_statements(
    session_factory: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> None:
    """Enfile le relevé du 1er du mois prochain (idempotent : une tâche par mois)."""
    moment = now or datetime.now(UTC)
    async with staff_transaction(session_factory) as session:
        await _enqueue_statements(session, _next_month(moment.date()))


async def _enqueue_statements(session: AsyncSession, run_on: date) -> None:
    await enqueue(
        session,
        MONTHLY_STATEMENTS_JOB,
        {},
        dedup_key=f"closer-statements:{run_on.isoformat()}",
        run_at=datetime.combine(run_on, STATEMENT_TIME, tzinfo=UTC),
    )


SEND_PAYOUT_NOTICE_JOB = "referrals.send_payout_notice"


def _email(to: str, subject: str, paragraphs: list[str], link: str) -> EmailMessage:
    text = (
        "\n\n".join(paragraphs) + f"\n\nMon espace Closer : {link}\n\nL'équipe BENILAB Digital360"
    )
    body = "".join(f"<p>{html.escape(item)}</p>" for item in paragraphs)
    body += f'<p><a href="{html.escape(link, quote=True)}">Ouvrir mon espace Closer</a></p>'
    body += "<p>L'équipe BENILAB Digital360</p>"
    return EmailMessage(to=[to], subject=subject, text=text, html=body)


def _amount(amount: int, currency: str) -> str:
    return format_price({"amount": amount, "currency": currency, "period": "NONE"})


def register_jobs(registry: JobRegistry, sender: EmailSender, *, app_url: str) -> None:
    link = app_url + "dashboard.html#closer"

    @registry.on(PAYMENT_RECORDED_EVENT, name=RECORD_COMMISSION_JOB)
    async def on_payment(session: AsyncSession, payload: dict[str, Any]) -> None:
        payment = await session.get(Payment, uuid.UUID(payload["payment_id"]))
        if payment is not None:
            await record_commission(session, payment)

    @registry.job(MONTHLY_STATEMENTS_JOB)
    async def monthly_statements(session: AsyncSession, _: dict[str, Any]) -> None:
        today = datetime.now(UTC).date()
        for statement in await build_monthly_statements(session, today=today):
            email = await session.scalar(
                select(User.email)
                .join(Closer, Closer.user_id == User.id)
                .where(Closer.id == statement.closer_id)
            )
            if email:
                month = _month_label(statement.period)
                await sender.send(
                    _email(
                        email,
                        f"Votre relevé Closer 3.0 de {month}",
                        [
                            "Bonjour,",
                            f"En {month}, les clients que vous avez apportés vous ont rapporté "
                            f"{_amount(statement.total_amount, statement.currency)} de commissions "
                            f"({statement.commission_count} paiement(s)).",
                            "Notre équipe vous verse ce montant dans les prochains jours, sur le "
                            "compte indiqué dans votre espace Closer.",
                        ],
                        link,
                    )
                )
        await _enqueue_statements(session, _next_month(today))

    @registry.job(SEND_PAYOUT_NOTICE_JOB)
    async def payout_notice(session: AsyncSession, payload: dict[str, Any]) -> None:
        statement = await session.get(CommissionStatement, uuid.UUID(payload["statement_id"]))
        if statement is None or statement.paid_on is None:
            return
        email = await session.scalar(
            select(User.email)
            .join(Closer, Closer.user_id == User.id)
            .where(Closer.id == statement.closer_id)
        )
        if email:
            await sender.send(
                _email(
                    email,
                    f"Vos commissions de {_month_label(statement.period)} ont été versées",
                    [
                        "Bonjour,",
                        f"Nous vous avons versé {_amount(statement.total_amount, statement.currency)} "
                        f"le {statement.paid_on.strftime('%d/%m/%Y')} (référence "
                        f"{statement.payout_reference}).",
                        "Merci pour les clients que vous nous apportez.",
                    ],
                    link,
                )
            )
