"""Équipe d'une entreprise : inviter un collaborateur, annuler, retirer un membre.

Une invitation part toujours par e-mail, que le compte existe ou non : le propriétaire
n'apprend pas si une adresse a déjà un compte, et la personne prouve qu'elle reçoit les
e-mails de cette adresse. Elle accepte en étant connectée avec l'adresse invitée (après
s'être inscrite si besoin).
"""

import html
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.actor import Actor
from digital360.core.audit import record_audit
from digital360.core.email import EmailMessage, EmailSender
from digital360.core.errors import AppError
from digital360.core.jobs import JobRegistry, enqueue
from digital360.core.permissions import ClientRole
from digital360.core.tenancy import TenantContext, staff_transaction, tenant_transaction
from digital360.modules.identity.application.auth_service import hash_token, normalize_email
from digital360.modules.identity.application.memberships import grant_membership
from digital360.modules.identity.infrastructure.models import Membership, MembershipStatus, User
from digital360.modules.organizations.infrastructure.models import (
    InvitationStatus,
    Organization,
    OrganizationInvitation,
)

SEND_INVITATION_JOB = "organizations.send_invitation"
INVITATION_TTL = timedelta(days=7)
MAX_PENDING_INVITATIONS = 20
ROLE_LABELS = {
    ClientRole.CLIENT_OWNER.value: "responsable (peut acheter et tout gérer)",
    ClientRole.CLIENT_MEMBER.value: "membre (consulte l'espace)",
}


@dataclass(frozen=True)
class InvitationView:
    id: uuid.UUID
    email: str
    role: str
    status: str
    expires_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class AcceptedInvitation:
    organization_id: uuid.UUID
    organization_name: str
    role: str


def _view(invitation: OrganizationInvitation) -> InvitationView:
    return InvitationView(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        status=invitation.status,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
    )


def _invalid_invitation() -> AppError:
    return AppError(
        "INVALID_INVITATION",
        "Cette invitation n'est plus valable. Demandez au responsable de vous réinviter.",
        status=400,
    )


def _masked(email: str) -> str:
    """« awa.kone@exemple.ci » → « aw***@exemple.ci » (sans révéler toute l'adresse)."""
    local, _, domain = email.partition("@")
    return f"{local[:2]}***@{domain}"


