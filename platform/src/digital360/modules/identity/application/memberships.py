"""Appartenances aux organisations, exposées aux autres modules.

Ces fonctions s'exécutent dans la transaction de l'appelant, déjà placée dans le bon
contexte tenant (RLS).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from digital360.core.permissions import ClientRole
from digital360.modules.identity.infrastructure.models import Membership, MembershipStatus, User


@dataclass(frozen=True)
class MemberView:
    user_id: uuid.UUID
    full_name: str
    email: str
    role: ClientRole
    joined_at: datetime


async def grant_membership(
    session: AsyncSession, *, organization_id: uuid.UUID, user_id: uuid.UUID, role: ClientRole
) -> Membership:
    membership = Membership(organization_id=organization_id, user_id=user_id, role=role.value)
    session.add(membership)
    await session.flush()
    return membership


async def list_members(session: AsyncSession, organization_id: uuid.UUID) -> list[MemberView]:
    rows = await session.execute(
        select(User.id, User.full_name, User.email, Membership.role, Membership.created_at)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.organization_id == organization_id,
            Membership.status == MembershipStatus.ACTIVE,
            User.deleted_at.is_(None),
        )
        .order_by(Membership.created_at)
    )
    return [
        MemberView(user_id, full_name, email, ClientRole(role), joined_at)
        for user_id, full_name, email, role, joined_at in rows
    ]
