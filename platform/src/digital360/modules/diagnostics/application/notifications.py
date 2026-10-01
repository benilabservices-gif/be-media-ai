"""Alerte à l'équipe commerciale à chaque diagnostic terminé (abonné à `diagnostic.completed`)."""

import html
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from digital360.core.email import EmailMessage, EmailSender
from digital360.core.jobs import JobRegistry
from digital360.modules.diagnostics.application.service import (
    DIAGNOSTIC_COMPLETED_EVENT,
    DiagnosticSummary,
    load_summary,
)

ALERT_SALES_JOB = "diagnostics.alert_sales"


def _yes_no(value: bool) -> str:
    return "oui" if value else "non"


def _alert_email(
    prospect: DiagnosticSummary, recipients: list[str], admin_url: str
) -> EmailMessage:
    company = prospect.company or "Entreprise sans nom"
    lines = [
        ("Entreprise", company),
        ("Secteur", prospect.sector or "—"),
        ("Ville", ", ".join(part for part in (prospect.city, prospect.country) if part) or "—"),
        ("Téléphone", prospect.phone or "—"),
        ("WhatsApp", prospect.whatsapp or "—"),
        ("Email", prospect.email or "—"),
        (
            "Digital Score",
            f"{prospect.total_score}/100" if prospect.total_score is not None else "—",
        ),
        ("Accepte les emails marketing", _yes_no(prospect.marketing_email_consent)),
        ("Accepte WhatsApp marketing", _yes_no(prospect.marketing_whatsapp_consent)),
    ]
    text = (
        "Un nouveau diagnostic vient d'être terminé.\n\n"
        + "\n".join(f"{label} : {value}" for label, value in lines)
        + f"\n\nVoir les prospects : {admin_url}\n"
    )
    # Toutes les valeurs viennent du formulaire public : échappées dans la version HTML
    rows = "".join(
        f"<tr><td><strong>{html.escape(label)}</strong></td><td>{html.escape(value)}</td></tr>"
        for label, value in lines
    )
    body = (
        "<p>Un nouveau diagnostic vient d'être terminé.</p>"
        f"<table>{rows}</table>"
        f'<p><a href="{html.escape(admin_url, quote=True)}">Voir les prospects</a></p>'
    )
    return EmailMessage(
        to=recipients,
        subject=f"Nouveau prospect : {company}",
        text=text,
        html=body,
    )


def register_jobs(
    registry: JobRegistry, sender: EmailSender, *, recipients: list[str], admin_url: str
) -> None:
    @registry.on(DIAGNOSTIC_COMPLETED_EVENT, name=ALERT_SALES_JOB)
    async def alert_sales(session: AsyncSession, payload: dict[str, Any]) -> None:
        if not recipients:
            return
        prospect = await load_summary(session, uuid.UUID(payload["diagnostic_id"]))
        if prospect is None:
            return
        await sender.send(_alert_email(prospect, recipients, admin_url))
