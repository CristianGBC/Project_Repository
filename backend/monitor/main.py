"""Process entry point for the singleton storage-node health monitor."""

import asyncio
import logging
import signal

import httpx
from sqlalchemy import text

from backend.app.config import load_settings
from backend.app.database import create_database_engine, create_session_factory
from backend.app.services.health_monitor import HealthMonitor

MONITOR_ADVISORY_LOCK_ID = 924_225_031
logger = logging.getLogger(__name__)


async def run_monitor() -> None:
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be configured for the health monitor")
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    lock_connection = engine.connect()
    acquired = bool(
        lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": MONITOR_ADVISORY_LOCK_ID},
        )
    )
    if not acquired:
        lock_connection.close()
        engine.dispose()
        raise RuntimeError("another health monitor already holds the catalog lock")

    try:
        async with httpx.AsyncClient() as client:
            monitor = HealthMonitor(settings, session_factory, client)
            loop = asyncio.get_running_loop()
            for signum in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(signum, monitor.stop)
                except NotImplementedError:
                    pass
            await monitor.run()
    finally:
        lock_connection.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": MONITOR_ADVISORY_LOCK_ID},
        )
        lock_connection.close()
        engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run_monitor())


if __name__ == "__main__":
    main()
