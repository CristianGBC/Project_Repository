"""Coordinator API routes."""

import asyncio

from fastapi import APIRouter, Request
import httpx

from backend.app.config import CoordinatorSettings
from backend.app.schemas import CoordinatorHealth, NodesResponse
from backend.app.services import check_node

router = APIRouter()


@router.get("/health", response_model=CoordinatorHealth)
async def coordinator_health() -> CoordinatorHealth:
    """Report only the health of the coordinator process."""

    return CoordinatorHealth()


@router.get("/nodes", response_model=NodesResponse)
async def nodes_health(request: Request) -> NodesResponse:
    """Check all configured storage nodes concurrently and preserve their order."""

    client: httpx.AsyncClient = request.app.state.http_client
    settings: CoordinatorSettings = request.app.state.settings
    statuses = await asyncio.gather(
        *(
            check_node(client, node, settings.request_timeout_seconds)
            for node in settings.nodes
        )
    )
    return NodesResponse(nodes=list(statuses))
