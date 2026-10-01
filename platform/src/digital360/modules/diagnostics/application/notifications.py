"""E-mails envoyés à chaque diagnostic terminé (abonnés à `diagnostic.completed`) :

- alerte à l'équipe commerciale, avec les coordonnées du prospect ;
- « Votre Digital Score » au prospect, s'il a donné son adresse.
"""

import html
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from digital360.core.email import EmailMessage, EmailSender
from digital360.core.jobs import JobRegistry
from digital360.modules.diagnostics.application.service import (
    DIAGNOSTIC_COMPLETED_EVENT,
    DiagnosticSummary,
    load_summary,
)
from digital360.modules.diagnostics.infrastructure.models import (
    ActionPlan,
    ActionPlanItem,
    DiagnosticSession,
    DigitalScore,
    PlanItemStatus,
)

ALERT_SALES_JOB = "diagnostics.alert_sales"
SEND_SCORE_JOB = "diagnostics.send_score_to_prospect"
PRIORITIES_IN_EMAIL = 3


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


def _score_email(to: str, score: DigitalScore, priorities: list[str], app_url: str) -> EmailMessage:
    """Aucun texte saisi par le visiteur (pas même le nom de l'entreprise) : sinon n'importe
    qui pourrait faire envoyer son propre message, signé par notre domaine, à un inconnu."""
    numbered = "\n".join(f"{index}. {action}" for index, action in enumerate(priorities, 1))
    text = (
        "Bonjour,\n\n"
        "Merci d'avoir réalisé votre diagnostic digital BENILAB.\n\n"
        f"Votre Digital Score : {score.total}/100 ({score.maturity_label})\n\n"
        + (f"Vos priorités :\n{numbered}\n\n" if priorities else "")
        + "Créez votre espace gratuit pour retrouver votre plan d'action complet et suivre "
        f"vos progrès : {app_url}\n\n"
        "Le Digital Score est un indicateur interne de BENILAB, établi à partir de vos "
        "réponses.\n\n"
        "L'équipe BENILAB Digital360"
    )
    items = "".join(f"<li>{html.escape(action)}</li>" for action in priorities)
    body = (
        "<p>Bonjour,</p>"
        "<p>Merci d'avoir réalisé votre diagnostic digital BENILAB.</p>"
        f'<p style="font-size:20px"><strong>Votre Digital Score : {score.total}/100</strong>'
        f"<br>{html.escape(score.maturity_label)}</p>"
        + (f"<p><strong>Vos priorités :</strong></p><ol>{items}</ol>" if priorities else "")
        + f'<p><a href="{html.escape(app_url, quote=True)}">Créer mon espace gratuit</a>'
        " pour retrouver votre plan d'action complet et suivre vos progrès.</p>"
        '<p style="color:#666;font-size:12px">Le Digital Score est un indicateur interne de '
        "BENILAB, établi à partir de vos réponses.</p>"
        "<p>L'équipe BENILAB Digital360</p>"
    )
    return EmailMessage(
        to=[to], subject=f"Votre Digital Score : {score.total}/100", text=text, html=body
    )


async def _top_priorities(session: AsyncSession, diagnostic_id: uuid.UUID) -> list[str]:
    actions = (
        await session.execute(
            select(ActionPlanItem.recommended_action)
            .join(ActionPlan, ActionPlan.id == ActionPlanItem.plan_id)
            .where(
                ActionPlan.session_id == diagnostic_id,
                ActionPlanItem.status != PlanItemStatus.DISMISSED.value,
            )
            .order_by(ActionPlanItem.position)
            .limit(PRIORITIES_IN_EMAIL)
        )
    ).scalars()
    return list(actions)


def register_jobs(
    registry: JobRegistry,
    sender: EmailSender,
    *,
    recipients: list[str],
    admin_url: str,
    app_url: str,
) -> None:
    @registry.on(DIAGNOSTIC_COMPLETED_EVENT, name=ALERT_SALES_JOB)
    async def alert_sales(session: AsyncSession, payload: dict[str, Any]) -> None:
        if not recipients:
            return
        prospect = await load_summary(session, uuid.UUID(payload["diagnostic_id"]))
        if prospect is None:
            return
        await sender.send(_alert_email(prospect, recipients, admin_url))

    @registry.on(DIAGNOSTIC_COMPLETED_EVENT, name=SEND_SCORE_JOB)
    async def send_score_to_prospect(session: AsyncSession, payload: dict[str, Any]) -> None:
        diagnostic_id = uuid.UUID(payload["diagnostic_id"])
        diagnostic = await session.get(DiagnosticSession, diagnostic_id)
        email = diagnostic.answers.get("email") if diagnostic else None
        if not isinstance(email, str) or not email.strip():
            return
        score = (
            await session.execute(
                select(DigitalScore).where(DigitalScore.session_id == diagnostic_id)
            )
        ).scalar_one_or_none()
        if score is None:
            return
        priorities = await _top_priorities(session, diagnostic_id)
        await sender.send(_score_email(email.strip(), score, priorities, app_url))
