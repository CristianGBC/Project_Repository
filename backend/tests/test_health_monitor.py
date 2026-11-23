"""Periodic health-monitor tests with an explicitly isolated PostgreSQL catalog."""

import asyncio
from dataclasses import replace
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
import httpx
import pytest
from sqlalchemy import create_engine, select, update

from backend.app.config import load_settings
from backend.app.database import create_session_factory
from backend.app.models import DocumentReplica, DocumentVersion, StorageNode
from backend.app.seed import seed_initial_catalog
from backend.app.services.health_monitor import HealthMonitor
from backend.app.services.replication_diagnostics import find_replication_deficits
from backend.tests.test_document_catalog_integration import (
    _request,
    replicated_storage_handler,
)


@pytest.fixture(scope="module")
def monitor_session_factory():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    database_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not database_name.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must target a database ending in _test")
    backend_dir = Path(__file__).resolve().parents[1]
    os.environ["DATABASE_URL"] = database_url
    command.upgrade(Config(str(backend_dir / "alembic.ini")), "head")
    seed_initial_catalog()
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def reset_node_health(monitor_session_factory):
    with monitor_session_factory.begin() as session:
        session.execute(
            update(StorageNode).values(health="up", last_seen_at=None)
        )
    yield
    with monitor_session_factory.begin() as session:
        session.execute(update(StorageNode).values(health="up"))


def monitor_settings(**overrides):
    values = {
        "health_check_interval_seconds": 0.01,
        "health_check_timeout_seconds": 0.1,
        "health_failure_threshold": 3,
        "health_recovery_threshold": 2,
    }
    values.update(overrides)
    return replace(load_settings(), **values)


def health_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "ok",
            "service": "storage",
            "node_id": request.url.host,
            "school": "Test school",
        },
    )


def run_cycle(handler, settings, session_factory):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            monitor = HealthMonitor(settings, session_factory, client)
            observations = await monitor.check_cycle()
            return monitor, observations

    return asyncio.run(run())


def node_state(session_factory, node_id: str) -> StorageNode:
    with session_factory() as session:
        node = session.get(StorageNode, node_id)
        session.expunge(node)
        return node


def test_all_nodes_remain_up_and_last_seen_is_updated(
    monitor_session_factory,
) -> None:
    monitor, observations = run_cycle(
        health_response,
        monitor_settings(),
        monitor_session_factory,
    )

    assert len(observations) == 6
    assert all(observation.healthy for observation in observations)
    assert all(
        counter.consecutive_successes == 1
        for counter in monitor.counters.values()
    )
    assert node_state(monitor_session_factory, "node-math").health == "up"
    assert node_state(monitor_session_factory, "node-math").last_seen_at is not None


def test_failure_and_recovery_thresholds_are_independent_per_node(
    monitor_session_factory,
) -> None:
    failing = True

    def handler(request: httpx.Request) -> httpx.Response:
        if failing and request.url.host == "node-math":
            raise httpx.ConnectError("offline", request=request)
        return health_response(request)

    async def scenario():
        nonlocal failing
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            monitor = HealthMonitor(
                monitor_settings(), monitor_session_factory, client
            )
            await monitor.check_cycle()
            assert node_state(monitor_session_factory, "node-math").health == "up"
            await monitor.check_cycle()
            assert node_state(monitor_session_factory, "node-math").health == "up"
            await monitor.check_cycle()
            assert node_state(monitor_session_factory, "node-math").health == "down"
            assert node_state(monitor_session_factory, "node-bio").health == "up"

            failing = False
            await monitor.check_cycle()
            assert node_state(monitor_session_factory, "node-math").health == "down"
            await monitor.check_cycle()
            assert node_state(monitor_session_factory, "node-math").health == "up"

    asyncio.run(scenario())


def test_checks_are_concurrent_and_timeout_does_not_block_other_nodes(
    monitor_session_factory,
) -> None:
    active = 0
    maximum_active = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        if request.url.host == "node-math":
            raise httpx.ReadTimeout("slow", request=request)
        return health_response(request)

    monitor, observations = run_cycle(
        handler,
        monitor_settings(health_failure_threshold=1),
        monitor_session_factory,
    )

    assert maximum_active == 6
    assert (
        next(item for item in observations if item.node_id == "node-math").result
        == "timeout"
    )
    assert node_state(monitor_session_factory, "node-math").health == "down"
    assert node_state(monitor_session_factory, "node-bio").health == "up"
    assert monitor.counters["node-math"].consecutive_failures == 1


