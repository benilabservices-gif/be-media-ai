"""Mot de passe oublié : demande de lien par email, puis choix d'un nouveau mot de passe.

Le jeton n'est jamais stocké en clair, ni dans la file de tâches : la requête se contente
d'enfiler une tâche ; c'est la tâche qui génère le jeton, enregistre son empreinte et
envoie l'email. Le lien met le jeton après `#`, que les navigateurs n'envoient ni aux
serveurs ni dans l'en-tête Referer.
"""

import html
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.actor import Actor
from digital360.core.audit import record_audit
from digital360.core.email import EmailMessage, EmailSender
from digital360.core.errors import AppError
from digital360.core.jobs import JobRegistry, enqueue
from digital360.modules.identity.application.auth_service import (
    EMAIL_CHANGED_JOB,
    ClientInfo,
    PasswordHasher,
    hash_token,
    normalize_email,
)
from digital360.modules.identity.domain.passwords import password_problems
from digital360.modules.identity.infrastructure.models import (
    PasswordResetToken,
    User,
    UserSession,
)

SEND_PASSWORD_RESET_JOB = "identity.send_password_reset"  # noqa: S105 (nom de tâche)
RESET_TOKEN_TTL = timedelta(hours=1)


def invalid_reset_token() -> AppError:
    return AppError(
        "INVALID_RESET_TOKEN",
        "Ce lien n'est plus valable. Demandez un nouveau lien de réinitialisation.",
        status=400,
    )


class PasswordResetService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        hasher: PasswordHasher,
        *,
        reset_url: str,
    ) -> None:
        self._session_factory = session_factory
        self._hasher = hasher
        self._reset_url = reset_url

    async def request(self, email: str) -> None:
        """Toujours silencieux : l'appelant ne doit pas apprendre si le compte existe."""
        async with self._session_factory() as session, session.begin():
            user_id = (
                await session.execute(
                    select(User.id).where(
                        func.lower(User.email) == normalize_email(email),
                        User.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if user_id is not None:
                await enqueue(
                    session,
                    SEND_PASSWORD_RESET_JOB,
                    {"user_id": str(user_id), "reset_url": self._reset_url},
                )

    async def reset(self, *, token: str, new_password: str, client: ClientInfo) -> None:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            row = (
                await session.execute(
                    select(PasswordResetToken, User)
                    .join(User, User.id == PasswordResetToken.user_id)
                    .where(
                        PasswordResetToken.id == hash_token(token),
                        PasswordResetToken.used_at.is_(None),
                        PasswordResetToken.expires_at > now,
                        User.deleted_at.is_(None),
                    )
                    .with_for_update(of=PasswordResetToken)
                )
            ).one_or_none()
            if row is None:
                raise invalid_reset_token()
            _, user = row

            problems = password_problems(new_password, email=user.email)
            if problems:
                raise AppError(
                    "WEAK_PASSWORD",
                    "Le mot de passe ne respecte pas les règles de sécurité.",
                    errors=[{"field": "password", "reason": problem} for problem in problems],
                )

            user.password_hash = self._hasher.hash(new_password)
            # Le lien utilisé et tous les autres liens encore valides du compte sont grillés
            await session.execute(
                update(PasswordResetToken)
                .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
                .values(used_at=now)
            )
            # Quelqu'un connaissait peut-être l'ancien mot de passe : on ferme toutes les sessions
            await session.execute(
                update(UserSession)
                .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            await record_audit(
                session,
                actor=Actor.user(user.id),
                action="user.password_reset",
                entity_type="user",
                entity_id=user.id,
                ip=client.ip,
                user_agent=client.user_agent,
            )


def _reset_email(user: User, link: str) -> EmailMessage:
    name = user.full_name.split(" ")[0] if user.full_name else ""
    text = (
        f"Bonjour {name},\n\n"
        "Vous avez demandé à réinitialiser le mot de passe de votre compte BENILAB Digital360.\n"
        f"Choisissez un nouveau mot de passe en ouvrant ce lien (valable 1 heure) :\n\n{link}\n\n"
        "Si vous n'êtes pas à l'origine de cette demande, ignorez cet email : votre mot de passe "
        "actuel reste valable.\n\nL'équipe BENILAB"
    )
    safe_link = html.escape(link, quote=True)
    body = (
        f"<p>Bonjour {html.escape(name)},</p>"
        "<p>Vous avez demandé à réinitialiser le mot de passe de votre compte "
        "BENILAB Digital360.</p>"
        f'<p><a href="{safe_link}">Choisir un nouveau mot de passe</a> (lien valable 1 heure)</p>'
        "<p>Si vous n'êtes pas à l'origine de cette demande, ignorez cet email : votre mot de "
        "passe actuel reste valable.</p><p>L'équipe BENILAB</p>"
    )
    return EmailMessage(
        to=[user.email],
        subject="Réinitialisation de votre mot de passe",
        text=text,
        html=body,
    )


def _email_changed_message(old_email: str, new_email: str, name: str) -> EmailMessage:
    first = name.split(" ")[0] if name else ""
    paragraphs = [
        f"Bonjour {first},",
        "L'adresse de connexion de votre compte BENILAB Digital360 vient d'être remplacée par "
        f"{new_email}. Utilisez désormais cette adresse pour vous connecter.",
        "Si vous n'êtes pas à l'origine de ce changement, répondez immédiatement à cet e-mail : "
        "notre équipe sécurisera votre compte.",
    ]
    text = "\n\n".join(paragraphs) + "\n\nL'équipe BENILAB"
    body = "".join(f"<p>{html.escape(item)}</p>" for item in paragraphs) + "<p>L'équipe BENILAB</p>"
    return EmailMessage(
        to=[old_email],
        subject="L'adresse e-mail de votre compte a été modifiée",
        text=text,
        html=body,
    )


def register_jobs(registry: JobRegistry, sender: EmailSender) -> None:
    @registry.job(EMAIL_CHANGED_JOB)
    async def notify_email_changed(_: AsyncSession, payload: dict[str, Any]) -> None:
        await sender.send(
            _email_changed_message(payload["old_email"], payload["new_email"], payload["name"])
        )

    @registry.job(SEND_PASSWORD_RESET_JOB)
    async def send_password_reset(session: AsyncSession, payload: dict[str, Any]) -> None:
        user = await session.get(User, uuid.UUID(payload["user_id"]))
        if user is None or user.deleted_at is not None:
            return
        token = secrets.token_urlsafe(32)
        session.add(
            PasswordResetToken(
                id=hash_token(token),
                user_id=user.id,
                expires_at=datetime.now(UTC) + RESET_TOKEN_TTL,
            )
        )
        await session.flush()
        # Envoi en dernier : si l'email échoue, la transaction (et le jeton) est annulée
        await sender.send(_reset_email(user, f"{payload['reset_url']}#token={token}"))
