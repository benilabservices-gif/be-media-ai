"""Création du projet à la vente gagnée, et e-mails à chaque étape (client et équipe)."""

import html
import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from digital360.core.email import EmailMessage, EmailSender
from digital360.core.jobs import JobRegistry
from digital360.core.permissions import ClientRole
from digital360.modules.billing.application.service import PURCHASE_REQUEST_WON_EVENT
from digital360.modules.identity.infrastructure.models import Membership, MembershipStatus, User
from digital360.modules.organizations.infrastructure.models import Organization
from digital360.modules.projects.application.service import prefilled_brief
from digital360.modules.projects.domain import offer
from digital360.modules.projects.infrastructure.models import ProjectStatus, WebsiteProject

CREATE_PROJECT_JOB = "projects.create_on_won_sale"
NOTIFY_STATUS_JOB = "projects.notify_status_change"
STATUS_CHANGED_EVENT = "website_project.status_changed"


def _french_date(day: date) -> str:
    months = (
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    )  # fmt: skip
    return f"{day.day} {months[day.month - 1]} {day.year}"


def _message(
    to: list[str], subject: str, paragraphs: list[str], link: tuple[str, str] | None
) -> EmailMessage:
    """Texte et HTML à partir des mêmes paragraphes ; tout est échappé dans la version HTML."""
    text = "\n\n".join(paragraphs)
    body = "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)
    if link:
        label, url = link
        text += f"\n\n{label} : {url}"
        body += f'<p><a href="{html.escape(url, quote=True)}">{html.escape(label)}</a></p>'
    text += "\n\nL'équipe BENILAB Digital360"
    body += "<p>L'équipe BENILAB Digital360</p>"
    return EmailMessage(to=to, subject=subject, text=text, html=body)


async def _owner_emails(session: AsyncSession, organization_id: uuid.UUID) -> list[str]:
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


def register_jobs(
    registry: JobRegistry,
    sender: EmailSender,
    *,
    team_recipients: list[str],
    app_url: str,
    admin_url: str,
) -> None:
    @registry.on(PURCHASE_REQUEST_WON_EVENT, name=CREATE_PROJECT_JOB)
    async def create_on_won_sale(session: AsyncSession, payload: dict[str, Any]) -> None:
        if payload["product_code"] != offer.PRODUCT_CODE:
            return
        organization_id = uuid.UUID(payload["organization_id"])
        organization = await session.get(Organization, organization_id)
        if organization is None:
            return
        created = await session.execute(
            insert(WebsiteProject)
            .values(
                organization_id=organization_id,
                purchase_request_id=uuid.UUID(payload["purchase_request_id"]),
                product_code=offer.PRODUCT_CODE,
                brief=prefilled_brief(organization),
            )
            .on_conflict_do_nothing(
                index_elements=["purchase_request_id"],
                index_where=WebsiteProject.purchase_request_id.is_not(None),
            )
            .returning(WebsiteProject.id)
        )
        if created.scalar_one_or_none() is None:
            return  # tâche rejouée : projet déjà créé, e-mail déjà envoyé
        owners = await _owner_emails(session, organization_id)
        if not owners:
            return
        await sender.send(
            _message(
                owners,
                "Votre offre Start est activée : place à votre site",
                [
                    "Bonjour,",
                    "Merci pour votre confiance : votre offre Start est activée.",
                    offer.PITCH,
                    "Prochaine étape : remplissez votre brief (quelques minutes). Nous avons déjà "
                    "repris les informations de votre entreprise. Choisissez votre modèle et vos "
                    "couleurs, listez vos services, puis validez.",
                    f"Votre site sera en ligne sous {offer.DELIVERY_BUSINESS_DAYS} jours ouvrés "
                    "après réception de votre brief complet.",
                ],
                ("Remplir mon brief", app_url + "dashboard.html"),
            )
        )

    @registry.on(STATUS_CHANGED_EVENT, name=NOTIFY_STATUS_JOB)
    async def notify_status_change(session: AsyncSession, payload: dict[str, Any]) -> None:
        project = await session.get(WebsiteProject, uuid.UUID(payload["entity_id"]))
        if project is None:
            return
        target = payload["to"]
        owners = await _owner_emails(session, project.organization_id)
        name = await session.scalar(
            select(Organization.commercial_name).where(Organization.id == project.organization_id)
        )
        space = ("Ouvrir mon espace", app_url + "dashboard.html")
        messages: list[EmailMessage] = []
        if target == ProjectStatus.IN_PRODUCTION and project.due_on:
            due = _french_date(project.due_on)
            messages.append(
                _message(
                    owners,
                    "Brief reçu : votre site est en préparation",
                    [
                        "Bonjour,",
                        "Nous avons bien reçu votre brief. Votre site est en préparation.",
                        f"Vous recevrez la première version au plus tard le {due} pour la relire.",
                    ],
                    space,
                )
            )
            if team_recipients:
                messages.append(
                    _message(
                        team_recipients,
                        f"Nouveau site Start à produire : {name}",
                        [
                            f"{name} a envoyé son brief. Livraison promise le {due}.",
                            f"Modèle : {project.brief.get('template')} · "
                            f"Palette : {project.brief.get('palette')} · "
                            f"Domaine souhaité : {project.brief.get('desired_domain') or 'à définir'}",
                        ],
                        ("Ouvrir les projets", admin_url),
                    )
                )
        elif target == ProjectStatus.CLIENT_REVIEW and project.preview_url:
            messages.append(
                _message(
                    owners,
                    "Votre site est prêt : à vous de le relire",
                    [
                        "Bonjour,",
                        "La première version de votre site est prête.",
                        "Depuis votre espace, validez-la ou demandez vos corrections de contenu "
                        "(textes, photos, coordonnées, horaires). Une série de corrections est "
                        "incluse dans votre offre.",
                    ],
                    ("Voir mon site", project.preview_url),
                )
            )
        elif target == ProjectStatus.REVISION and team_recipients:
            count = len(project.revision_requests[-1]["items"]) if project.revision_requests else 0
            messages.append(
                _message(
                    team_recipients,
                    f"Corrections demandées : {name}",
                    [f"{name} a demandé {count} correction(s) de contenu sur son site."],
                    ("Ouvrir les projets", admin_url),
                )
            )
        elif target == ProjectStatus.LIVE and project.live_url:
            messages.append(
                _message(
                    owners,
                    "Votre site est en ligne",
                    [
                        "Bonjour,",
                        "Félicitations : votre site est en ligne et visible par vos clients.",
                        "Pour gagner en visibilité (Google, réseaux sociaux, publications "
                        "régulières), découvrez nos accompagnements depuis votre espace.",
                    ],
                    ("Voir mon site", project.live_url),
                )
            )
        for message in messages:
            if message.to:
                await sender.send(message)
