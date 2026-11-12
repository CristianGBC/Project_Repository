"""Health endpoint tests."""

import httpx

from backend.storage.application import create_app as create_storage_app
from backend.tests.conftest import asgi_get, coordinator_request


def test_coordinator_health() -> None:
    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Coordinator health contacted {request.url}")

    response = coordinator_request(unexpected_request, "/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "coordinator"}


def test_storage_health_reports_configured_identity() -> None:
    application = create_storage_app(
        node_id="node-math",
        school_name="Escuela de Ciencias Matemáticas y Computacionales",
    )

    response = asgi_get(application, "/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "storage",
        "node_id": "node-math",
        "school": "Escuela de Ciencias Matemáticas y Computacionales",
    }


def test_storage_health_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("NODE_ID", "node-bio")
    monkeypatch.setenv(
        "SCHOOL_NAME", "Escuela de Ciencias Biológicas e Ingeniería"
    )
    application = create_storage_app()

    payload = asgi_get(application, "/health").json()

    assert payload["node_id"] == "node-bio"
    assert payload["school"] == "Escuela de Ciencias Biológicas e Ingeniería"


def test_coordinator_health_is_independent_when_all_nodes_fail() -> None:
    def all_nodes_fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    response = coordinator_request(all_nodes_fail, "/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "coordinator"}
