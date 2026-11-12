"""Shared helpers for coordinator tests."""

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import FastAPI
import httpx

from backend.app.config import CoordinatorSettings, load_settings
from backend.app.main import create_app

MockHandler = Callable[
    [httpx.Request],
    httpx.Response | Awaitable[httpx.Response],
]


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
    **kwargs,
) -> httpx.Response:
    """Run one coordinator request with an isolated outgoing transport."""

    async def request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as outgoing_client:
            application = create_app(
                settings=settings or load_settings(),
                http_client=outgoing_client,
            )
            async with application.router.lifespan_context(application):
                transport = httpx.ASGITransport(app=application)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    return await client.request(method, path, **kwargs)

    return asyncio.run(request())
