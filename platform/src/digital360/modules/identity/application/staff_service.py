"""Équipe BENILAB : rôles staff, et statistiques sur les comptes pour le tableau de bord admin."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.actor import Actor
from digital360.core.audit import record_audit
from digital360.core.errors import AppError
from digital360.core.permissions import StaffRole
from digital360.core.tenancy import staff_transaction
from digital360.modules.identity.application.auth_service import normalize_email
from digital360.modules.identity.infrastructure.models import StaffRoleAssignment, User


@dataclass(frozen=True)
class StaffMember:
    user_id: uuid.UUID
    email: str
    full_name: str
    roles: list[StaffRole] = field(default_factory=list)
    last_login_at: datetime | None = None


@dataclass(frozen=True)
class UserStats:
    total: int
    created_last_7_days: int


class StaffService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_staff(self) -> list[StaffMember]:
        async with staff_transaction(self._session_factory) as session:
            return await self._list(session)

    async def grant(self, actor: Actor, *, email: str, role: StaffRole) -> StaffMember:
        async with staff_transaction(self._session_factory) as session:
            user = (
                await session.execute(
                    select(User).where(
                        func.lower(User.email) == normalize_email(email), User.deleted_at.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if user is None:
                raise AppError(
                    "NOT_FOUND",
                    "Aucun compte avec cet email : la personne doit d'abord créer son compte.",
                    status=404,
                )
            inserted = await session.execute(
                insert(StaffRoleAssignment)
                .values(user_id=user.id, role=role.value, granted_by=actor.user_id)
                .on_conflict_do_nothing()
                .returning(StaffRoleAssignment.role)
            )
            if inserted.scalar_one_or_none() is not None:
                await record_audit(
                    session,
                    actor=actor,
                    action="staff_role.grant",
                    entity_type="user",
                    entity_id=user.id,
                    new_value={"role": role.value},
                )
            return next(member for member in await self._list(session) if member.user_id == user.id)

    async def revoke(self, actor: Actor, *, user_id: uuid.UUID, role: StaffRole) -> None:
        async with staff_transaction(self._session_factory) as session:
            # Seul un ADMIN gère l'équipe : lui interdire de se retirer garantit qu'il en reste un
            if role is StaffRole.ADMIN and user_id == actor.user_id:
                raise AppError(
                    "FORBIDDEN",
                    "Vous ne pouvez pas retirer votre propre rôle administrateur.",
                    status=403,
                )
            result = await session.execute(
                delete(StaffRoleAssignment)
                .where(
                    StaffRoleAssignment.user_id == user_id,
                    StaffRoleAssignment.role == role.value,
                )
                .returning(StaffRoleAssignment.role)
            )
            if result.scalar_one_or_none() is None:
                raise AppError("NOT_FOUND", "Ce rôle n'est pas attribué.", status=404)
            await record_audit(
                session,
                actor=actor,
                action="staff_role.revoke",
                entity_type="user",
                entity_id=user_id,
                old_value={"role": role.value},
            )

    async def user_stats(self) -> UserStats:
        week_ago = datetime.now(UTC) - timedelta(days=7)
        async with staff_transaction(self._session_factory) as session:
            total = await session.scalar(
                select(func.count()).select_from(User).where(User.deleted_at.is_(None))
            )
            recent = await session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.deleted_at.is_(None), User.created_at >= week_ago)
            )
        return UserStats(total=total or 0, created_last_7_days=recent or 0)

    async def _list(self, session: AsyncSession) -> list[StaffMember]:
        rows = (
            await session.execute(
                select(User, StaffRoleAssignment.role)
                .join(StaffRoleAssignment, StaffRoleAssignment.user_id == User.id)
                .where(User.deleted_at.is_(None))
                .order_by(User.full_name)
            )
        ).all()
        members: dict[uuid.UUID, StaffMember] = {}
        for user, role in rows:
            member = members.setdefault(
                user.id,
                StaffMember(user.id, user.email, user.full_name, [], user.last_login_at),
            )
            member.roles.append(StaffRole(role))
        for member in members.values():
            member.roles.sort()
        return list(members.values())
