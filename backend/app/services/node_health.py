"""Health validation for storage nodes."""

from dataclasses import dataclass
import time
from typing import Any

import httpx

from backend.app.config import NodeSettings
from backend.app.schemas import NodeStatus


@dataclass(frozen=True)
class NodeHealthObservation:
    node_id: str
    healthy: bool
    result: str
    duration_ms: float


def _is_valid_health_payload(payload: Any, expected_node_id: str) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and payload.get("service") == "storage"
        and payload.get("node_id") == expected_node_id
        and isinstance(payload.get("school"), str)
    )


async def check_node(
    client: httpx.AsyncClient,
    node: NodeSettings,
    timeout_seconds: float | None = None,
) -> NodeStatus:
    """Return a stable status even when a node fails or answers incorrectly."""

    observation = await probe_node(client, node, timeout_seconds)
    status = "healthy" if observation.healthy else "unavailable"
    return NodeStatus(node_id=node.node_id, school=node.school, status=status)


async def probe_node(
    client: httpx.AsyncClient,
    node: NodeSettings,
    timeout_seconds: float | None = None,
) -> NodeHealthObservation:
    """Classify one health response while preserving a stable public contract."""

    started = time.monotonic()
    healthy = False
    result = "invalid_response"
    try:
        response = await client.get(
            f"{node.url}/health",
            timeout=timeout_seconds,
        )
        if response.status_code >= 500:
            result = "server_error"
        elif not response.is_success:
            result = "http_error"
        else:
            try:
                payload = response.json()
            except ValueError:
                result = "invalid_response"
            else:
                healthy = _is_valid_health_payload(payload, node.node_id)
                result = "healthy" if healthy else "invalid_response"
    except httpx.TimeoutException:
        result = "timeout"
    except httpx.RequestError:
        result = "unreachable"

    return NodeHealthObservation(
        node_id=node.node_id,
        healthy=healthy,
        result=result,
        duration_ms=(time.monotonic() - started) * 1000,
    )
