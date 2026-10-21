"""File de tâches sur PostgreSQL, qui sert aussi d'outbox (ARCHITECTURE.md §12).

- `enqueue` insère la tâche dans la transaction métier en cours : elle n'existe que si
  cette transaction est validée, et ne peut pas être perdue après validation.
- `publish` diffuse un événement de domaine : une tâche par abonné.
- `Worker` réserve les tâches avec `FOR UPDATE SKIP LOCKED` (plusieurs workers possibles),
  exécute le handler et son marquage « terminé » dans une même transaction, et replanifie
  avec un délai croissant en cas d'échec, jusqu'à l'état DEAD visible dans l'admin.

Un handler peut être exécuté plus d'une fois (crash après un appel externe) : il doit
être idempotent.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from digital360.core.db import Base
from digital360.core.ids import uuid7
from digital360.core.logging import request_id_var
from digital360.core.tenancy import staff_transaction

logger = logging.getLogger("digital360.jobs")

MAX_ERROR_LENGTH = 2000


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    DEAD = "DEAD"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'DEAD')", name="status_valid"
        ),
        Index("ix_jobs_pending_run_at", "run_at", postgresql_where=text("status = 'PENDING'")),
        Index(
            "uq_jobs_dedup_key",
            "dedup_key",
            unique=True,
            postgresql_where=text("dedup_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), server_default=JobStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(Text)
    dedup_key: Mapped[str | None] = mapped_column(String(300))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    request_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


JobHandler = Callable[[AsyncSession, dict[str, Any]], Awaitable[None]]


class JobRegistry:
    """Associe un nom de tâche (persisté en base, donc stable) à son handler."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}
        self._subscribers: dict[str, list[str]] = {}

    def job(self, name: str) -> Callable[[JobHandler], JobHandler]:
        def register(handler: JobHandler) -> JobHandler:
            if name in self._handlers:
                raise ValueError(f"tâche {name!r} déjà enregistrée")
            self._handlers[name] = handler
            return handler

        return register

    def on(self, event: str, *, name: str) -> Callable[[JobHandler], JobHandler]:
        """Abonne un handler à un événement de domaine."""

        def register(handler: JobHandler) -> JobHandler:
            self.job(name)(handler)
            self._subscribers.setdefault(event, []).append(name)
            return handler

        return register

    def handler(self, name: str) -> JobHandler | None:
        return self._handlers.get(name)

    def subscribers(self, event: str) -> list[str]:
        return list(self._subscribers.get(event, []))


