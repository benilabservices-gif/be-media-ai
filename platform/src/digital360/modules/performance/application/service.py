"""Suivi des résultats d'un client : mise en place de Google Business et rapports mensuels.

Étape 1 : l'équipe saisit chaque mois les chiffres relevés sur les tableaux de bord de Google
et de l'outil de mesure du site. Le client voit le dernier mois, l'évolution par rapport au
mois précédent (calculée ici) et l'historique ; il est prévenu par e-mail d'un nouveau mois.
"""

import html
import uuid
from datetime import date
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.actor import Actor
from digital360.core.audit import record_audit
from digital360.core.email import EmailMessage, EmailSender
from digital360.core.errors import AppError
from digital360.core.jobs import JobRegistry, enqueue
from digital360.core.tenancy import TenantContext, staff_transaction, tenant_transaction
from digital360.modules.billing.application.service import owner_emails
from digital360.modules.organizations.infrastructure.models import Organization
from digital360.modules.performance.domain.metrics import (
    MAX_TOP_SEARCHES,
    Channel,
    SetupStatus,
    changes,
    clean_metrics,
    definitions,
    setup_steps,
)
from digital360.modules.performance.infrastructure.models import (
    GoogleBusinessSetup,
    MonthlyReport,
)

NOTIFY_REPORT_JOB = "performance.notify_new_report"
CHANNEL_NAMES = {Channel.GOOGLE_BUSINESS: "Google Business", Channel.WEBSITE: "site web"}
MONTHS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)  # fmt: skip


def month_label(period: date) -> str:
    return f"{MONTHS[period.month - 1]} {period.year}"


def of_month(period: date) -> str:
    """« de septembre 2026 », mais « d'août 2026 » (élision devant une voyelle)."""
    label = month_label(period)
    return ("d'" if label[0] in "aeiouéèêàâîôû" else "de ") + label


def _setup_view(setup: GoogleBusinessSetup | None) -> dict[str, Any]:
    status = SetupStatus(setup.status) if setup else SetupStatus.NOT_STARTED
    return {
        "status": status.value,
        "steps": setup_steps(status),
        "profile_url": setup.profile_url if setup else None,
        "note": setup.note if setup else None,
        "updated_at": setup.updated_at if setup else None,
    }


def _channel_view(channel: Channel, reports: list[MonthlyReport]) -> dict[str, Any]:
    """Rapports du plus récent au plus ancien, chacun comparé au mois qui le précède."""
    ordered = sorted(reports, key=lambda item: item.period, reverse=True)
    items = []
    for index, report in enumerate(ordered):
        previous = ordered[index + 1] if index + 1 < len(ordered) else None
        items.append(
            {
                "period": report.period,
                "metrics": report.metrics,
                "top_searches": report.top_searches,
                "note": report.note,
                "changes": changes(channel, report.metrics, previous.metrics if previous else None),
                "updated_at": report.updated_at,
            }
        )
    return {"definitions": definitions(channel), "reports": items}


async def _overview(
    session: AsyncSession, organization_id: uuid.UUID, manager_email: str
) -> dict[str, Any]:
    setup = await session.scalar(
        select(GoogleBusinessSetup).where(GoogleBusinessSetup.organization_id == organization_id)
    )
    reports = list(
        await session.scalars(
            select(MonthlyReport).where(MonthlyReport.organization_id == organization_id)
        )
    )
    return {
        "google_setup": _setup_view(setup),
        # Adresse à ajouter comme gestionnaire de la fiche Google
        "manager_email": manager_email,
        "channels": {
            channel.value: _channel_view(
                channel, [item for item in reports if item.channel == channel.value]
            )
            for channel in Channel
        },
    }


def first_of_month(period: date) -> date:
    return period.replace(day=1)


