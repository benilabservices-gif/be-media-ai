"""Point d'entrée du worker : `python -m digital360.worker`."""

import asyncio
import contextlib
import logging
import signal
import socket
import uuid

from digital360.core.config import get_settings
from digital360.core.db import create_engine, create_session_factory
from digital360.core.email import build_email_sender
from digital360.core.jobs import Worker
from digital360.core.logging import configure_logging
from digital360.jobs import build_registry
from digital360.modules.billing.application.renewals import schedule_daily_sweep

logger = logging.getLogger("digital360.worker")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(str(settings.database_url))
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    registry = build_registry(settings, build_email_sender(settings))
    worker = Worker(create_session_factory(engine), registry, worker_id=worker_id)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        # Windows ne supporte pas add_signal_handler : Ctrl+C lève KeyboardInterrupt à la place
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop.set)

    await schedule_daily_sweep(worker.session_factory)
    logger.info("worker démarré", extra={"worker_id": worker_id})
    try:
        await worker.run_forever(stop)
    finally:
        await engine.dispose()
        logger.info("worker arrêté", extra={"worker_id": worker_id})


if __name__ == "__main__":
    asyncio.run(main())
