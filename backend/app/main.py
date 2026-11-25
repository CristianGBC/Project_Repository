"""FastAPI coordinator entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.api import (
    authentication_router,
    catalogs_router,
    documents_router,
    operations_router,
    router,
    subjects_router,
    users_router,
)
from backend.app.config import CoordinatorSettings, load_settings
from backend.app.database import create_database_engine, create_session_factory
from backend.app.services.auth_service import LoginThrottle


def create_app(
    settings: CoordinatorSettings | None = None,
    http_client: httpx.AsyncClient | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    """Build the coordinator, allowing controlled dependencies in tests."""

    coordinator_settings = settings or load_settings()
    database_engine: Engine | None = None
    configured_session_factory = session_factory
    if configured_session_factory is None and coordinator_settings.database_url:
        database_engine = create_database_engine(coordinator_settings.database_url)
        configured_session_factory = create_session_factory(database_engine)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        owns_client = http_client is None
        application.state.settings = coordinator_settings
        application.state.http_client = http_client or httpx.AsyncClient(
            timeout=coordinator_settings.transfer_timeout_seconds
        )
        application.state.session_factory = configured_session_factory
        application.state.login_throttle = LoginThrottle(
            coordinator_settings.auth_login_max_attempts,
            coordinator_settings.auth_login_window_seconds,
        )
        try:
            yield
        finally:
            if owns_client:
                await application.state.http_client.aclose()
            if database_engine is not None:
                database_engine.dispose()

    application = FastAPI(
        title="YachayRepo Coordinator",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(coordinator_settings.cors_allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Content-Disposition"],
    )
    application.include_router(router)
    application.include_router(authentication_router)
    application.include_router(catalogs_router)
    application.include_router(documents_router)
    application.include_router(operations_router)
    application.include_router(subjects_router)
    application.include_router(users_router)
    return application


app = create_app()