async def enqueue(
    session: AsyncSession,
    name: str,
    payload: dict[str, Any],
    *,
    dedup_key: str | None = None,
    run_at: datetime | None = None,
    organization_id: uuid.UUID | None = None,
    max_attempts: int = 5,
) -> uuid.UUID | None:
    """Ajoute une tâche. Avec `dedup_key`, une tâche déjà connue n'est pas recréée (renvoie None)."""
    values: dict[str, Any] = {
        "id": uuid7(),
        "name": name,
        "payload": payload,
        "max_attempts": max_attempts,
        "dedup_key": dedup_key,
        "organization_id": organization_id,
        "request_id": request_id_var.get(),
    }
    if run_at is not None:
        values["run_at"] = run_at
    statement = (
        insert(Job)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=["dedup_key"], index_where=text("dedup_key IS NOT NULL")
        )
        .returning(Job.id)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def publish(
    session: AsyncSession,
    registry: JobRegistry,
    event: str,
    payload: dict[str, Any],
    *,
    event_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Publie un événement de domaine : une tâche par abonné, dédupliquée par (événement, abonné)."""
    event_id = event_id or uuid7()
    message = {"event": event, "event_id": str(event_id), **payload}
    for subscriber in registry.subscribers(event):
        await enqueue(
            session,
            subscriber,
            message,
            dedup_key=f"{event_id}:{subscriber}",
            organization_id=organization_id,
        )
    return event_id


def default_backoff(attempts: int) -> timedelta:
    """10 s, 20 s, 40 s… plafonné à 1 h."""
    return timedelta(seconds=min(10 * 2 ** (attempts - 1), 3600))


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    name: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    request_id: str | None


class Worker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        registry: JobRegistry,
        *,
        worker_id: str,
        poll_interval: float = 1.0,
        lock_timeout: timedelta = timedelta(minutes=10),
        backoff: Callable[[int], timedelta] = default_backoff,
    ) -> None:
        self.session_factory = session_factory
        self.registry = registry
        self.worker_id = worker_id
        self.poll_interval = poll_interval
        self.lock_timeout = lock_timeout
        self.backoff = backoff

    async def claim(self) -> ClaimedJob | None:
        next_job = (
            select(Job.id)
            .where(Job.status == JobStatus.PENDING, Job.run_at <= func.now())
            .order_by(Job.run_at)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        statement = (
            update(Job)
            .where(Job.id == next_job)
            .values(
                status=JobStatus.RUNNING,
                locked_at=func.now(),
                locked_by=self.worker_id,
                attempts=Job.attempts + 1,
            )
            .returning(
                Job.id, Job.name, Job.payload, Job.attempts, Job.max_attempts, Job.request_id
            )
        )
        async with staff_transaction(self.session_factory) as session:
            row = (await session.execute(statement)).one_or_none()
        return ClaimedJob(*row) if row else None

    async def run_once(self) -> bool:
        """Traite au plus une tâche. Renvoie False si la file est vide."""
        job = await self.claim()
        if job is None:
            return False

        request_id_var.set(job.request_id)
        handler = self.registry.handler(job.name)
        if handler is None:
            await self._fail(job, f"aucun handler enregistré pour {job.name!r}", final=True)
            return True

        try:
            async with staff_transaction(self.session_factory) as session:
                await handler(session, job.payload)
                await session.execute(
                    update(Job)
                    .where(Job.id == job.id, Job.locked_by == self.worker_id)
                    .values(status=JobStatus.SUCCEEDED, finished_at=func.now(), locked_at=None)
                )
        except Exception as exc:
            logger.exception("échec de la tâche", extra={"job_id": str(job.id), "job": job.name})
            await self._fail(job, f"{type(exc).__name__}: {exc}", final=False)
        return True

    async def _fail(self, job: ClaimedJob, error: str, *, final: bool) -> None:
        is_dead = final or job.attempts >= job.max_attempts
        values: dict[str, Any] = {"last_error": error[:MAX_ERROR_LENGTH], "locked_at": None}
        if is_dead:
            values |= {"status": JobStatus.DEAD, "finished_at": func.now()}
            logger.error("tâche abandonnée", extra={"job_id": str(job.id), "job": job.name})
        else:
            values |= {
                "status": JobStatus.PENDING,
                "run_at": func.now() + self.backoff(job.attempts),
            }
        async with staff_transaction(self.session_factory) as session:
            await session.execute(update(Job).where(Job.id == job.id).values(**values))

    async def recover_stale(self) -> int:
        """Remet en file les tâches RUNNING dont le worker a disparu (crash, redéploiement)."""
        async with staff_transaction(self.session_factory) as session:
            result = await session.execute(
                update(Job)
                .where(
                    Job.status == JobStatus.RUNNING,
                    Job.locked_at < func.now() - self.lock_timeout,
                )
                .values(status=JobStatus.PENDING, locked_at=None, locked_by=None)
                .returning(Job.id)
            )
            return len(result.all())

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.recover_stale()
                while not stop.is_set() and await self.run_once():
                    pass
            except Exception:
                # Base momentanément injoignable : on réessaie au tour suivant au lieu de
                # mourir (le worker intégré à l'API ne serait pas redémarré)
                logger.exception("boucle du worker interrompue", extra={"worker": self.worker_id})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
