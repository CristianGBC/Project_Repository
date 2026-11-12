"""FastAPI coordinator entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
import httpx

from backend.app.api import router
from backend.app.config import CoordinatorSettings, load_settings


def create_app(
    settings: CoordinatorSettings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    """Build the coordinator, allowing controlled dependencies in tests."""

    coordinator_settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        owns_client = http_client is None
        application.state.settings = coordinator_settings
        application.state.http_client = http_client or httpx.AsyncClient(
            timeout=coordinator_settings.request_timeout_seconds
        )
        try:
            yield
        finally:
            if owns_client:
                await application.state.http_client.aclose()

    application = FastAPI(
        title="YachayRepo Coordinator",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.include_router(router)
    return application


app = create_app()
