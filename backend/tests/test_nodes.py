"""Coordinator-to-storage communication tests."""

import asyncio

import httpx
import pytest

from backend.app.config import load_settings
from backend.tests.conftest import coordinator_request


def healthy_response(request: httpx.Request) -> httpx.Response:
    node_id = request.url.host
    return httpx.Response(
        200,
        json={
            "status": "ok",
            "service": "storage",
            "node_id": node_id,
            "school": "Reported by storage",
        },
    )


def statuses_by_id(payload: dict) -> dict[str, str]:
    return {node["node_id"]: node["status"] for node in payload["nodes"]}


def test_nodes_returns_six_healthy_nodes_in_configuration_order() -> None:
    settings = load_settings()

    response = coordinator_request(healthy_response, "/nodes", settings)

    assert response.status_code == 200
    payload = response.json()
    assert [node["node_id"] for node in payload["nodes"]] == [
        node.node_id for node in settings.nodes
    ]
    assert all(node["status"] == "healthy" for node in payload["nodes"])


def test_one_unavailable_node_does_not_hide_healthy_nodes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "node-math":
            raise httpx.ConnectError("offline", request=request)
        return healthy_response(request)

    response = coordinator_request(handler, "/nodes")

    statuses = statuses_by_id(response.json())
    assert response.status_code == 200
    assert statuses["node-math"] == "unavailable"
    assert all(
        status == "healthy"
        for node_id, status in statuses.items()
        if node_id != "node-math"
    )


def test_multiple_unavailable_nodes_are_reported() -> None:
    unavailable = {"node-phy", "node-earth", "node-agro"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host in unavailable:
            raise httpx.ConnectError("offline", request=request)
        return healthy_response(request)

    statuses = statuses_by_id(coordinator_request(handler, "/nodes").json())

    for node_id, status in statuses.items():
        expected = "unavailable" if node_id in unavailable else "healthy"
        assert status == expected


def test_timeout_marks_only_that_node_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "node-chem":
            raise httpx.ReadTimeout("too slow", request=request)
        return healthy_response(request)

    statuses = statuses_by_id(coordinator_request(handler, "/nodes").json())

    assert statuses["node-chem"] == "unavailable"
    assert statuses["node-bio"] == "healthy"


@pytest.mark.parametrize(
    "bad_response",
    [
        httpx.Response(500, json={"detail": "error"}),
        httpx.Response(200, content=b"not-json"),
        httpx.Response(
            200,
            json={
                "status": "ok",
                "service": "storage",
                "node_id": "another-node",
                "school": "Wrong identity",
            },
        ),
        httpx.Response(200, json={"status": "ok"}),
        httpx.Response(
            200,
            json={
                "status": "ok",
                "service": "coordinator",
                "node_id": "node-bio",
                "school": "Wrong service",
            },
        ),
    ],
    ids=["http-500", "invalid-json", "wrong-id", "missing-fields", "wrong-service"],
)
def test_incorrect_response_is_not_healthy(bad_response: httpx.Response) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "node-bio":
            return bad_response
        return healthy_response(request)

    statuses = statuses_by_id(coordinator_request(handler, "/nodes").json())

    assert statuses["node-bio"] == "unavailable"
    assert statuses["node-phy"] == "healthy"


def test_node_checks_run_concurrently() -> None:
    active_requests = 0
    maximum_active_requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_requests, maximum_active_requests
        active_requests += 1
        maximum_active_requests = max(maximum_active_requests, active_requests)
        await asyncio.sleep(0.01)
        active_requests -= 1
        return healthy_response(request)

    response = coordinator_request(handler, "/nodes")

    assert response.status_code == 200
    assert maximum_active_requests > 1


def test_nodes_uses_configured_remote_ip_and_ports(monkeypatch) -> None:
    remote_urls = {
        "node-bio": "http://192.168.1.23:8001",
        "node-phy": "http://192.168.1.23:8002",
        "node-math": "http://192.168.1.23:8003",
        "node-chem": "http://192.168.1.23:8004",
        "node-earth": "http://192.168.1.23:8005",
        "node-agro": "http://192.168.1.23:8006",
    }
    for node_id, url in remote_urls.items():
        variable = f"NODE_{node_id.removeprefix('node-').upper()}_URL"
        monkeypatch.setenv(variable, url)

    node_by_port = {
        int(url.rsplit(":", maxsplit=1)[1]): node_id
        for node_id, url in remote_urls.items()
    }
    requested_urls: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.add(str(request.url))
        node_id = node_by_port[request.url.port]
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "service": "storage",
                "node_id": node_id,
                "school": "Remote storage node",
            },
        )

    response = coordinator_request(handler, "/nodes", load_settings())

    assert response.status_code == 200
    assert all(node["status"] == "healthy" for node in response.json()["nodes"])
    assert requested_urls == {f"{url}/health" for url in remote_urls.values()}


def test_unavailable_remote_node_does_not_affect_coordinator(monkeypatch) -> None:
    monkeypatch.setenv("NODE_MATH_URL", "http://192.168.1.23:8003")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == 8003:
            raise httpx.ConnectError("remote node offline", request=request)
        return healthy_response(request)

    settings = load_settings()
    response = coordinator_request(handler, "/nodes", settings)
    statuses = statuses_by_id(response.json())

    assert response.status_code == 200
    assert statuses["node-math"] == "unavailable"