class TeamService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def invite(
        self, context: TenantContext, inviter_id: uuid.UUID, *, email: str, role: ClientRole
    ) -> InvitationView:
        email = normalize_email(email)
        async with tenant_transaction(self._session_factory, context) as session:
            already_member = await session.scalar(
                select(Membership.id)
                .join(User, User.id == Membership.user_id)
                .where(
                    Membership.organization_id == context.organization_id,
                    Membership.status == MembershipStatus.ACTIVE.value,
                    func.lower(User.email) == email,
                )
            )
            if already_member is not None:
                raise AppError(
                    "ALREADY_MEMBER", "Cette personne fait déjà partie de l'équipe.", status=409
                )
            invitation = await session.scalar(
                select(OrganizationInvitation).where(
                    OrganizationInvitation.organization_id == context.organization_id,
                    OrganizationInvitation.email == email,
                    OrganizationInvitation.status == InvitationStatus.PENDING.value,
                )
            )
            if invitation is None:
                pending = await session.scalar(
                    select(func.count())
                    .select_from(OrganizationInvitation)
                    .where(
                        OrganizationInvitation.organization_id == context.organization_id,
                        OrganizationInvitation.status == InvitationStatus.PENDING.value,
                    )
                )
                if (pending or 0) >= MAX_PENDING_INVITATIONS:
                    raise AppError(
                        "TOO_MANY_INVITATIONS",
                        "Trop d'invitations en attente : annulez-en avant d'en envoyer d'autres.",
                        status=422,
                    )
                invitation = OrganizationInvitation(
                    organization_id=context.organization_id,
                    email=email,
                    role=role.value,
                    invited_by=inviter_id,
                )
                session.add(invitation)
            else:
                # Réinviter la même adresse renvoie l'e-mail (nouveau lien) avec le rôle choisi
                invitation.role = role.value
            await session.flush()
            await record_audit(
                session,
                actor=Actor.user(inviter_id),
                action="organization.invite",
                entity_type="organization_invitation",
                entity_id=invitation.id,
                organization_id=context.organization_id,
                new_value={"email": email, "role": role.value},
            )
            await enqueue(
                session,
                SEND_INVITATION_JOB,
                {"invitation_id": str(invitation.id), "inviter_id": str(inviter_id)},
                organization_id=context.organization_id,
            )
            await session.refresh(invitation)
            return _view(invitation)

    async def pending(self, context: TenantContext) -> list[InvitationView]:
        async with tenant_transaction(self._session_factory, context) as session:
            rows = await session.scalars(
                select(OrganizationInvitation)
                .where(
                    OrganizationInvitation.organization_id == context.organization_id,
                    OrganizationInvitation.status == InvitationStatus.PENDING.value,
                )
                .order_by(OrganizationInvitation.created_at.desc())
            )
            return [_view(item) for item in rows]

    async def cancel(
        self, context: TenantContext, actor_id: uuid.UUID, invitation_id: uuid.UUID
    ) -> None:
        async with tenant_transaction(self._session_factory, context) as session:
            invitation = await session.scalar(
                select(OrganizationInvitation).where(
                    OrganizationInvitation.id == invitation_id,
                    OrganizationInvitation.organization_id == context.organization_id,
                    OrganizationInvitation.status == InvitationStatus.PENDING.value,
                )
            )
            if invitation is None:
                raise AppError("NOT_FOUND", "Invitation introuvable.", status=404)
            invitation.status = InvitationStatus.CANCELLED.value
            invitation.token_hash = None
            await record_audit(
                session,
                actor=Actor.user(actor_id),
                action="organization.invitation_cancel",
                entity_type="organization_invitation",
                entity_id=invitation.id,
                organization_id=context.organization_id,
            )

    async def remove_member(
        self, context: TenantContext, actor_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        async with tenant_transaction(self._session_factory, context) as session:
            membership = await session.scalar(
                select(Membership)
                .where(
                    Membership.organization_id == context.organization_id,
                    Membership.user_id == user_id,
                    Membership.status == MembershipStatus.ACTIVE.value,
                )
                .with_for_update()
            )
            if membership is None:
                raise AppError("NOT_FOUND", "Membre introuvable.", status=404)
            if membership.role == ClientRole.CLIENT_OWNER.value:
                owners = await session.scalar(
                    select(func.count())
                    .select_from(Membership)
                    .where(
                        Membership.organization_id == context.organization_id,
                        Membership.role == ClientRole.CLIENT_OWNER.value,
                        Membership.status == MembershipStatus.ACTIVE.value,
                    )
                )
                if (owners or 0) <= 1:
                    raise AppError(
                        "LAST_OWNER",
                        "L'entreprise doit garder au moins un responsable.",
                        status=409,
                    )
            membership.status = MembershipStatus.REVOKED.value
            await record_audit(
                session,
                actor=Actor.user(actor_id),
                action="organization.member_remove",
                entity_type="membership",
                entity_id=membership.id,
                organization_id=context.organization_id,
                old_value={"user_id": str(user_id), "role": membership.role},
            )

    async def accept(self, user_id: uuid.UUID, token: str) -> AcceptedInvitation:
        """Le lien de l'e-mail, ouvert en étant connecté avec l'adresse invitée."""
        async with staff_transaction(self._session_factory) as session:
            invitation = await session.scalar(
                select(OrganizationInvitation)
                .where(OrganizationInvitation.token_hash == hash_token(token))
                .with_for_update()
            )
            now = datetime.now(UTC)
            if (
                invitation is None
                or invitation.status != InvitationStatus.PENDING.value
                or invitation.expires_at is None
                or invitation.expires_at <= now
            ):
                raise _invalid_invitation()
            user = await session.get(User, user_id)
            if user is None or normalize_email(user.email) != invitation.email:
                raise AppError(
                    "INVITATION_EMAIL_MISMATCH",
                    f"Cette invitation a été envoyée à {_masked(invitation.email)} : connectez-"
                    "vous (ou inscrivez-vous) avec cette adresse pour l'accepter.",
                    status=403,
                )
            organization = await session.get(Organization, invitation.organization_id)
            if organization is None or organization.deleted_at is not None:
                raise _invalid_invitation()
            membership = await session.scalar(
                select(Membership).where(
                    Membership.organization_id == invitation.organization_id,
                    Membership.user_id == user_id,
                )
            )
            if membership is None:
                await grant_membership(
                    session,
                    organization_id=invitation.organization_id,
                    user_id=user_id,
                    role=ClientRole(invitation.role),
                )
            elif membership.status != MembershipStatus.ACTIVE.value:
                # Ancien membre retiré puis réinvité
                membership.status = MembershipStatus.ACTIVE.value
                membership.role = invitation.role
            invitation.status = InvitationStatus.ACCEPTED.value
            invitation.accepted_by = user_id
            invitation.accepted_at = now
            invitation.token_hash = None
            await record_audit(
                session,
                actor=Actor.user(user_id),
                action="organization.invitation_accept",
                entity_type="organization_invitation",
                entity_id=invitation.id,
                organization_id=invitation.organization_id,
                new_value={"role": invitation.role},
            )
            return AcceptedInvitation(
                organization_id=organization.id,
                organization_name=organization.commercial_name,
                role=invitation.role,
            )


def _invitation_email(
    to: str, inviter: str, organization: str, role: str, link: str
) -> EmailMessage:
    paragraphs = [
        "Bonjour,",
        f"{inviter} vous invite à rejoindre l'espace Digital360 de {organization}, en tant que "
        f"{ROLE_LABELS.get(role, role)}.",
        "Pour accepter, ouvrez le lien ci-dessous puis connectez-vous, ou créez votre compte, "
        f"avec cette adresse : {to}. Le lien est valable 7 jours.",
        "Si vous ne connaissez pas cette personne, ignorez simplement cet e-mail.",
    ]
    text = "\n\n".join(paragraphs) + f"\n\nAccepter l'invitation : {link}\n\nL'équipe BENILAB"
    body = "".join(f"<p>{html.escape(item)}</p>" for item in paragraphs)
    body += f'<p><a href="{html.escape(link, quote=True)}">Accepter l\'invitation</a></p>'
    body += "<p>L'équipe BENILAB</p>"
    return EmailMessage(
        to=[to],
        subject=f"Invitation à rejoindre {organization} sur Digital360",
        text=text,
        html=body,
    )


def register_jobs(registry: JobRegistry, sender: EmailSender, *, app_url: str) -> None:
    @registry.job(SEND_INVITATION_JOB)
    async def send_invitation(session: AsyncSession, payload: dict[str, Any]) -> None:
        invitation = await session.get(OrganizationInvitation, uuid.UUID(payload["invitation_id"]))
        if invitation is None or invitation.status != InvitationStatus.PENDING.value:
            return
        organization = await session.get(Organization, invitation.organization_id)
        inviter = await session.get(User, uuid.UUID(payload["inviter_id"]))
        if organization is None:
            return
        token = secrets.token_urlsafe(32)
        invitation.token_hash = hash_token(token)
        invitation.expires_at = datetime.now(UTC) + INVITATION_TTL
        await session.flush()
        # Le jeton est après « # » : jamais envoyé aux serveurs ni dans l'en-tête Referer.
        # Envoi en dernier : si l'e-mail échoue, le nouveau jeton est annulé avec la transaction
        await sender.send(
            _invitation_email(
                invitation.email,
                inviter.full_name if inviter else "Votre équipe",
                organization.commercial_name,
                invitation.role,
                f"{app_url}#invitation={token}",
            )
        )
