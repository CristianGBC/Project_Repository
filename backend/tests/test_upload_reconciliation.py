"""Upload-reconciliation integration tests using real PostgreSQL."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import httpx
import pytest
from sqlalchemy import create_engine, select, update

from backend.app.config import load_settings
from backend.app.database import create_session_factory
from backend.app.models import (
    Document,
    DocumentReplica,
    DocumentVersion,
    ReconciliationAttempt,
    ReconciliationJob,
    StorageNode,
)
from backend.app.seed import seed_initial_catalog
from backend.app.services.upload_reconciliation import UploadReconciler
from backend.tests.test_coordinator_documents import parse_multipart_request
from backend.tests.test_document_catalog_integration import (
    _request,
    replicated_storage_handler,
)


@pytest.fixture(scope="module")
def reconciliation_catalog():
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
def reset_nodes(reconciliation_catalog):
    session_factory, _ = reconciliation_catalog
    with session_factory.begin() as session:
        session.execute(
            update(StorageNode).values(
                health="up", health_changed_at=datetime.now(timezone.utc)
            )
        )


def reconciliation_settings(**overrides):
    values = {
        "reconciliation_interval_seconds": 0.01,
        "reconciliation_retry_backoff_seconds": 0,
        "reconciliation_max_attempts": 5,
        "reconciliation_transfer_timeout_seconds": 1,
        "repair_internal_token": "test-token",
    }
    values.update(overrides)
    return replace(load_settings(), **values)


def reconciliation_storage_handler():
    base = replicated_storage_handler()
    standard_failures: set[str] = set()
    internal_failures: set[str] = set()
    read_faults: dict[str, str] = {}
    internal_puts: list[str] = []
    internal_delay = {"seconds": 0.0}

    async def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        is_internal = request.url.path.startswith("/internal/repair/")
        if request.method == "PUT" and not is_internal and host in standard_failures:
            raise httpx.ConnectError("initial upload failure", request=request)
        if is_internal and request.method == "PUT":
            storage_id = request.url.path.rsplit("/", 1)[-1]
            internal_puts.append(host)
            if internal_delay["seconds"]:
                await asyncio.sleep(internal_delay["seconds"])
            if host in internal_failures:
                return httpx.Response(500, json={"detail": "temporary failure"})
            if request.headers.get("X-Repair-Token") != "test-token":
                return httpx.Response(403, json={"detail": "forbidden"})
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
            base.records[(host, storage_id)] = (metadata, content)
            return httpx.Response(200, json={**metadata, "status": "stored"})
        if request.method == "GET" and request.url.path.startswith("/files/"):
            fault = read_faults.get(host)
            if fault == "unreachable":
                raise httpx.ConnectError("temporary source failure", request=request)
            if fault == "corrupted":
                storage_id = request.url.path.strip("/").split("/")[1]
                record = base.records.get((host, storage_id))
                if record is not None:
                    return httpx.Response(200, content=b"x" + record[1][1:])
        return await base(request)

    handler.records = base.records
    handler.standard_failures = standard_failures
    handler.internal_failures = internal_failures
    handler.read_faults = read_faults
    handler.internal_puts = internal_puts
    handler.internal_delay = internal_delay
    return handler


def upload_version(
    handler,
    session_factory,
    content: bytes,
    document_id: str | None = None,
    replication_factor: int = 3,
):
    checksum = hashlib.sha256(content).hexdigest()
    path = "/documents" if document_id is None else f"/documents/{document_id}/versions"
    kwargs = {
        "method": "POST",
        "replication_factor": replication_factor,
        "files": {"file": (f"reconcile-{uuid4()}.txt", content, "text/plain")},
    }
    if document_id is None:
        kwargs["data"] = {"school_id": "school-math"}
    response = _request(handler, session_factory, path, **kwargs)
    with session_factory() as session:
        version = session.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.sha256 == checksum)
            .order_by(DocumentVersion.created_at.desc())
        )
        document = session.get(Document, version.document_id)
        return response, str(document.id), str(version.id)


def run_job(handler, settings, session_factory, engine, version_id: str):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            reconciler = UploadReconciler(settings, session_factory, client, engine)
            reconciler.discover_jobs()
            with session_factory() as session:
                job_id = session.scalar(
                    select(ReconciliationJob.id).where(
                        ReconciliationJob.version_id == UUID(version_id)
                    )
                )
            return await reconciler._reconcile_job(job_id)

    return asyncio.run(run())


def version_state(session_factory, version_id: str):
    with session_factory() as session:
        version = session.get(DocumentVersion, UUID(version_id))
        replicas = session.scalars(
            select(DocumentReplica)
            .where(DocumentReplica.version_id == version.id)
            .order_by(DocumentReplica.node_id)
        ).all()
        job = session.scalar(
            select(ReconciliationJob).where(
                ReconciliationJob.version_id == version.id
            )
        )
        return version, replicas, job


def test_complete_rf3_upload_does_not_create_reconciliation_job(
    reconciliation_catalog,
) -> None:
    session_factory, engine = reconciliation_catalog
    handler = reconciliation_storage_handler()
    response, _, version_id = upload_version(handler, session_factory, b"complete")
    assert response.status_code == 201

    async def cycle():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            reconciler = UploadReconciler(
                reconciliation_settings(), session_factory, client, engine
            )
            reconciler.discover_jobs()

    asyncio.run(cycle())
    with session_factory() as session:
        job = session.scalar(
            select(ReconciliationJob).where(
                ReconciliationJob.version_id == UUID(version_id)
            )
        )
    assert job is None


@pytest.mark.parametrize(
    ("failed_nodes", "expected_healthy"),
    [({"node-agro"}, 2), ({"node-agro", "node-bio"}, 1)],
)
def test_partial_rf3_upload_is_recovered_and_atomically_published(
    reconciliation_catalog, failed_nodes: set[str], expected_healthy: int
) -> None:
    session_factory, engine = reconciliation_catalog
    handler = reconciliation_storage_handler()
    handler.standard_failures.update(failed_nodes)
    response, document_id, version_id = upload_version(
        handler, session_factory, f"partial-{uuid4()}".encode()
    )
    assert response.status_code == 503
    before, replicas, _ = version_state(session_factory, version_id)
    assert before.status == "failed"
    assert sum(replica.status == "healthy" for replica in replicas) == expected_healthy
    handler.standard_failures.clear()

    result = run_job(
        handler,
        reconciliation_settings(),
        session_factory,
        engine,
        version_id,
    )
    after, replicas, job = version_state(session_factory, version_id)
    with session_factory() as session:
        document = session.get(Document, UUID(document_id))

    assert result.status == "completed"
    assert after.status == "available"
    assert document.current_version_id == after.id
    assert sum(replica.status == "healthy" for replica in replicas) == 3
    assert len({replica.physical_host_id for replica in replicas if replica.status == "healthy"}) == 3
    assert job.status == "completed" and job.completed_at is not None


def test_already_transferred_pending_file_is_reused_without_duplicate_upload(
    reconciliation_catalog,
) -> None:
    session_factory, engine = reconciliation_catalog
    handler = reconciliation_storage_handler()
    handler.standard_failures.add("node-agro")
    _, _, version_id = upload_version(handler, session_factory, b"already transferred")
    version, _, _ = version_state(session_factory, version_id)
    source_metadata, source_content = next(iter(handler.records.values()))
    handler.records[("node-agro", str(version.document_id))] = (
        {**source_metadata, "node_id": "node-agro", "school_id": "node-agro"},
        source_content,
    )
    handler.standard_failures.clear()

    result = run_job(
        handler, reconciliation_settings(), session_factory, engine, version_id
    )

    assert result.status == "completed"
    assert handler.internal_puts == []
    assert len(version_state(session_factory, version_id)[1]) == 3


def test_corrupt_first_source_falls_back_to_another_verified_copy(
    reconciliation_catalog,
) -> None:
    session_factory, engine = reconciliation_catalog
    handler = reconciliation_storage_handler()
    handler.standard_failures.add("node-agro")
    _, _, version_id = upload_version(handler, session_factory, b"source fallback")
    handler.standard_failures.clear()
    handler.read_faults["node-math"] = "corrupted"

    result = run_job(
        handler, reconciliation_settings(), session_factory, engine, version_id
    )
    _, replicas, _ = version_state(session_factory, version_id)

    assert result.status == "completed"
    assert all(replica.status == "healthy" for replica in replicas)
    assert "node-math" in handler.internal_puts


def test_no_recoverable_copy_fails_without_fabricating_content(
    reconciliation_catalog,
) -> None:
    session_factory, engine = reconciliation_catalog
    handler = reconciliation_storage_handler()
    handler.standard_failures.update({"node-math", "node-agro", "node-bio"})
    _, _, version_id = upload_version(handler, session_factory, b"nothing stored")
    handler.standard_failures.clear()

    result = run_job(
        handler, reconciliation_settings(), session_factory, engine, version_id
    )
    version, _, job = version_state(session_factory, version_id)

    assert result.status == "failed"
    assert version.status == "failed"
    assert job.status == "failed"
    assert handler.internal_puts == []


def test_transient_destination_failure_persists_backoff_and_audit(
    reconciliation_catalog,
) -> None:
    session_factory, engine = reconciliation_catalog
    handler = reconciliation_storage_handler()
    handler.standard_failures.add("node-agro")
    _, _, version_id = upload_version(handler, session_factory, b"retry later")
    handler.standard_failures.clear()
    handler.internal_failures.add("node-agro")

    result = run_job(
        handler,
        reconciliation_settings(reconciliation_retry_backoff_seconds=30),
        session_factory,
        engine,
        version_id,
    )
    _, _, job = version_state(session_factory, version_id)
    with session_factory() as session:
        attempts = session.scalars(
            select(ReconciliationAttempt).where(
                ReconciliationAttempt.job_id == job.id
            )
        ).all()

    assert result.status == "retry_wait"
    assert job.next_attempt_at > datetime.now(timezone.utc)
    assert attempts[0].outcome == "retry"
    assert attempts[0].destination_node_id == "node-agro"
    assert attempts[0].replica_snapshot


def test_attempt_limit_stops_retries(reconciliation_catalog) -> None:
    session_factory, engine = reconciliation_catalog
    handler = reconciliation_storage_handler()
    handler.standard_failures.add("node-agro")
    _, _, version_id = upload_version(handler, session_factory, b"attempt limit")
    handler.standard_failures.clear()
    handler.internal_failures.add("node-agro")

    result = run_job(
        handler,
        reconciliation_settings(reconciliation_max_attempts=1),
        session_factory,
        engine,
        version_id,
    )

    assert result.status == "failed"
    assert version_state(session_factory, version_id)[2].attempts == 1


def test_temporary_absence_of_every_source_waits_past_transfer_attempt_limit(
    reconciliation_catalog,
) -> None:
    session_factory, engine = reconciliation_catalog
    handler = reconciliation_storage_handler()
    handler.standard_failures.add("node-agro")
    _, _, version_id = upload_version(
        handler, session_factory, b"temporarily unavailable sources"
    )
    handler.standard_failures.clear()
    handler.read_faults.update(
        {"node-math": "unreachable", "node-bio": "unreachable"}
    )
    settings = reconciliation_settings(reconciliation_max_attempts=1)

    waiting = run_job(handler, settings, session_factory, engine, version_id)
    _, _, job = version_state(session_factory, version_id)

    assert waiting.status == "retry_wait"
    assert job.status == "retry_wait"
    assert job.attempts == 1
    assert job.last_error == "all candidate sources are temporarily unavailable"

    handler.read_faults.clear()
    recovered = run_job(handler, settings, session_factory, engine, version_id)
    version, replicas, job = version_state(session_factory, version_id)

    assert recovered.status == "completed"
    assert version.status == "available"
    assert job.status == "completed" and job.attempts == 2
    assert sum(replica.status == "healthy" for replica in replicas) == 3


def test_recovered_old_version_never_replaces_newer_current_version(
    reconciliation_catalog,
) -> None:
    session_factory, engine = reconciliation_catalog
    handler = reconciliation_storage_handler()
    first, document_id, _ = upload_version(handler, session_factory, b"version one")
    assert first.status_code == 201
    handler.standard_failures.add("node-agro")
    second, _, second_id = upload_version(
        handler, session_factory, b"version two", document_id
    )
    assert second.status_code == 503
    handler.standard_failures.clear()
    third, _, third_id = upload_version(
        handler, session_factory, b"version three", document_id
    )
    assert third.status_code == 201

    result = run_job(
        handler, reconciliation_settings(), session_factory, engine, second_id
    )
    with session_factory() as session:
        document = session.get(Document, UUID(document_id))

    assert result.status == "completed"
    assert str(document.current_version_id) == third_id
    assert version_state(session_factory, second_id)[0].status == "available"


def test_soft_deleted_document_cancels_job_without_publication(
    reconciliation_catalog,
) -> None:
    session_factory, engine = reconciliation_catalog
    handler = reconciliation_storage_handler()
    handler.standard_failures.add("node-agro")
    _, document_id, version_id = upload_version(
        handler, session_factory, b"deleted partial"
    )
    handler.standard_failures.clear()
    deleted = _request(
        handler,
        session_factory,
        f"/documents/{document_id}",
        method="DELETE",
        replication_factor=3,
    )
    assert deleted.status_code == 200

    result = run_job(
        handler, reconciliation_settings(), session_factory, engine, version_id
    )
    version, _, job = version_state(session_factory, version_id)

    assert result is None
    assert job.status == "cancelled"
    assert version.status == "failed"


def test_two_reconcilers_cannot_process_same_version_concurrently(
    reconciliation_catalog,
) -> None:
    session_factory, engine = reconciliation_catalog
    handler = reconciliation_storage_handler()
    handler.standard_failures.add("node-agro")
    _, _, version_id = upload_version(handler, session_factory, b"one worker")
    handler.standard_failures.clear()
    handler.internal_delay["seconds"] = 0.03

    async def scenario():
        transport = httpx.MockTransport(handler)
        async with (
            httpx.AsyncClient(transport=transport) as first_client,
            httpx.AsyncClient(transport=transport) as second_client,
        ):
            first = UploadReconciler(
                reconciliation_settings(), session_factory, first_client, engine
            )
            second = UploadReconciler(
                reconciliation_settings(), session_factory, second_client, engine
            )
            first.discover_jobs()
            with session_factory() as session:
                job_id = session.scalar(
                    select(ReconciliationJob.id).where(
                        ReconciliationJob.version_id == UUID(version_id)
                    )
                )
            return await asyncio.gather(
                first._reconcile_job(job_id), second._reconcile_job(job_id)
            )

    results = asyncio.run(scenario())

    assert sum(result is not None for result in results) == 1
    assert len(handler.internal_puts) == 1
    assert version_state(session_factory, version_id)[0].status == "available"


def test_rf1_pending_file_can_publish_without_changing_target(
    reconciliation_catalog,
) -> None:
    session_factory, engine = reconciliation_catalog
    handler = reconciliation_storage_handler()
    handler.standard_failures.add("node-math")
    _, _, version_id = upload_version(
        handler, session_factory, b"rf1 pending", replication_factor=1
    )
    version, _, _ = version_state(session_factory, version_id)
    handler.standard_failures.clear()
    metadata = {
        "document_id": str(version.document_id),
        "filename": version.filename,
        "school_id": "node-math",
        "school": next(
            node.school
            for node in load_settings().nodes
            if node.node_id == "node-math"
        ),
        "size_bytes": version.size_bytes,
        "sha256": version.sha256,
        "created_at": version.created_at.isoformat(),
        "node_id": "node-math",
    }
    handler.records[("node-math", str(version.document_id))] = (
        metadata,
        b"rf1 pending",
    )

    result = run_job(
        handler, reconciliation_settings(), session_factory, engine, version_id
    )
    published, replicas, _ = version_state(session_factory, version_id)

    assert result.status == "completed"
    assert published.target_replicas == 1
    assert len(replicas) == 1 and replicas[0].status == "healthy"


def test_restart_closes_interrupted_audit_and_resumes_same_job(
    reconciliation_catalog,
) -> None:
    session_factory, engine = reconciliation_catalog
    handler = reconciliation_storage_handler()
    handler.standard_failures.add("node-agro")
    _, _, version_id = upload_version(handler, session_factory, b"restart audit")
    handler.standard_failures.clear()
    settings = reconciliation_settings()

    async def prepare():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            reconciler = UploadReconciler(settings, session_factory, client, engine)
            reconciler.discover_jobs()
            with session_factory.begin() as session:
                job = session.scalar(
                    select(ReconciliationJob).where(
                        ReconciliationJob.version_id == UUID(version_id)
                    )
                )
                job.status = "in_progress"
                job.attempts = 1
                session.add(
                    ReconciliationAttempt(
                        id=uuid4(),
                        job_id=job.id,
                        attempt_number=1,
                        outcome="in_progress",
                        replica_snapshot=[],
                    )
                )

    asyncio.run(prepare())
    result = run_job(handler, settings, session_factory, engine, version_id)
    _, _, job = version_state(session_factory, version_id)
    with session_factory() as session:
        attempts = session.scalars(
            select(ReconciliationAttempt)
            .where(ReconciliationAttempt.job_id == job.id)
            .order_by(ReconciliationAttempt.attempt_number)
        ).all()

    assert result.status == "completed"
    assert job.attempts == 2
    assert [attempt.outcome for attempt in attempts] == ["retry", "completed"]
    assert attempts[0].detail == "process interrupted before completion"
