"""Shared helpers for coordinator tests."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace

from fastapi import FastAPI
import httpx
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import SQLAlchemyError

from backend.app.auth import Principal, get_current_principal
from backend.app.config import CoordinatorSettings, load_settings
from backend.app.main import create_app
from backend.app.models import User

MockHandler = Callable[
    [httpx.Request],
    httpx.Response | Awaitable[httpx.Response],
]

TEST_USER_ID = load_settings().technical_user_id
TEST_PRINCIPAL = Principal(
    user_id=TEST_USER_ID,
    first_name="Integration",
    last_name="Operator",
    email="integration-operator@yachayrepo.invalid",
    is_active=True,
    global_role="super_admin",
    must_change_password=False,
    token_version=0,
    roles_by_school={
        school_id: frozenset({"student", "professor", "school_admin"})
        for school_id in (
            "school-bio", "school-phy", "school-math", "school-chem",
            "school-earth", "school-agro",
        )
    },
)


def asgi_request(
    application: FastAPI,
    method: str,
    path: str,
    **kwargs,
) -> httpx.Response:
    """Issue one request to an ASGI application without opening a real socket."""

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(request())


def asgi_get(application: FastAPI, path: str) -> httpx.Response:
    return asgi_request(application, "GET", path)


def coordinator_request(
    handler: MockHandler,
    path: str,
    settings: CoordinatorSettings | None = None,
    method: str = "GET",
    session_factory: sessionmaker[Session] | None = None,
    **kwargs,
) -> httpx.Response:
    """Run one coordinator request with an isolated outgoing transport."""

    async def request() -> httpx.Response:
        coordinator_settings = settings or load_settings()
        if session_factory is None:
            coordinator_settings = replace(
                coordinator_settings,
                database_url=None,
            )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as outgoing_client:
            application = create_app(
                settings=coordinator_settings,
                http_client=outgoing_client,
                session_factory=session_factory,
            )
            application.dependency_overrides[get_current_principal] = lambda: TEST_PRINCIPAL
            if session_factory is not None:
                try:
                    with session_factory.begin() as session:
                        test_user = session.get(User, TEST_USER_ID)
                        if test_user is None:
                            session.add(
                                User(
                                id=TEST_USER_ID,
                                full_name="Integration Operator",
                                first_name="Integration",
                                last_name="Operator",
                                email="integration-operator@yachayrepo.invalid",
                                password_hash="!test-dependency-override!",
                                is_admin=False,
                                global_role=None,
                                is_active=True,
                                must_change_password=False,
                                token_version=0,
                                )
                            )
                        else:
                            test_user.is_admin = False
                            test_user.global_role = None
                except SQLAlchemyError:
                    pass
            async with application.router.lifespan_context(application):
                transport = httpx.ASGITransport(app=application)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    return await client.request(method, path, **kwargs)

    return asyncio.run(request())
