"""Process entry point for the singleton replica-repair service."""

import asyncio
import logging
import signal

import httpx
from sqlalchemy import text

from backend.app.config import load_settings
from backend.app.database import create_database_engine, create_session_factory
from backend.app.services.replica_repair import ReplicaRepair

REPAIR_ADVISORY_LOCK_ID = 924_225_032
logger = logging.getLogger(__name__)


async def run_repair() -> None:
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be configured for replica repair")
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    lock_connection = engine.connect()
    acquired = bool(
        lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": REPAIR_ADVISORY_LOCK_ID},
        )
    )
    lock_connection.commit()
    if not acquired:
        lock_connection.close()
        engine.dispose()
        raise RuntimeError("another replica repair process holds the catalog lock")

    try:
        timeout = httpx.Timeout(settings.repair_transfer_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            repair = ReplicaRepair(settings, session_factory, client, engine)
            loop = asyncio.get_running_loop()
            for signum in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(signum, repair.stop)
                except NotImplementedError:
                    pass
            await repair.run()
    finally:
        lock_connection.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": REPAIR_ADVISORY_LOCK_ID},
        )
        lock_connection.commit()
        lock_connection.close()
        engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run_repair())


if __name__ == "__main__":
    main()
