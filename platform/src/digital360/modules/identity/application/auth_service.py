"""Inscription, connexion, sessions et chargement du Principal (ARCHITECTURE.md §5.1)."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.actor import Actor
from digital360.core.audit import record_audit
from digital360.core.errors import AppError
from digital360.core.permissions import ClientRole, Principal, StaffRole
from digital360.core.tenancy import set_user_scope
from digital360.modules.identity.domain.passwords import password_problems
from digital360.modules.identity.infrastructure.models import (
    Membership,
    MembershipStatus,
    StaffRoleAssignment,
    User,
    UserSession,
)


class PasswordHasher(Protocol):
    dummy_hash: str

    def hash(self, password: str) -> str: ...
    def verify(self, password_hash: str, password: str) -> bool: ...
    def needs_rehash(self, password_hash: str) -> bool: ...


@dataclass(frozen=True)
class ClientInfo:
    """Informations de la requête conservées dans la session et l'audit."""

    ip: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class IssuedSession:
    token: str
    absolute_expires_at: datetime


@dataclass(frozen=True)
class AuthenticatedUser:
    user: User
    principal: Principal


def invalid_credentials() -> AppError:
    # Même message que l'email soit inconnu ou le mot de passe faux : pas d'énumération de comptes
    return AppError(
        "INVALID_CREDENTIALS",
        "Email ou mot de passe incorrect.",
        status=401,
        title="Connexion refusée",
    )


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        hasher: PasswordHasher,
        *,
        idle_timeout: timedelta,
        absolute_timeout: timedelta,
    ) -> None:
        self._session_factory = session_factory
        self._hasher = hasher
        self._idle_timeout = idle_timeout
        self._absolute_timeout = absolute_timeout

    async def register(
        self, *, email: str, password: str, full_name: str, phone: str | None, client: ClientInfo
    ) -> tuple[User, IssuedSession]:
        email = normalize_email(email)
        problems = password_problems(password, email=email)
        if problems:
            raise AppError(
                "WEAK_PASSWORD",
                "Le mot de passe ne respecte pas les règles de sécurité.",
                errors=[{"field": "password", "reason": problem} for problem in problems],
            )
        user = User(
            email=email,
            full_name=full_name.strip(),
            phone=phone,
            password_hash=self._hasher.hash(password),
        )
        try:
            async with self._session_factory() as session, session.begin():
                session.add(user)
                await session.flush()
                issued = await self._create_session(session, user.id, client)
                await record_audit(
                    session,
                    actor=Actor.user(user.id),
                    action="user.register",
                    entity_type="user",
                    entity_id=user.id,
                    ip=client.ip,
                    user_agent=client.user_agent,
                )
        except IntegrityError as exc:
            raise AppError(
                "ALREADY_EXISTS", "Un compte existe déjà avec cet email.", status=409
            ) from exc
        return user, issued

    async def login(
        self, *, email: str, password: str, client: ClientInfo
    ) -> tuple[User, IssuedSession]:
        email = normalize_email(email)
        async with self._session_factory() as session, session.begin():
            user = (
                await session.execute(
                    select(User).where(func.lower(User.email) == email, User.deleted_at.is_(None))
                )
            ).scalar_one_or_none()

            if user is None:
                self._hasher.verify(self._hasher.dummy_hash, password)
                is_valid = False
            else:
                is_valid = self._hasher.verify(user.password_hash, password)

            if user is None or not is_valid:
                await record_audit(
                    session,
                    actor=Actor.user(user.id) if user else Actor.system("auth"),
                    action="user.login_failed",
                    entity_type="user",
                    entity_id=user.id if user else None,
                    new_value={"email": email},
                    ip=client.ip,
                    user_agent=client.user_agent,
                )
                failed = True
            else:
                failed = False
                if self._hasher.needs_rehash(user.password_hash):
                    user.password_hash = self._hasher.hash(password)
                user.last_login_at = datetime.now(UTC)
                issued = await self._create_session(session, user.id, client)
                await record_audit(
                    session,
                    actor=Actor.user(user.id),
                    action="user.login",
                    entity_type="user",
                    entity_id=user.id,
                    ip=client.ip,
                    user_agent=client.user_agent,
                )
        # L'échec est levé après validation de la transaction, pour que l'audit soit conservé
        if failed or user is None:
            raise invalid_credentials()
        return user, issued

    async def logout(self, token: str) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(UserSession)
                .where(UserSession.id == hash_token(token), UserSession.revoked_at.is_(None))
                .values(revoked_at=func.now())
            )

    async def authenticate(self, token: str) -> AuthenticatedUser | None:
        """Charge l'utilisateur et ses droits depuis un jeton de session ; None si invalide."""
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            row = (
                await session.execute(
                    select(UserSession, User)
                    .join(User, User.id == UserSession.user_id)
                    .where(
                        UserSession.id == hash_token(token),
                        UserSession.revoked_at.is_(None),
                        UserSession.expires_at > now,
                        UserSession.absolute_expires_at > now,
                        User.deleted_at.is_(None),
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            user_session, user = row

            # Expiration glissante, bornée par l'expiration absolue ; écrite au plus une
            # fois par demi-période pour éviter une écriture à chaque requête
            if user_session.expires_at - now < self._idle_timeout / 2:
                user_session.expires_at = min(
                    now + self._idle_timeout, user_session.absolute_expires_at
                )

            principal = await self._load_principal(session, user.id)
        return AuthenticatedUser(user=user, principal=principal)

    async def update_profile(
        self, user_id: uuid.UUID, *, full_name: str | None, phone: str | None, client: ClientInfo
    ) -> User:
        async with self._session_factory() as session, session.begin():
            user = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
            old = {"full_name": user.full_name, "phone": user.phone}
            if full_name is not None:
                user.full_name = full_name.strip()
            if phone is not None:
                user.phone = phone or None
            await record_audit(
                session,
                actor=Actor.user(user_id),
                action="user.update_profile",
                entity_type="user",
                entity_id=user_id,
                old_value=old,
                new_value={"full_name": user.full_name, "phone": user.phone},
                ip=client.ip,
                user_agent=client.user_agent,
            )
            await session.flush()
            await session.refresh(user)
        return user

    async def _create_session(
        self, session: AsyncSession, user_id: uuid.UUID, client: ClientInfo
    ) -> IssuedSession:
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        absolute = now + self._absolute_timeout
        session.add(
            UserSession(
                id=hash_token(token),
                user_id=user_id,
                expires_at=min(now + self._idle_timeout, absolute),
                absolute_expires_at=absolute,
                ip=client.ip,
                user_agent=client.user_agent,
            )
        )
        await session.flush()
        return IssuedSession(token=token, absolute_expires_at=absolute)

    async def _load_principal(self, session: AsyncSession, user_id: uuid.UUID) -> Principal:
        staff_roles = (
            await session.execute(
                select(StaffRoleAssignment.role).where(StaffRoleAssignment.user_id == user_id)
            )
        ).scalars()
        # Les appartenances sont protégées par RLS : visibles via app.current_user_id
        await set_user_scope(session, user_id)
        memberships = (
            await session.execute(
                select(Membership.organization_id, Membership.role).where(
                    Membership.user_id == user_id,
                    Membership.status == MembershipStatus.ACTIVE,
                )
            )
        ).all()
        return Principal(
            user_id=user_id,
            staff_roles=frozenset(StaffRole(role) for role in staff_roles),
            memberships={org_id: ClientRole(role) for org_id, role in memberships},
        )
