"""Process entry point for the singleton upload reconciler."""

import asyncio
import logging
import signal

import httpx
from sqlalchemy import text

from backend.app.config import load_settings
from backend.app.database import create_database_engine, create_session_factory
from backend.app.services.upload_reconciliation import UploadReconciler

RECONCILIATION_ADVISORY_LOCK_ID = 924_225_033


async def run_reconciliation() -> None:
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be configured for upload reconciliation")
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    lock_connection = engine.connect()
    acquired = bool(
        lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": RECONCILIATION_ADVISORY_LOCK_ID},
        )
    )
    lock_connection.commit()
    if not acquired:
        lock_connection.close()
        engine.dispose()
        raise RuntimeError("another upload reconciler holds the catalog lock")
    try:
        timeout = httpx.Timeout(settings.reconciliation_transfer_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            reconciler = UploadReconciler(settings, session_factory, client, engine)
            loop = asyncio.get_running_loop()
            for signum in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(signum, reconciler.stop)
                except NotImplementedError:
                    pass
            await reconciler.run()
    finally:
        lock_connection.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": RECONCILIATION_ADVISORY_LOCK_ID},
        )
        lock_connection.commit()
        lock_connection.close()
        engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run_reconciliation())


if __name__ == "__main__":
    main()
