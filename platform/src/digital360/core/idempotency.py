"""Clés d'idempotence des requêtes critiques (header `Idempotency-Key`, ARCHITECTURE.md §12.4).

Usage dans un endpoint (commande, paiement) :

    stored = await reserve(session, scope, key, request_hash)
    if stored:
        return stored  # même requête déjà traitée : on renvoie la même réponse
    ... traitement ...
    await complete(session, scope, key, status_code, body)

Tout se passe dans la transaction métier : si le traitement échoue, la réservation est
annulée avec lui et la requête peut être rejouée.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from digital360.core.db import Base
from digital360.core.errors import AppError

MAX_KEY_LENGTH = 255


class IdempotencyStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        CheckConstraint("status IN ('IN_PROGRESS', 'COMPLETED')", name="status_valid"),
    )

    # Portée : utilisateur ou organisation + route, pour qu'une clé ne fuite pas d'un client à l'autre
    scope: Mapped[str] = mapped_column(String(300), primary_key=True)
    key: Mapped[str] = mapped_column(String(MAX_KEY_LENGTH), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[Any] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True)
class StoredResponse:
    status_code: int
    body: Any


def hash_request(method: str, path: str, body: bytes) -> str:
    digest = hashlib.sha256()
    for part in (method.upper().encode(), path.encode(), body):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


async def reserve(
    session: AsyncSession, scope: str, key: str, request_hash: str
) -> StoredResponse | None:
    """Réserve la clé. Renvoie la réponse mémorisée si la même requête a déjà abouti."""
    if not key or len(key) > MAX_KEY_LENGTH:
        raise AppError(
            "VALIDATION_ERROR",
            f"Idempotency-Key doit faire entre 1 et {MAX_KEY_LENGTH} caractères.",
        )
    inserted = await session.execute(
        insert(IdempotencyKey)
        .values(
            scope=scope,
            key=key,
            request_hash=request_hash,
            status=IdempotencyStatus.IN_PROGRESS,
        )
        .on_conflict_do_nothing()
        .returning(IdempotencyKey.key)
    )
    if inserted.scalar_one_or_none() is not None:
        return None

    existing = (
        await session.execute(
            select(IdempotencyKey).where(IdempotencyKey.scope == scope, IdempotencyKey.key == key)
        )
    ).scalar_one()
    if existing.request_hash != request_hash:
        raise AppError(
            "IDEMPOTENCY_KEY_REUSED",
            "Cette clé d'idempotence a déjà servi pour une requête différente.",
            status=422,
        )
    if existing.status == IdempotencyStatus.IN_PROGRESS or existing.response_status is None:
        raise AppError(
            "CONFLICT",
            "Une requête identique est en cours de traitement. Réessayez dans un instant.",
            status=409,
        )
    return StoredResponse(existing.response_status, existing.response_body)


async def complete(
    session: AsyncSession, scope: str, key: str, status_code: int, body: Any
) -> None:
    await session.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.scope == scope, IdempotencyKey.key == key)
        .values(
            status=IdempotencyStatus.COMPLETED,
            response_status=status_code,
            response_body=body,
            completed_at=func.now(),
        )
    )
