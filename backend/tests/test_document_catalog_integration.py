"""Stage 4.3 tests using only an explicitly isolated PostgreSQL database."""

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
