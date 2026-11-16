"""Stage 4.3/4.4 tests using an explicitly isolated PostgreSQL database."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, select

from backend.app.config import load_settings
from backend.app.database import create_session_factory
from backend.app.models import Document, DocumentReplica, DocumentVersion, User
from backend.app.seed import seed_initial_catalog
from backend.tests.conftest import coordinator_request
from backend.tests.test_coordinator_documents import memory_storage_handler


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


def _request(handler, session_factory, path, method="GET", **kwargs):
    return coordinator_request(
        handler,
        path,
        settings=load_settings(),
        method=method,
        session_factory=session_factory,
        **kwargs,
    )


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
    assert payload["sha256"] == hashlib.sha256(content).hexdigest()

    with catalog_session_factory() as session:
        document = session.get(Document, payload["document_id"])
        version = session.get(DocumentVersion, payload["version_id"])
        replica = session.scalar(
            select(DocumentReplica).where(DocumentReplica.version_id == version.id)
        )
        technical_user = session.get(User, load_settings().technical_user_id)
        assert document.current_version_id == version.id
        assert version.target_replicas == 1
        assert version.status == "available"
        assert replica.status == "healthy"
        assert replica.node_id == "node-math"
        assert replica.stored_at is not None and replica.verified_at is not None
        assert technical_user is not None and technical_user.is_admin is False


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
        assert response.json()["sha256"] == hashlib.sha256(
            contents[number]
        ).hexdigest()

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
            select(DocumentVersion).where(
                DocumentVersion.document_id == document.id
            )
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