def test_monitor_stops_cleanly_and_restart_preserves_persisted_state(
    monitor_session_factory,
) -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        if request.url.host == "node-math":
            raise httpx.ConnectError("offline", request=request)
        return health_response(request)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(unavailable)
        ) as client:
            first = HealthMonitor(
                monitor_settings(health_failure_threshold=1),
                monitor_session_factory,
                client,
            )
            task = asyncio.create_task(first.run())
            await asyncio.sleep(0.02)
            first.stop()
            await asyncio.wait_for(task, timeout=1)
            assert node_state(monitor_session_factory, "node-math").health == "down"

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(health_response)
        ) as client:
            restarted = HealthMonitor(
                monitor_settings(), monitor_session_factory, client
            )
            await restarted.check_cycle()
            assert restarted.counters["node-math"].consecutive_successes == 1
            assert node_state(monitor_session_factory, "node-math").health == "down"
            await restarted.check_cycle()
            assert node_state(monitor_session_factory, "node-math").health == "up"

    asyncio.run(scenario())


def test_monitor_does_not_change_replicas_and_diagnoses_operational_deficit(
    monitor_session_factory,
) -> None:
    storage = replicated_storage_handler()
    uploaded = _request(
        storage,
        monitor_session_factory,
        "/documents",
        method="POST",
        replication_factor=3,
        data={"school_id": "school-math"},
        files={"file": ("monitor-rf3.txt", b"monitor rf3", "text/plain")},
    )
    assert uploaded.status_code == 201
    version_id = uploaded.json()["version_id"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "node-math":
            raise httpx.ConnectError("offline", request=request)
        return health_response(request)

    run_cycle(
        handler,
        monitor_settings(health_failure_threshold=1),
        monitor_session_factory,
    )

    with monitor_session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        replicas = session.scalars(
            select(DocumentReplica).where(DocumentReplica.version_id == version.id)
        ).all()
        assert version.status == "available"
        assert {replica.status for replica in replicas} == {"healthy"}

    deficit = next(
        item
        for item in find_replication_deficits(monitor_session_factory)
        if str(item.version_id) == version_id
    )
    assert deficit.target_replicas == 3
    assert deficit.registered_replicas == 3
    assert deficit.healthy_replicas == 3
    assert deficit.accessible_replicas == 2
    assert deficit.operational_deficit == 1

    visits = []

    async def observed_storage(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.startswith("/files/"):
            visits.append(request.url.host)
        return await storage(request)

    downloaded = _request(
        observed_storage,
        monitor_session_factory,
        f"/documents/school-math/{uploaded.json()['document_id']}/download",
        replication_factor=3,
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"monitor rf3"
    assert visits == ["node-agro"]


def test_recovery_never_changes_missing_or_corrupted_replicas(
    monitor_session_factory,
) -> None:
    storage = replicated_storage_handler()
    uploaded = _request(
        storage,
        monitor_session_factory,
        "/documents",
        method="POST",
        replication_factor=3,
        data={"school_id": "school-math"},
        files={"file": ("monitor-integrity.txt", b"integrity", "text/plain")},
    )
    version_id = uploaded.json()["version_id"]
    with monitor_session_factory.begin() as session:
        replicas = session.scalars(
            select(DocumentReplica).where(DocumentReplica.version_id == version_id)
        ).all()
        statuses = {"node-math": "missing", "node-agro": "corrupted"}
        for replica in replicas:
            if replica.node_id in statuses:
                replica.status = statuses[replica.node_id]
        session.execute(update(StorageNode).values(health="down"))

    run_cycle(
        health_response,
        monitor_settings(health_recovery_threshold=1),
        monitor_session_factory,
    )

    with monitor_session_factory() as session:
        replicas = session.scalars(
            select(DocumentReplica).where(DocumentReplica.version_id == version_id)
        ).all()
        assert {replica.node_id: replica.status for replica in replicas} == {
            "node-math": "missing",
            "node-agro": "corrupted",
            "node-bio": "healthy",
        }
