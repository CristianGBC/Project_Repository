"""Health validation for storage nodes."""

from typing import Any

import httpx

from backend.app.config import NodeSettings
from backend.app.schemas import NodeStatus


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

    status = "unavailable"
    try:
        response = await client.get(
            f"{node.url}/health",
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if _is_valid_health_payload(payload, node.node_id):
            status = "healthy"
    except (httpx.HTTPError, ValueError):
        pass

    return NodeStatus(node_id=node.node_id, school=node.school, status=status)
