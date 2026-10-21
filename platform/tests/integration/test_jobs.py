import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from digital360.core.jobs import Job, JobRegistry, JobStatus, Worker, enqueue, publish
from digital360.core.tenancy import staff_transaction

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
    pytest.mark.usefixtures("empty_job_queue"),
]


def _worker(factory: async_sessionmaker[AsyncSession], registry: JobRegistry) -> Worker:
    # Backoff nul : la tâche replanifiée est immédiatement reprenable dans le test
    return Worker(factory, registry, worker_id="test-worker", backoff=lambda _: timedelta(0))


async def _job(factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID) -> Job:
    async with staff_transaction(factory) as session:
        return (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()


async def test_should_run_handler_and_mark_job_succeeded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = JobRegistry()
    received: list[dict[str, Any]] = []

    @registry.job("test.record")
    async def record(_: AsyncSession, payload: dict[str, Any]) -> None:
        received.append(payload)

    async with staff_transaction(session_factory) as session:
        job_id = await enqueue(session, "test.record", {"value": 42})
    assert job_id is not None

    assert await _worker(session_factory, registry).run_once() is True

    assert received == [{"value": 42}]
    job = await _job(session_factory, job_id)
    assert job.status == JobStatus.SUCCEEDED
    assert job.attempts == 1


async def test_should_not_enqueue_when_business_transaction_rolls_back(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(RuntimeError):
        async with staff_transaction(session_factory) as session:
            await enqueue(session, "test.never", {})
            raise RuntimeError("échec métier")

    assert await _worker(session_factory, JobRegistry()).run_once() is False


async def test_should_deduplicate_jobs_with_same_key(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with staff_transaction(session_factory) as session:
        first = await enqueue(session, "test.dedup", {}, dedup_key="invoice:1")
        second = await enqueue(session, "test.dedup", {}, dedup_key="invoice:1")

    assert first is not None
    assert second is None


async def test_should_retry_then_mark_dead_after_max_attempts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = JobRegistry()
    calls = 0

    @registry.job("test.flaky")
    async def flaky(_: AsyncSession, __: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1
        raise ConnectionError("fournisseur indisponible")

    async with staff_transaction(session_factory) as session:
        job_id = await enqueue(session, "test.flaky", {}, max_attempts=2)
    assert job_id is not None
    worker = _worker(session_factory, registry)

    await worker.run_once()
    assert (await _job(session_factory, job_id)).status == JobStatus.PENDING
    await worker.run_once()

    job = await _job(session_factory, job_id)
    assert calls == 2
    assert job.status == JobStatus.DEAD
    assert job.last_error == "ConnectionError: fournisseur indisponible"


async def test_should_roll_back_handler_writes_when_it_fails(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = JobRegistry()

    @registry.job("test.partial")
    async def partial(session: AsyncSession, _: dict[str, Any]) -> None:
        await enqueue(session, "test.side_effect", {})
        raise ValueError("après une écriture")

    async with staff_transaction(session_factory) as session:
        await enqueue(session, "test.partial", {}, max_attempts=1)

    await _worker(session_factory, registry).run_once()

    async with staff_transaction(session_factory) as session:
        names = (await session.execute(select(Job.name))).scalars().all()
    assert names == ["test.partial"]


async def test_should_mark_job_dead_when_no_handler_is_registered(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with staff_transaction(session_factory) as session:
        job_id = await enqueue(session, "test.unknown", {})
    assert job_id is not None

    await _worker(session_factory, JobRegistry()).run_once()

    job = await _job(session_factory, job_id)
    assert job.status == JobStatus.DEAD
    assert job.last_error is not None and "aucun handler" in job.last_error


async def test_should_not_run_job_scheduled_in_the_future(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with staff_transaction(session_factory) as session:
        now = await session.scalar(text("SELECT now()"))
        await enqueue(session, "test.later", {}, run_at=now + timedelta(hours=1))

    assert await _worker(session_factory, JobRegistry()).run_once() is False


async def test_should_requeue_stale_running_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with staff_transaction(session_factory) as session:
        job_id = await enqueue(session, "test.stale", {})
        await session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.RUNNING,
                locked_by="worker-mort",
                locked_at=text("now() - interval '1 hour'"),
            )
        )

    recovered = await _worker(session_factory, JobRegistry()).recover_stale()

    assert recovered == 1
    assert (await _job(session_factory, job_id)).status == JobStatus.PENDING


async def test_should_fan_out_event_to_each_subscriber_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = JobRegistry()
    handled: list[str] = []

    @registry.on("payment.confirmed", name="notifications.payment_confirmed")
    async def notify(_: AsyncSession, payload: dict[str, Any]) -> None:
        handled.append(f"notify:{payload['invoice_id']}")

    @registry.on("payment.confirmed", name="production.start_project")
    async def start(_: AsyncSession, payload: dict[str, Any]) -> None:
        handled.append(f"start:{payload['invoice_id']}")

    event_id = uuid.uuid4()
    async with staff_transaction(session_factory) as session:
        await publish(
            session, registry, "payment.confirmed", {"invoice_id": "INV-1"}, event_id=event_id
        )
        # Même événement republié (ex. webhook reçu deux fois) : aucune tâche en double
        await publish(
            session, registry, "payment.confirmed", {"invoice_id": "INV-1"}, event_id=event_id
        )

    worker = _worker(session_factory, registry)
    while await worker.run_once():
        pass

    assert sorted(handled) == ["notify:INV-1", "start:INV-1"]
