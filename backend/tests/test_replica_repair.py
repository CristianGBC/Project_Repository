"""Replica-repair integration tests against an isolated PostgreSQL catalog."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
import httpx
import pytest
from sqlalchemy import create_engine, select, update

from backend.app.config import load_settings
from backend.app.database import create_session_factory
from backend.app.models import Document, DocumentReplica, DocumentVersion, StorageNode
from backend.app.seed import seed_initial_catalog
from backend.app.services.replica_repair import ReplicaRepair
from backend.tests.test_coordinator_documents import parse_multipart_request
from backend.tests.test_document_catalog_integration import (
    _create_rf3_document,
    _request,
    replicated_storage_handler,
)


@pytest.fixture(scope="module")
def repair_catalog():
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
        yield create_session_factory(engine), engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def reset_health(repair_catalog):
    session_factory, _ = repair_catalog
    with session_factory.begin() as session:
        session.execute(
            update(StorageNode).values(
                health="up",
                health_changed_at=datetime.now(timezone.utc),
            )
        )


def repair_settings(**overrides):
    values = {
        "repair_down_grace_seconds": 0,
        "repair_retry_backoff_seconds": 0,
        "repair_interval_seconds": 0.01,
        "repair_internal_token": "test-token",
    }
    values.update(overrides)
    return replace(load_settings(), **values)


def repair_storage_handler():
    base = replicated_storage_handler()
    read_faults: dict[str, str] = {}
    write_faults: set[str] = set()
    delete_faults: set[str] = set()
    internal_puts: list[str] = []
    internal_deletes: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if request.url.path.startswith("/internal/repair/"):
            storage_id = request.url.path.rsplit("/", 1)[-1]
            key = (host, storage_id)
            if request.headers.get("X-Repair-Token") != "test-token":
                return httpx.Response(403, json={"detail": "forbidden"})
            if request.method == "PUT":
                internal_puts.append(host)
                if handler.write_delay:
                    await asyncio.sleep(handler.write_delay)
                if host in write_faults:
                    return httpx.Response(500, json={"detail": "write failed"})
                fields, content = await parse_multipart_request(request)
                metadata = {
                    "document_id": storage_id,
                    "filename": fields["filename"],
                    "school_id": fields["school_id"],
                    "school": fields["school"],
                    "size_bytes": int(fields["size_bytes"]),
                    "sha256": fields["sha256"],
                    "created_at": fields["created_at"],
                    "node_id": fields["school_id"],
                }
                base.records[key] = (metadata, content)
                return httpx.Response(200, json={**metadata, "status": "stored"})
            if request.method == "DELETE":
                internal_deletes.append(host)
                if host in delete_faults:
                    return httpx.Response(500, json={"detail": "delete failed"})
                record = base.records.get(key)
                if record is None:
                    return httpx.Response(204)
                expected = request.url.params.get("expected_sha256")
                if record[0]["sha256"] != expected:
                    return httpx.Response(409, json={"detail": "checksum mismatch"})
                del base.records[key]
                return httpx.Response(204)

        if request.method == "GET" and request.url.path.startswith("/files/"):
            fault = read_faults.get(host)
            if fault == "unreachable":
                raise httpx.ConnectError("offline", request=request)
            if fault == "corrupted":
                storage_id = request.url.path.strip("/").split("/")[1]
                record = base.records.get((host, storage_id))
                if record is not None:
                    content = record[1]
                    return httpx.Response(200, content=b"x" + content[1:])
        return await base(request)

    handler.records = base.records
    handler.read_faults = read_faults
    handler.write_faults = write_faults
    handler.delete_faults = delete_faults
    handler.internal_puts = internal_puts
    handler.internal_deletes = internal_deletes
    handler.write_delay = 0
    return handler


def run_repair(handler, settings, session_factory, engine, version_id: str):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            repair = ReplicaRepair(settings, session_factory, client, engine)
            return await repair._repair_version(UUID(version_id))

    return asyncio.run(run())


def replicas_for(session_factory, version_id: str):
    with session_factory() as session:
        return session.scalars(
            select(DocumentReplica)
            .where(DocumentReplica.version_id == UUID(version_id))
            .order_by(DocumentReplica.node_id)
        ).all()


def test_complete_rf3_version_needs_no_repair(repair_catalog) -> None:
    session_factory, engine = repair_catalog
    handler = repair_storage_handler()
    uploaded, _ = _create_rf3_document(handler, session_factory)

    results = run_repair(
        handler, repair_settings(), session_factory, engine, uploaded["version_id"]
    )

    assert results == []
    assert len(replicas_for(session_factory, uploaded["version_id"])) == 3


def test_down_replica_obeys_grace_then_uses_another_physical_host(
    repair_catalog,
) -> None:
    session_factory, engine = repair_catalog
    handler = repair_storage_handler()
    uploaded, content = _create_rf3_document(handler, session_factory)
    with session_factory.begin() as session:
        node = session.get(StorageNode, "node-math")
        node.health = "down"
        node.health_changed_at = datetime.now(timezone.utc)

    before = run_repair(
        handler,
        repair_settings(repair_down_grace_seconds=60),
        session_factory,
        engine,
        uploaded["version_id"],
    )
    assert before == []
    assert len(replicas_for(session_factory, uploaded["version_id"])) == 3

    with session_factory.begin() as session:
        node = session.get(StorageNode, "node-math")
        node.health_changed_at = datetime.now(timezone.utc) - timedelta(seconds=61)
    after = run_repair(
        handler,
        repair_settings(repair_down_grace_seconds=60),
        session_factory,
        engine,
        uploaded["version_id"],
    )
    replicas = replicas_for(session_factory, uploaded["version_id"])

    assert any(result.success and result.node_id == "node-chem" for result in after)
    assert {replica.physical_host_id for replica in replicas} == {
        "raspberry-1",
        "raspberry-2",
        "raspberry-3",
        "raspberry-4",
    }
    assert handler.records[("node-chem", uploaded["document_id"])][1] == content


def test_recovered_origin_is_retained_and_excess_is_safely_removed(
    repair_catalog,
) -> None:
    session_factory, engine = repair_catalog
    handler = repair_storage_handler()
    uploaded, _ = _create_rf3_document(handler, session_factory)
    with session_factory.begin() as session:
        node = session.get(StorageNode, "node-math")
        node.health = "down"
        node.health_changed_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    run_repair(
        handler, repair_settings(), session_factory, engine, uploaded["version_id"]
    )
    with session_factory.begin() as session:
        node = session.get(StorageNode, "node-math")
        node.health = "up"
        node.health_changed_at = datetime.now(timezone.utc)

    results = run_repair(
        handler, repair_settings(), session_factory, engine, uploaded["version_id"]
    )
    replicas = replicas_for(session_factory, uploaded["version_id"])

    assert any(result.action == "cleanup" and result.success for result in results)
    assert {replica.node_id for replica in replicas} == {
        "node-math",
        "node-agro",
        "node-bio",
    }
    assert "node-math" not in handler.internal_deletes


@pytest.mark.parametrize("broken_status", ["missing", "corrupted"])
def test_missing_or_corrupted_origin_is_restored_in_place(
    repair_catalog, broken_status: str
) -> None:
    session_factory, engine = repair_catalog
    handler = repair_storage_handler()
    uploaded, content = _create_rf3_document(handler, session_factory)
    with session_factory.begin() as session:
        replica = session.scalar(
            select(DocumentReplica).where(
                DocumentReplica.version_id == UUID(uploaded["version_id"]),
                DocumentReplica.node_id == "node-math",
            )
        )
        replica.status = broken_status
    if broken_status == "missing":
        del handler.records[("node-math", uploaded["document_id"])]
    else:
        metadata, _ = handler.records[("node-math", uploaded["document_id"])]
        handler.records[("node-math", uploaded["document_id"])] = (
            metadata,
            b"corrupt",
        )

    results = run_repair(
        handler, repair_settings(), session_factory, engine, uploaded["version_id"]
    )
    math = next(
        replica
        for replica in replicas_for(session_factory, uploaded["version_id"])
        if replica.node_id == "node-math"
    )

    assert any(result.success and result.node_id == "node-math" for result in results)
    assert math.status == "healthy"
    assert handler.records[("node-math", uploaded["document_id"])][1] == content


def test_corrupt_source_is_rejected_and_next_source_repairs_version(
    repair_catalog,
) -> None:
    session_factory, engine = repair_catalog
    handler = repair_storage_handler()
    uploaded, content = _create_rf3_document(handler, session_factory)
    with session_factory.begin() as session:
        math = session.scalar(
            select(DocumentReplica).where(
                DocumentReplica.version_id == UUID(uploaded["version_id"]),
                DocumentReplica.node_id == "node-math",
            )
        )
        math.status = "missing"
    del handler.records[("node-math", uploaded["document_id"])]
    handler.read_faults["node-agro"] = "corrupted"

    results = run_repair(
        handler, repair_settings(), session_factory, engine, uploaded["version_id"]
    )

    assert results[0].success is True
    assert handler.records[("node-math", uploaded["document_id"])][1] == content
    assert all(
        replica.status == "healthy"
        for replica in replicas_for(session_factory, uploaded["version_id"])
    )


def test_failed_physical_cleanup_preserves_catalog_evidence(repair_catalog) -> None:
    session_factory, engine = repair_catalog
    handler = repair_storage_handler()
    uploaded, _ = _create_rf3_document(handler, session_factory)
    with session_factory.begin() as session:
        node = session.get(StorageNode, "node-math")
        node.health = "down"
        node.health_changed_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    run_repair(
        handler, repair_settings(), session_factory, engine, uploaded["version_id"]
    )
    with session_factory.begin() as session:
        session.get(StorageNode, "node-math").health = "up"
    handler.delete_faults.add("node-chem")

    results = run_repair(
        handler, repair_settings(), session_factory, engine, uploaded["version_id"]
    )

    assert results[0].action == "cleanup" and results[0].success is False
    assert len(replicas_for(session_factory, uploaded["version_id"])) == 4


def test_rf1_without_verified_source_does_not_fabricate_content(
    repair_catalog,
) -> None:
    session_factory, engine = repair_catalog
    handler = repair_storage_handler()
    response = _request(
        handler,
        session_factory,
        "/documents",
        method="POST",
        replication_factor=1,
        data={"school_id": "school-math"},
        files={"file": ("rf1.txt", b"single copy", "text/plain")},
    )
    payload = response.json()
    with session_factory.begin() as session:
        replica = session.scalar(
            select(DocumentReplica).where(
                DocumentReplica.version_id == UUID(payload["version_id"])
            )
        )
        replica.status = "missing"
    del handler.records[("node-math", payload["document_id"])]

    results = run_repair(
        handler, repair_settings(), session_factory, engine, payload["version_id"]
    )
    with session_factory() as session:
        version = session.get(DocumentVersion, UUID(payload["version_id"]))

    assert results[0].success is False
    assert version.target_replicas == 1
    assert handler.internal_puts == []


def test_interrupted_pending_repair_is_resumed_idempotently(repair_catalog) -> None:
    session_factory, engine = repair_catalog
    handler = repair_storage_handler()
    uploaded, _ = _create_rf3_document(handler, session_factory)
    with session_factory.begin() as session:
        replica = session.scalar(
            select(DocumentReplica).where(
                DocumentReplica.version_id == UUID(uploaded["version_id"]),
                DocumentReplica.node_id == "node-math",
            )
        )
        replica.status = "missing"
    del handler.records[("node-math", uploaded["document_id"])]
    handler.write_faults.add("node-math")

    failed = run_repair(
        handler, repair_settings(), session_factory, engine, uploaded["version_id"]
    )
    assert failed[0].success is False
    handler.write_faults.clear()
    resumed = run_repair(
        handler, repair_settings(), session_factory, engine, uploaded["version_id"]
    )

    assert resumed[0].success is True
    replicas = replicas_for(session_factory, uploaded["version_id"])
    assert len(replicas) == 3
    assert all(replica.status == "healthy" for replica in replicas)


def test_two_repairers_cannot_duplicate_same_version_job(repair_catalog) -> None:
    session_factory, engine = repair_catalog
    handler = repair_storage_handler()
    handler.write_delay = 0.03
    uploaded, _ = _create_rf3_document(handler, session_factory)
    with session_factory.begin() as session:
        node = session.get(StorageNode, "node-math")
        node.health = "down"
        node.health_changed_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    async def scenario():
        transport = httpx.MockTransport(handler)
        async with (
            httpx.AsyncClient(transport=transport) as first_client,
            httpx.AsyncClient(transport=transport) as second_client,
        ):
            first = ReplicaRepair(
                repair_settings(), session_factory, first_client, engine
            )
            second = ReplicaRepair(
                repair_settings(), session_factory, second_client, engine
            )
            return await asyncio.gather(
                first._repair_version(UUID(uploaded["version_id"])),
                second._repair_version(UUID(uploaded["version_id"])),
            )

    batches = asyncio.run(scenario())

    assert sum(result.success for batch in batches for result in batch) == 1
    assert len(handler.internal_puts) == 1
    assert len(replicas_for(session_factory, uploaded["version_id"])) == 4


def test_historical_version_of_soft_deleted_document_repairs_independently(
    repair_catalog,
) -> None:
    session_factory, engine = repair_catalog
    handler = repair_storage_handler()
    first, first_content = _create_rf3_document(handler, session_factory)
    second = _request(
        handler,
        session_factory,
        f"/documents/{first['document_id']}/versions",
        method="POST",
        replication_factor=3,
        files={"file": ("second.txt", b"second version", "text/plain")},
    )
    assert second.status_code == 201
    deleted = _request(
        handler,
        session_factory,
        f"/documents/{first['document_id']}",
        method="DELETE",
        replication_factor=3,
    )
    assert deleted.status_code == 200
    with session_factory.begin() as session:
        historical = session.scalar(
            select(DocumentReplica).where(
                DocumentReplica.version_id == UUID(first["version_id"]),
                DocumentReplica.node_id == "node-math",
            )
        )
        historical.status = "missing"
    del handler.records[("node-math", first["document_id"])]

    results = run_repair(
        handler, repair_settings(), session_factory, engine, first["version_id"]
    )
    with session_factory() as session:
        document = session.get(Document, UUID(first["document_id"]))

    assert results[0].success is True
    assert document.deleted_at is not None
    assert str(document.current_version_id) == second.json()["version_id"]
    assert handler.records[("node-math", first["document_id"])][1] == first_content