class PerformanceService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *, manager_email: str
    ) -> None:
        self._session_factory = session_factory
        self._manager_email = manager_email

    async def overview(self, context: TenantContext) -> dict[str, Any]:
        async with tenant_transaction(self._session_factory, context) as session:
            return await _overview(session, context.organization_id, self._manager_email)

    async def admin_overview(self, organization_id: uuid.UUID) -> dict[str, Any]:
        async with staff_transaction(self._session_factory) as session:
            await self._organization(session, organization_id)
            return await _overview(session, organization_id, self._manager_email)

    async def set_google_setup(
        self,
        organization_id: uuid.UUID,
        staff_user_id: uuid.UUID,
        *,
        status: SetupStatus,
        profile_url: str | None,
        note: str | None,
    ) -> dict[str, Any]:
        async with staff_transaction(self._session_factory) as session:
            await self._organization(session, organization_id)
            setup = await session.scalar(
                select(GoogleBusinessSetup)
                .where(GoogleBusinessSetup.organization_id == organization_id)
                .with_for_update()
            )
            old = setup.status if setup else SetupStatus.NOT_STARTED.value
            if setup is None:
                setup = GoogleBusinessSetup(organization_id=organization_id)
                session.add(setup)
            setup.status = status.value
            setup.profile_url = profile_url
            setup.note = note
            setup.updated_by = staff_user_id
            await session.flush()
            await record_audit(
                session,
                actor=Actor.user(staff_user_id),
                action="performance.google_setup",
                entity_type="google_business_setup",
                entity_id=setup.id,
                organization_id=organization_id,
                old_value={"status": old},
                new_value={"status": status.value},
            )
            await session.refresh(setup)
            return _setup_view(setup)

    async def save_report(
        self,
        organization_id: uuid.UUID,
        staff_user_id: uuid.UUID,
        *,
        channel: Channel,
        period: date,
        metrics: dict[str, Any],
        top_searches: list[str],
        note: str | None,
    ) -> dict[str, Any]:
        """Crée ou corrige le rapport du mois ; le client est prévenu à la première publication."""
        period = first_of_month(period)
        if period > first_of_month(date.today()):
            raise AppError(
                "VALIDATION_ERROR",
                "Le mois du rapport ne peut pas être dans le futur.",
                status=422,
                errors=[{"field": "period", "reason": "future"}],
            )
        cleaned = clean_metrics(channel, metrics)
        searches = [item.strip() for item in top_searches if item.strip()][:MAX_TOP_SEARCHES]
        if channel is not Channel.GOOGLE_BUSINESS:
            searches = []
        async with staff_transaction(self._session_factory) as session:
            await self._organization(session, organization_id)
            report = await session.scalar(
                select(MonthlyReport)
                .where(
                    MonthlyReport.organization_id == organization_id,
                    MonthlyReport.channel == channel.value,
                    MonthlyReport.period == period,
                )
                .with_for_update()
            )
            created = report is None
            if report is None:
                report = MonthlyReport(
                    organization_id=organization_id, channel=channel.value, period=period
                )
                session.add(report)
            report.metrics = cleaned
            report.top_searches = searches
            report.note = note
            report.entered_by = staff_user_id
            await session.flush()
            await record_audit(
                session,
                actor=Actor.user(staff_user_id),
                action="performance.report_" + ("create" if created else "update"),
                entity_type="monthly_report",
                entity_id=report.id,
                organization_id=organization_id,
                new_value={"channel": channel.value, "period": period.isoformat(), **cleaned},
            )
            if created:
                await enqueue(
                    session,
                    NOTIFY_REPORT_JOB,
                    {"report_id": str(report.id)},
                    organization_id=organization_id,
                )
            return await _overview(session, organization_id, self._manager_email)

    async def delete_report(
        self,
        organization_id: uuid.UUID,
        staff_user_id: uuid.UUID,
        *,
        channel: Channel,
        period: date,
    ) -> None:
        async with staff_transaction(self._session_factory) as session:
            removed = await session.execute(
                delete(MonthlyReport)
                .where(
                    MonthlyReport.organization_id == organization_id,
                    MonthlyReport.channel == channel.value,
                    MonthlyReport.period == first_of_month(period),
                )
                .returning(MonthlyReport.id)
            )
            report_id = removed.scalar_one_or_none()
            if report_id is None:
                raise AppError("NOT_FOUND", "Rapport introuvable.", status=404)
            await record_audit(
                session,
                actor=Actor.user(staff_user_id),
                action="performance.report_delete",
                entity_type="monthly_report",
                entity_id=report_id,
                organization_id=organization_id,
                old_value={"channel": channel.value, "period": first_of_month(period).isoformat()},
            )

    @staticmethod
    async def _organization(session: AsyncSession, organization_id: uuid.UUID) -> Organization:
        organization = await session.get(Organization, organization_id)
        if organization is None or organization.deleted_at is not None:
            raise AppError("NOT_FOUND", "Entreprise introuvable.", status=404)
        return organization


def register_jobs(registry: JobRegistry, sender: EmailSender, *, app_url: str) -> None:
    @registry.job(NOTIFY_REPORT_JOB)
    async def notify_new_report(session: AsyncSession, payload: dict[str, Any]) -> None:
        report = await session.get(MonthlyReport, uuid.UUID(payload["report_id"]))
        if report is None:
            return
        owners = await owner_emails(session, report.organization_id)
        if not owners:
            return
        channel = Channel(report.channel)
        month = of_month(report.period)
        page = "google" if channel is Channel.GOOGLE_BUSINESS else "analytics"
        link = app_url + "dashboard.html#" + page
        paragraphs = [
            "Bonjour,",
            f"Votre rapport {CHANNEL_NAMES[channel]} {month} est disponible dans votre espace : "
            "chiffres du mois, évolution par rapport au mois précédent et commentaire de votre "
            "conseiller.",
        ]
        text = "\n\n".join(paragraphs) + f"\n\nVoir mon rapport : {link}\n\nL'équipe BENILAB"
        body = "".join(f"<p>{html.escape(item)}</p>" for item in paragraphs)
        body += f'<p><a href="{html.escape(link, quote=True)}">Voir mon rapport</a></p>'
        body += "<p>L'équipe BENILAB</p>"
        await sender.send(
            EmailMessage(
                to=owners,
                subject=f"Votre rapport {CHANNEL_NAMES[channel]} {month}",
                text=text,
                html=body,
            )
        )
