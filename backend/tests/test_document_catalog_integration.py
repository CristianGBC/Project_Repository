"""Stage 4.3/4.4 tests using an explicitly isolated PostgreSQL database."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, select
import httpx

from backend.app.config import load_settings
from backend.app.database import create_session_factory
from backend.app.models import (
    Document,
    DocumentReplica,
    DocumentType,
    DocumentVersion,
    StorageNode,
    Subject,
    User,
)
from backend.app.seed import seed_initial_catalog
from backend.tests.conftest import coordinator_request
from backend.tests.test_coordinator_documents import (
    memory_storage_handler,
    parse_multipart_request,
)


@pytest.fixture(scope="module")
def catalog_session_factory():
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


def _request(
    handler,
    session_factory,
    path,
    method="GET",
    replication_factor=1,
    settings=None,
    **kwargs,
):
    return coordinator_request(
        handler,
        path,
        settings=settings
        or replace(load_settings(), replication_factor=replication_factor),
        method=method,
        session_factory=session_factory,
        **kwargs,
    )


def replicated_storage_handler(failing_host: str | None = None):
    records: dict[tuple[str, str], tuple[dict, bytes]] = {}
    transfer_started = 0
    active_transfers = 0
    max_active_transfers = 0
    pending_snapshots: list[tuple[int, str]] = []
    session_factory = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal transfer_started, active_transfers, max_active_transfers
        host = request.url.host
        document_id = request.url.path.strip("/").split("/")[1]
        key = (host, document_id)
        if request.method == "PUT":
            fields, content = await parse_multipart_request(request)
            transfer_started += 1
            active_transfers += 1
            max_active_transfers = max(max_active_transfers, active_transfers)
            if session_factory is not None:
                with session_factory() as session:
                    version = session.scalar(
                        select(DocumentVersion).where(
                            DocumentVersion.sha256 == fields["sha256"],
                            DocumentVersion.size_bytes == int(fields["size_bytes"]),
                        ).order_by(DocumentVersion.created_at.desc())
                    )
                    replicas = session.scalars(
                        select(DocumentReplica).where(
                            DocumentReplica.version_id == version.id
                        )
                    ).all()
                    pending_snapshots.append(
                        (
                            sum(replica.status == "pending" for replica in replicas),
                            version.status,
                        )
                    )
            await asyncio.sleep(0.01)
            active_transfers -= 1
            if host == failing_host:
                raise httpx.ConnectError("simulated failure", request=request)
            metadata = {
                "document_id": document_id,
                "filename": fields["filename"],
                "school_id": fields["school_id"],
                "school": fields["school"],
                "size_bytes": int(fields["size_bytes"]),
                "sha256": fields["sha256"],
                "created_at": fields["created_at"],
                "node_id": fields["school_id"],
            }
            records[key] = (metadata, content)
            return httpx.Response(201, json={**metadata, "status": "stored"})
        if key not in records:
            return httpx.Response(404, json={"detail": "document does not exist"})
        metadata, content = records[key]
        if request.url.path.endswith("/metadata"):
            return httpx.Response(200, json=metadata)
        return httpx.Response(200, content=content)

    handler.records = records
    handler.pending_snapshots = pending_snapshots

    def attach(value):
        nonlocal session_factory
        session_factory = value

    handler.attach = attach
    handler.stats = lambda: (transfer_started, max_active_transfers)
    return handler


class InterruptedReadStream(httpx.AsyncByteStream):
    def __init__(self, request: httpx.Request) -> None:
        self.request = request

    async def __aiter__(self):
        yield b"partial"
        raise httpx.ReadError("interrupted", request=self.request)


def read_fault_handler(storage_handler, faults: dict[str, str]):
    visits: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        is_file_read = (
            request.method == "GET"
            and request.url.path.startswith("/files/")
            and not request.url.path.endswith("/metadata")
        )
        if not is_file_read:
            return await storage_handler(request)

        host = request.url.host
        storage_id = request.url.path.strip("/").split("/")[1]
        visits.append((host, storage_id))
        fault = faults.get(host)
        if fault == "connection":
            raise httpx.ConnectError("offline", request=request)
        if fault == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if fault == "server_error":
            return httpx.Response(500, json={"detail": "temporary"})
        if fault == "missing":
            return httpx.Response(404, json={"detail": "missing"})
        if fault == "interrupted":
            return httpx.Response(200, stream=InterruptedReadStream(request))
        if fault in {"corrupted", "short"}:
            _, content = storage_handler.records[(host, storage_id)]
            if fault == "short":
                content = content[:-1]
            elif content:
                content = bytes([content[0] ^ 0xFF]) + content[1:]
            return httpx.Response(200, content=content)
        return await storage_handler(request)

    handler.visits = visits
    return handler


def _create_rf3_document(handler, session_factory, content: bytes | None = None):
    payload = content or f"read-failover-{uuid4()}".encode()
    response = _request(
        handler,
        session_factory,
        "/documents",
        method="POST",
        replication_factor=3,
        data={"school_id": "school-math", "title": "Read failover"},
        files={"file": (f"failover-{uuid4()}.txt", payload, "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json(), payload


def _create_subject(session_factory, code: str, name: str | None = None) -> dict:
    def unexpected_request(request):
        raise AssertionError(f"Unexpected storage request to {request.url}")

    response = _request(
        unexpected_request,
        session_factory,
        "/subjects",
        method="POST",
        json={
            "school_id": "school-math",
            "code": code,
            "name": name or code,
            "description": "Integration test subject",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_upload_persists_document_version_replica_and_technical_user(
    catalog_session_factory,
) -> None:
    content = b"catalog-backed document"
    handler = memory_storage_handler()
    response = _request(
        handler,
        catalog_session_factory,
        "/documents",
        method="POST",
        data={
            "school_id": "school-math",
            "title": "Distributed catalog",
            "description": "Stage 4.3",
            "document_type_id": "article",
        },
        files={"file": ("catalog.pdf", content, "application/pdf")},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["school_id"] == "school-math"
    assert payload["node_id"] == "node-math"
    assert payload["target_replicas"] == 1
    assert payload["version_number"] == 1
    assert payload["version_status"] == "available"
    assert payload["document_type_id"] == "article"
    assert payload["document_type_name"] == "Artículo"
    assert payload["sha256"] == hashlib.sha256(content).hexdigest()

    with catalog_session_factory() as session:
        document = session.get(Document, payload["document_id"])
        version = session.get(DocumentVersion, payload["version_id"])
        replica = session.scalar(
            select(DocumentReplica).where(DocumentReplica.version_id == version.id)
        )
        technical_user = session.get(User, load_settings().technical_user_id)
        assert document.current_version_id == version.id
        assert document.document_type_id == "article"
        assert version.target_replicas == 1
        assert version.status == "available"
        assert replica.status == "healthy"
        assert replica.node_id == "node-math"
        assert replica.stored_at is not None and replica.verified_at is not None
        assert technical_user is not None and technical_user.is_admin is False


def test_seed_registers_document_types_and_upload_rejects_unknown_type(
    catalog_session_factory,
) -> None:
    with catalog_session_factory() as session:
        types = session.query(DocumentType).order_by(DocumentType.id).all()
        assert [(item.id, item.name) for item in types] == [
            ("administrative", "Documento administrativo"),
            ("article", "Artículo"),
            ("study_material", "Material de estudio"),
            ("syllabus", "Syllabus"),
        ]

    def unexpected_request(request):
        raise AssertionError(f"Unexpected storage request to {request.url}")

    response = _request(
        unexpected_request,
        catalog_session_factory,
        "/documents",
        method="POST",
        data={
            "school_id": "school-math",
            "document_type_id": "unknown-type",
        },
        files={"file": ("unknown.pdf", b"content", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unknown or inactive document_type_id"


def test_rf3_stores_three_verified_replicas_on_distinct_catalog_hosts(
    catalog_session_factory,
) -> None:
    content = f"rf3-success-{uuid4()}".encode()
    checksum = hashlib.sha256(content).hexdigest()
    handler = replicated_storage_handler()
    handler.attach(catalog_session_factory)

    response = _request(
        handler,
        catalog_session_factory,
        "/documents",
        method="POST",
        replication_factor=3,
        data={"school_id": "school-math", "title": "RF3 success"},
        files={"file": (f"rf3-{uuid4()}.txt", content, "text/plain")},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["target_replicas"] == 3
    assert payload["node_id"] == "node-math"
    assert payload["version_status"] == "available"
    assert handler.stats() == (3, 3)
    assert handler.pending_snapshots
    assert set(handler.pending_snapshots) == {(3, "pending")}

    with catalog_session_factory() as session:
        version = session.get(DocumentVersion, payload["version_id"])
        document = session.get(Document, payload["document_id"])
        replicas = session.scalars(
            select(DocumentReplica).where(DocumentReplica.version_id == version.id)
        ).all()
        node_hosts = {
            node.id: node.physical_host_id
            for node in session.scalars(select(StorageNode)).all()
        }

        assert version.status == "available"
        assert version.target_replicas == 3
        assert document.current_version_id == version.id
        assert len(replicas) == 3
        assert {replica.status for replica in replicas} == {"healthy"}
        assert len({replica.node_id for replica in replicas}) == 3
        assert len({replica.physical_host_id for replica in replicas}) == 3
        selected_nodes = {replica.node_id for replica in replicas}
        assert selected_nodes == {"node-math", "node-agro", "node-bio"}
        assert not {"node-bio", "node-phy"} <= selected_nodes
        assert not {"node-math", "node-chem"} <= selected_nodes
        assert all(
            node_hosts[replica.node_id] == replica.physical_host_id
            for replica in replicas
        )

    stored = [
        record
        for (host, storage_id), record in handler.records.items()
        if storage_id == payload["document_id"]
    ]
    assert len(stored) == 3
    assert {stored_content for _, stored_content in stored} == {content}
    assert {metadata["sha256"] for metadata, _ in stored} == {checksum}

    downloaded = _request(
        handler,
        catalog_session_factory,
        f"/documents/school-math/{payload['document_id']}/download",
        replication_factor=3,
    )
    assert downloaded.status_code == 200
    assert downloaded.content == content


def test_read_uses_first_healthy_replica_without_failover(
    catalog_session_factory,
) -> None:
    storage = replicated_storage_handler()
    document, content = _create_rf3_document(storage, catalog_session_factory)
    handler = read_fault_handler(storage, {})

    downloaded = _request(
        handler,
        catalog_session_factory,
        f"/documents/school-math/{document['document_id']}/download",
        replication_factor=3,
    )

    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert handler.visits == [("node-math", document["document_id"])]


@pytest.mark.parametrize(
    ("failure_type", "expected_status", "recorded_type"),
    [
        ("connection", "healthy", "unreachable"),
        ("timeout", "healthy", "unreachable"),
        ("server_error", "healthy", "server_error"),
        ("interrupted", "healthy", "interrupted"),
        ("missing", "missing", "missing"),
        ("corrupted", "corrupted", "corrupted"),
        ("short", "corrupted", "corrupted"),
    ],
)
def test_read_failover_uses_second_replica_and_classifies_first_failure(
    catalog_session_factory,
    failure_type: str,
    expected_status: str,
    recorded_type: str,
) -> None:
    storage = replicated_storage_handler()
    document, content = _create_rf3_document(storage, catalog_session_factory)
    handler = read_fault_handler(storage, {"node-math": failure_type})

    downloaded = _request(
        handler,
        catalog_session_factory,
        f"/documents/school-math/{document['document_id']}/download",
        replication_factor=3,
    )

    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert handler.visits == [
        ("node-math", document["document_id"]),
        ("node-agro", document["document_id"]),
    ]
    with catalog_session_factory() as session:
        version = session.get(DocumentVersion, document["version_id"])
        first = session.scalar(
            select(DocumentReplica).where(
                DocumentReplica.version_id == version.id,
                DocumentReplica.node_id == "node-math",
            )
        )
        second = session.scalar(
            select(DocumentReplica).where(
                DocumentReplica.version_id == version.id,
                DocumentReplica.node_id == "node-agro",
            )
        )
        assert version.status == "available"
        assert first.status == expected_status
        assert first.last_error.startswith(f"read {recorded_type}")
        assert second.status == "healthy"
        assert second.last_error is None


def test_read_failover_survives_two_failed_replicas(
    catalog_session_factory,
) -> None:
    storage = replicated_storage_handler()
    document, content = _create_rf3_document(storage, catalog_session_factory)
    handler = read_fault_handler(
        storage,
        {"node-math": "connection", "node-agro": "server_error"},
    )

    downloaded = _request(
        handler,
        catalog_session_factory,
        f"/documents/school-math/{document['document_id']}/download",
        replication_factor=3,
    )

    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert [host for host, _ in handler.visits] == [
        "node-math",
        "node-agro",
        "node-bio",
    ]


def test_read_failover_returns_controlled_errors_when_all_replicas_fail(
    catalog_session_factory,
) -> None:
    storage = replicated_storage_handler()
    temporary_document, _ = _create_rf3_document(storage, catalog_session_factory)
    temporary_handler = read_fault_handler(
        storage,
        {
            "node-math": "connection",
            "node-agro": "timeout",
            "node-bio": "server_error",
        },
    )
    temporary = _request(
        temporary_handler,
        catalog_session_factory,
        f"/documents/school-math/{temporary_document['document_id']}/download",
        replication_factor=3,
    )

    other_storage = replicated_storage_handler()
    invalid_document, _ = _create_rf3_document(
        other_storage, catalog_session_factory
    )
    invalid_handler = read_fault_handler(
        other_storage,
        {
            "node-math": "missing",
            "node-agro": "corrupted",
            "node-bio": "short",
        },
    )
    invalid = _request(
        invalid_handler,
        catalog_session_factory,
        f"/documents/school-math/{invalid_document['document_id']}/download",
        replication_factor=3,
    )

    assert temporary.status_code == 503
    assert temporary.json()["detail"] == "document is temporarily unavailable"
    assert invalid.status_code == 409
    assert invalid.json()["detail"] == "no intact document replica is available"
    assert len(temporary_handler.visits) == 3
    assert len(invalid_handler.visits) == 3


def test_read_failover_uses_version_specific_replicas_and_ignores_active_rf(
    catalog_session_factory,
) -> None:
    storage = replicated_storage_handler()
    first, first_content = _create_rf3_document(
        storage, catalog_session_factory, b"historical version one"
    )
    second_content = b"current version two"
    second = _request(
        storage,
        catalog_session_factory,
        f"/documents/{first['document_id']}/versions",
        method="POST",
        replication_factor=3,
        files={"file": ("historical-v2.txt", second_content, "text/plain")},
    )
    assert second.status_code == 201
    handler = read_fault_handler(storage, {"node-math": "connection"})

    historical = _request(
        handler,
        catalog_session_factory,
        f"/documents/{first['document_id']}/versions/1/download",
        replication_factor=1,
    )

    assert historical.status_code == 200
    assert historical.content == first_content
    assert {storage_id for _, storage_id in handler.visits} == {
        first["document_id"]
    }
    with catalog_session_factory() as session:
        document = session.get(Document, first["document_id"])
        assert str(document.current_version_id) == second.json()["version_id"]


def test_read_failover_closes_temporaries_and_supports_concurrent_downloads(
    catalog_session_factory,
    monkeypatch,
) -> None:
    from backend.app.services import document_service

    storage = replicated_storage_handler()
    document, content = _create_rf3_document(storage, catalog_session_factory)
    handler = read_fault_handler(storage, {"node-math": "corrupted"})
    real_spooled_file = document_service.tempfile.SpooledTemporaryFile
    temporary_files = []

    def tracked_spooled_file(*args, **kwargs):
        temporary = real_spooled_file(*args, **kwargs)
        temporary_files.append(temporary)
        return temporary

    monkeypatch.setattr(
        document_service.tempfile,
        "SpooledTemporaryFile",
        tracked_spooled_file,
    )

    def download(_value: int):
        return _request(
            handler,
            catalog_session_factory,
            f"/documents/school-math/{document['document_id']}/download",
            replication_factor=3,
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        responses = list(executor.map(download, range(3)))

    assert all(response.status_code == 200 for response in responses)
    assert all(response.content == content for response in responses)
    assert temporary_files
    assert all(temporary.closed for temporary in temporary_files)


def test_rf3_rejects_upload_when_fewer_than_three_catalog_hosts_are_eligible(
    catalog_session_factory,
) -> None:
    filename = f"insufficient-hosts-{uuid4()}.txt"
    settings = replace(load_settings(), replication_factor=3)
    settings = replace(
        settings,
        nodes=tuple(
            node
            for node in settings.nodes
            if node.node_id in {"node-math", "node-chem"}
        ),
    )

    def unexpected_request(request):
        raise AssertionError(f"Unexpected transfer to {request.url}")

    response = _request(
        unexpected_request,
        catalog_session_factory,
        "/documents",
        method="POST",
        settings=settings,
        data={"school_id": "school-math"},
        files={"file": (filename, b"not published", "text/plain")},
    )

    assert response.status_code == 503
    assert "requires 3 eligible physical hosts; found 2" in response.json()["detail"]
    with catalog_session_factory() as session:
        assert session.scalar(
            select(DocumentVersion).where(DocumentVersion.filename == filename)
        ) is None


def test_rf3_selection_uses_catalog_hosts_not_node_urls(
    catalog_session_factory,
) -> None:
    settings = replace(load_settings(), replication_factor=3)
    settings = replace(
        settings,
        nodes=tuple(
            replace(node, url=f"http://arbitrary-location-{index}.invalid:9999")
            for index, node in enumerate(settings.nodes)
        ),
    )
    handler = replicated_storage_handler()
    response = _request(
        handler,
        catalog_session_factory,
        "/documents",
        method="POST",
        settings=settings,
        data={"school_id": "school-math"},
        files={
            "file": (
                f"catalog-topology-{uuid4()}.txt",
                b"catalog topology",
                "text/plain",
            )
        },
    )

    assert response.status_code == 201, response.text
    assert {host for host, _ in handler.records} == {
        "arbitrary-location-0.invalid",
        "arbitrary-location-2.invalid",
        "arbitrary-location-5.invalid",
    }
    with catalog_session_factory() as session:
        replicas = session.scalars(
            select(DocumentReplica).where(
                DocumentReplica.version_id == response.json()["version_id"]
            )
        ).all()
        assert {replica.node_id for replica in replicas} == {
            "node-math",
            "node-agro",
            "node-bio",
        }
        assert len({replica.physical_host_id for replica in replicas}) == 3


def test_rf3_partial_failure_preserves_healthy_copies_and_previous_version(
    catalog_session_factory,
) -> None:
    successful_handler = replicated_storage_handler()
    first = _request(
        successful_handler,
        catalog_session_factory,
        "/documents",
        method="POST",
        replication_factor=3,
        data={"school_id": "school-math", "title": "RF3 failure test"},
        files={"file": ("rf3-v1.txt", b"available version", "text/plain")},
    )
    assert first.status_code == 201, first.text

    failing_handler = replicated_storage_handler(failing_host="node-agro")
    failed = _request(
        failing_handler,
        catalog_session_factory,
        f"/documents/{first.json()['document_id']}/versions",
        method="POST",
        replication_factor=3,
        files={"file": ("rf3-v2-failed.txt", b"partial version", "text/plain")},
    )

    assert failed.status_code == 503
    with catalog_session_factory() as session:
        document = session.get(Document, first.json()["document_id"])
        versions = session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version_number)
        ).all()
        failed_version = versions[1]
        replicas = session.scalars(
            select(DocumentReplica).where(
                DocumentReplica.version_id == failed_version.id
            )
        ).all()

        assert document.current_version_id == versions[0].id
        assert versions[0].status == "available"
        assert failed_version.status == "failed"
        assert len(replicas) == 3
        assert sorted(replica.status for replica in replicas) == [
            "failed",
            "healthy",
            "healthy",
        ]
        assert next(
            replica for replica in replicas if replica.status == "failed"
        ).node_id == "node-agro"
    assert len(failing_handler.records) == 2


def test_concurrent_rf3_versions_remain_unique_and_fully_replicated(
    catalog_session_factory,
) -> None:
    handler = replicated_storage_handler()
    first = _request(
        handler,
        catalog_session_factory,
        "/documents",
        method="POST",
        replication_factor=3,
        data={"school_id": "school-math", "title": "Concurrent RF3"},
        files={"file": ("concurrent-rf3-v1.txt", b"version 1", "text/plain")},
    )
    assert first.status_code == 201
    document_id = first.json()["document_id"]

    def upload(value: int):
        return _request(
            handler,
            catalog_session_factory,
            f"/documents/{document_id}/versions",
            method="POST",
            replication_factor=3,
            files={
                "file": (
                    f"concurrent-rf3-{value}.txt",
                    f"concurrent rf3 {value}".encode(),
                    "text/plain",
                )
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(upload, (2, 3)))

    assert all(response.status_code == 201 for response in responses)
    assert {response.json()["version_number"] for response in responses} == {2, 3}
    with catalog_session_factory() as session:
        versions = session.scalars(
            select(DocumentVersion).where(DocumentVersion.document_id == document_id)
        ).all()
        assert {version.version_number for version in versions} == {1, 2, 3}
        for version in versions:
            replicas = session.scalars(
                select(DocumentReplica).where(
                    DocumentReplica.version_id == version.id
                )
            ).all()
            assert version.status == "available"
            assert len(replicas) == 3
            assert {replica.status for replica in replicas} == {"healthy"}
            assert len({replica.physical_host_id for replica in replicas}) == 3


def test_metadata_survives_new_coordinator_and_download_uses_catalog_location(
    catalog_session_factory,
) -> None:
    content = b"persistent metadata"
    handler = memory_storage_handler()
    uploaded_response = _request(
        handler,
        catalog_session_factory,
        "/documents",
        method="POST",
        data={"school_id": "node-math"},
        files={"file": ("persistent.txt", content, "text/plain")},
    )
    assert uploaded_response.status_code == 201, uploaded_response.text
    uploaded = uploaded_response.json()
    document_id = uploaded["document_id"]

    metadata = _request(
        handler,
        catalog_session_factory,
        f"/documents/school-math/{document_id}",
    )
    downloaded = _request(
        handler,
        catalog_session_factory,
        f"/documents/school-math/{document_id}/download",
    )

    assert metadata.status_code == 200
    assert metadata.json()["version_status"] == "available"
    assert metadata.json()["filename"] == "persistent.txt"
    assert downloaded.status_code == 200
    assert downloaded.content == content


def test_failed_node_leaves_a_failed_reconcilable_operation(
    catalog_session_factory,
) -> None:
    filename = f"failed-{uuid4()}.pdf"

    def unavailable(request):
        import httpx

        raise httpx.ConnectError("offline", request=request)

    response = _request(
        unavailable,
        catalog_session_factory,
        "/documents",
        method="POST",
        data={"school_id": "school-math"},
        files={"file": (filename, b"will fail", "application/pdf")},
    )

    assert response.status_code == 503
    with catalog_session_factory() as session:
        version = session.scalar(
            select(DocumentVersion).where(DocumentVersion.filename == filename)
        )
        replica = session.scalar(
            select(DocumentReplica).where(DocumentReplica.version_id == version.id)
        )
        document = session.get(Document, version.document_id)
        assert version.status == "failed"
        assert replica.status == "failed"
        assert replica.attempts == 1
        assert document.current_version_id is None


def _create_versioned_document(handler, session_factory, content=b"version one"):
    response = _request(
        handler,
        session_factory,
        "/documents",
        method="POST",
        data={"school_id": "school-math", "title": "Versioned document"},
        files={"file": ("version-1.txt", content, "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_versions_two_and_three_history_and_specific_downloads(
    catalog_session_factory,
) -> None:
    handler = memory_storage_handler()
    first = _create_versioned_document(handler, catalog_session_factory)
    document_id = first["document_id"]
    contents = {1: b"version one", 2: b"version two", 3: b"version three"}

    for number in (2, 3):
        response = _request(
            handler,
            catalog_session_factory,
            f"/documents/{document_id}/versions",
            method="POST",
            files={
                "file": (
                    f"version-{number}.txt",
                    contents[number],
                    "text/plain",
                )
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["document_id"] == document_id
        assert response.json()["version_number"] == number
        assert response.json()["sha256"] == hashlib.sha256(contents[number]).hexdigest()

    history = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}/versions",
    )
    assert history.status_code == 200
    versions = history.json()["versions"]
    assert [version["version_number"] for version in versions] == [3, 2, 1]
    assert [version["is_current"] for version in versions] == [True, False, False]

    for number, content in contents.items():
        downloaded = _request(
            handler,
            catalog_session_factory,
            f"/documents/{document_id}/versions/{number}/download",
        )
        assert downloaded.status_code == 200
        assert downloaded.content == content
        assert hashlib.sha256(downloaded.content).hexdigest() == next(
            version["sha256"]
            for version in versions
            if version["version_number"] == number
        )

    current = _request(
        handler,
        catalog_session_factory,
        f"/documents/school-math/{document_id}/download",
    )
    assert current.content == contents[3]


def test_failed_new_version_preserves_previous_current_version(
    catalog_session_factory,
) -> None:
    handler = memory_storage_handler()
    first = _create_versioned_document(handler, catalog_session_factory)
    document_id = first["document_id"]

    def unavailable(request):
        import httpx

        raise httpx.ConnectError("offline", request=request)

    failed = _request(
        unavailable,
        catalog_session_factory,
        f"/documents/{document_id}/versions",
        method="POST",
        files={"file": ("failed-v2.txt", b"not stored", "text/plain")},
    )
    history = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}/versions",
    ).json()

    assert failed.status_code == 503
    assert [item["status"] for item in history["versions"]] == ["failed", "available"]
    assert history["versions"][1]["is_current"] is True
    current = _request(
        handler,
        catalog_session_factory,
        f"/documents/school-math/{document_id}/download",
    )
    assert current.content == b"version one"


def test_concurrent_version_uploads_get_unique_monotonic_numbers(
    catalog_session_factory,
) -> None:
    handler = memory_storage_handler()
    first = _create_versioned_document(handler, catalog_session_factory)
    document_id = first["document_id"]

    def upload(value: int):
        return _request(
            handler,
            catalog_session_factory,
            f"/documents/{document_id}/versions",
            method="POST",
            files={
                "file": (
                    f"concurrent-{value}.txt",
                    f"concurrent {value}".encode(),
                    "text/plain",
                )
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(upload, (1, 2)))

    assert all(response.status_code == 201 for response in responses)
    assert {response.json()["version_number"] for response in responses} == {2, 3}
    history = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}/versions",
    ).json()
    assert [item["version_number"] for item in history["versions"]] == [3, 2, 1]
    assert history["versions"][0]["is_current"] is True


def test_soft_delete_restricts_reads_and_restore_preserves_versions_and_files(
    catalog_session_factory,
) -> None:
    handler = memory_storage_handler()
    first = _create_versioned_document(handler, catalog_session_factory)
    document_id = first["document_id"]
    second_content = b"survives soft deletion"
    second = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}/versions",
        method="POST",
        files={"file": ("version-2.txt", second_content, "text/plain")},
    )
    assert second.status_code == 201

    deleted = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}",
        method="DELETE",
    )
    repeated_delete = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}",
        method="DELETE",
    )
    assert deleted.json()["status"] == "deleted"
    assert deleted.json()["deleted_at"] is not None
    assert repeated_delete.json()["status"] == "already_deleted"

    restricted_paths = (
        f"/documents/school-math/{document_id}",
        f"/documents/school-math/{document_id}/download",
        f"/documents/{document_id}/versions",
        f"/documents/{document_id}/versions/1/download",
    )
    assert all(
        _request(handler, catalog_session_factory, path).status_code == 404
        for path in restricted_paths
    )

    with catalog_session_factory() as session:
        document = session.get(Document, document_id)
        versions = session.scalars(
            select(DocumentVersion).where(DocumentVersion.document_id == document.id)
        ).all()
        replicas = session.scalars(
            select(DocumentReplica).where(
                DocumentReplica.version_id.in_([version.id for version in versions])
            )
        ).all()
        assert document.deleted_at is not None
        assert document.deleted_by == load_settings().technical_user_id
        assert len(versions) == 2
        assert len(replicas) == 2

    restored = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}/restore",
        method="POST",
    )
    repeated_restore = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}/restore",
        method="POST",
    )
    assert restored.json()["status"] == "restored"
    assert repeated_restore.json()["status"] == "already_active"
    history = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}/versions",
    )
    version_one = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}/versions/1/download",
    )
    version_two = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}/versions/2/download",
    )
    assert history.status_code == 200 and len(history.json()["versions"]) == 2
    assert version_one.content == b"version one"
    assert version_two.content == second_content


def test_subject_crud_school_filter_and_duplicate_code(catalog_session_factory) -> None:
    suffix = uuid4().hex[:8]
    subject = _create_subject(
        catalog_session_factory,
        f"ALG-{suffix}",
        "Álgebra Lineal",
    )
    handler = memory_storage_handler()

    listed = _request(
        handler,
        catalog_session_factory,
        "/subjects?school_id=school-math",
    )
    fetched = _request(
        handler,
        catalog_session_factory,
        f"/subjects/{subject['id']}",
    )
    updated = _request(
        handler,
        catalog_session_factory,
        f"/subjects/{subject['id']}",
        method="PATCH",
        json={"name": "Álgebra Lineal I", "is_active": False},
    )
    duplicate = _request(
        handler,
        catalog_session_factory,
        "/subjects",
        method="POST",
        json={
            "school_id": "school-math",
            "code": subject["code"],
            "name": "Duplicate",
        },
    )

    assert any(item["id"] == subject["id"] for item in listed.json())
    assert fetched.json()["school_id"] == "school-math"
    assert updated.json()["name"] == "Álgebra Lineal I"
    assert updated.json()["is_active"] is False
    assert duplicate.status_code == 409


def test_upload_subject_rules_and_many_to_many_persistence(
    catalog_session_factory,
) -> None:
    suffix = uuid4().hex[:8]
    algebra = _create_subject(catalog_session_factory, f"ALG2-{suffix}", "Álgebra")
    calculus = _create_subject(catalog_session_factory, f"CALC-{suffix}", "Cálculo")
    handler = memory_storage_handler()

    missing_required = _request(
        handler,
        catalog_session_factory,
        "/documents",
        method="POST",
        data={
            "school_id": "school-math",
            "document_type_id": "syllabus",
        },
        files={"file": ("syllabus.pdf", b"syllabus", "application/pdf")},
    )
    uploaded = _request(
        handler,
        catalog_session_factory,
        "/documents",
        method="POST",
        data={
            "school_id": "school-math",
            "document_type_id": "study_material",
            "subject_ids": ["", algebra["id"], calculus["id"]],
        },
        files={"file": ("material.pdf", b"material", "application/pdf")},
    )
    article = _request(
        handler,
        catalog_session_factory,
        "/documents",
        method="POST",
        data={
            "school_id": "school-math",
            "document_type_id": "article",
        },
        files={"file": ("article.pdf", b"article", "application/pdf")},
    )

    assert missing_required.status_code == 400
    assert (
        missing_required.json()["detail"]
        == "document type requires at least one subject"
    )
    assert uploaded.status_code == 201, uploaded.text
    assert {item["id"] for item in uploaded.json()["subjects"]} == {
        algebra["id"],
        calculus["id"],
    }
    assert article.status_code == 201
    assert article.json()["subjects"] == []
    with catalog_session_factory() as session:
        document = session.get(Document, uploaded.json()["document_id"])
        assert {str(subject.id) for subject in document.subjects} == {
            algebra["id"],
            calculus["id"],
        }


def test_upload_accepts_each_subject_ids_form_representation(
    catalog_session_factory,
) -> None:
    suffix = uuid4().hex[:8]
    first = _create_subject(catalog_session_factory, f"FORM1-{suffix}")
    second = _create_subject(catalog_session_factory, f"FORM2-{suffix}")
    handler = memory_storage_handler()
    representations = (
        ("single", first["id"], {first["id"]}),
        ("repeated", [first["id"], second["id"]], {first["id"], second["id"]}),
        (
            "comma-separated",
            f"{first['id']},{second['id']}",
            {first["id"], second["id"]},
        ),
        (
            "duplicates",
            f"{first['id']}, {first['id']},{second['id']}",
            {first["id"], second["id"]},
        ),
    )

    for label, subject_ids, expected_ids in representations:
        response = _request(
            handler,
            catalog_session_factory,
            "/documents",
            method="POST",
            data={
                "school_id": "school-math",
                "document_type_id": "study_material",
                "subject_ids": subject_ids,
            },
            files={
                "file": (
                    f"{label}-{uuid4()}.pdf",
                    label.encode(),
                    "application/pdf",
                )
            },
        )

        assert response.status_code == 201, response.text
        assert {item["id"] for item in response.json()["subjects"]} == expected_ids
        with catalog_session_factory() as session:
            document = session.get(Document, response.json()["document_id"])
            assert {str(subject.id) for subject in document.subjects} == expected_ids


@pytest.mark.parametrize("document_type_id", ["article", "administrative"])
def test_upload_optional_subject_type_accepts_swagger_blank_value(
    catalog_session_factory,
    document_type_id: str,
) -> None:
    handler = memory_storage_handler()
    response = _request(
        handler,
        catalog_session_factory,
        "/documents",
        method="POST",
        data={
            "school_id": "school-math",
            "document_type_id": document_type_id,
            "subject_ids": "",
        },
        files={
            "file": (
                f"{document_type_id}-{uuid4()}.pdf",
                b"optional subject",
                "application/pdf",
            )
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["subjects"] == []


@pytest.mark.parametrize("document_type_id", ["syllabus", "study_material"])
def test_upload_required_subject_type_rejects_swagger_blank_value(
    catalog_session_factory,
    document_type_id: str,
) -> None:
    def unexpected_request(request):
        raise AssertionError(f"Unexpected storage request to {request.url}")

    response = _request(
        unexpected_request,
        catalog_session_factory,
        "/documents",
        method="POST",
        data={
            "school_id": "school-math",
            "document_type_id": document_type_id,
            "subject_ids": "",
        },
        files={"file": ("required.pdf", b"required subject", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "document type requires at least one subject"


def test_upload_rejects_inactive_or_other_school_subject(
    catalog_session_factory,
) -> None:
    suffix = uuid4().hex[:8]
    inactive = _create_subject(catalog_session_factory, f"INACTIVE-{suffix}")
    handler = memory_storage_handler()
    _request(
        handler,
        catalog_session_factory,
        f"/subjects/{inactive['id']}",
        method="PATCH",
        json={"is_active": False},
    )
    other_school = _request(
        handler,
        catalog_session_factory,
        "/subjects",
        method="POST",
        json={
            "school_id": "school-bio",
            "code": f"BIO-{suffix}",
            "name": "Biología",
        },
    ).json()

    inactive_response = _request(
        handler,
        catalog_session_factory,
        "/documents",
        method="POST",
        data={
            "school_id": "school-math",
            "document_type_id": "syllabus",
            "subject_ids": [inactive["id"]],
        },
        files={"file": ("inactive.pdf", b"content", "application/pdf")},
    )
    other_school_response = _request(
        handler,
        catalog_session_factory,
        "/documents",
        method="POST",
        data={
            "school_id": "school-math",
            "document_type_id": "syllabus",
            "subject_ids": [other_school["id"]],
        },
        files={"file": ("other-school.pdf", b"content", "application/pdf")},
    )

    assert inactive_response.status_code == 400
    assert inactive_response.json()["detail"] == "one or more subjects are inactive"
    assert other_school_response.status_code == 400
    assert (
        other_school_response.json()["detail"]
        == "subjects must belong to the document school"
    )


def test_replace_subjects_preserves_versions_and_enforces_type_rule(
    catalog_session_factory,
) -> None:
    suffix = uuid4().hex[:8]
    first_subject = _create_subject(catalog_session_factory, f"NUM-{suffix}")
    second_subject = _create_subject(catalog_session_factory, f"OPT-{suffix}")
    handler = memory_storage_handler()
    uploaded = _request(
        handler,
        catalog_session_factory,
        "/documents",
        method="POST",
        data={
            "school_id": "school-math",
            "document_type_id": "study_material",
            "subject_ids": [first_subject["id"]],
        },
        files={"file": ("numerical.pdf", b"v1", "application/pdf")},
    ).json()
    document_id = uploaded["document_id"]
    second_version = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}/versions",
        method="POST",
        files={"file": ("numerical-v2.pdf", b"v2", "application/pdf")},
    )
    before = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}/versions",
    ).json()
    replaced = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}/subjects",
        method="PUT",
        json={"subject_ids": [second_subject["id"]]},
    )
    rejected_empty = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}/subjects",
        method="PUT",
        json={"subject_ids": []},
    )
    after = _request(
        handler,
        catalog_session_factory,
        f"/documents/{document_id}/versions",
    ).json()

    assert second_version.status_code == 201
    assert [item["id"] for item in replaced.json()["subjects"]] == [
        second_subject["id"]
    ]
    assert rejected_empty.status_code == 400
    assert before == after


def test_document_filters_and_subject_documents_exclude_soft_deleted(
    catalog_session_factory,
) -> None:
    suffix = uuid4().hex[:8]
    subject = _create_subject(catalog_session_factory, f"FILTER-{suffix}")
    handler = memory_storage_handler()
    uploaded = _request(
        handler,
        catalog_session_factory,
        "/documents",
        method="POST",
        data={
            "school_id": "school-math",
            "document_type_id": "syllabus",
            "subject_ids": [subject["id"]],
        },
        files={"file": ("filter.pdf", b"filter", "application/pdf")},
    ).json()
    query = (
        "/documents?school_id=school-math"
        f"&subject_id={subject['id']}&document_type_id=syllabus"
    )
    filtered = _request(handler, catalog_session_factory, query)
    by_subject = _request(
        handler,
        catalog_session_factory,
        f"/subjects/{subject['id']}/documents",
    )

    assert [item["document_id"] for item in filtered.json()] == [
        uploaded["document_id"]
    ]
    assert [item["document_id"] for item in by_subject.json()] == [
        uploaded["document_id"]
    ]

    deleted = _request(
        handler,
        catalog_session_factory,
        f"/documents/{uploaded['document_id']}",
        method="DELETE",
    )
    assert deleted.status_code == 200
    assert _request(handler, catalog_session_factory, query).json() == []
    assert (
        _request(
            handler,
            catalog_session_factory,
            f"/subjects/{subject['id']}/documents",
        ).json()
        == []
    )
