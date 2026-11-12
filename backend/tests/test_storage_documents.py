"""Filesystem persistence and integrity tests for storage nodes."""

import asyncio
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from uuid import uuid4

import httpx

from backend.storage.application import create_app
from backend.tests.conftest import asgi_request

SCHOOL = "Escuela de Ciencias Matemáticas y Computacionales"


def document_form(content: bytes, checksum: str | None = None) -> dict[str, str]:
    return {
        "filename": "informe.pdf",
        "school_id": "node-math",
        "school": SCHOOL,
        "size_bytes": str(len(content)),
        "sha256": checksum or hashlib.sha256(content).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def store_request(application, document_id: str, content: bytes) -> httpx.Response:
    return asgi_request(
        application,
        "PUT",
        f"/files/{document_id}",
        data=document_form(content),
        files={"file": ("upload.bin", content, "application/pdf")},
    )


def test_storage_persists_metadata_and_downloads_identical_content(tmp_path: Path) -> None:
    content = b"YachayRepo document content"
    document_id = str(uuid4())
    application = create_app("node-math", SCHOOL, tmp_path, 1024)

    stored = store_request(application, document_id, content)
    metadata = asgi_request(
        application, "GET", f"/files/{document_id}/metadata"
    )
    downloaded = asgi_request(application, "GET", f"/files/{document_id}")

    assert stored.status_code == 201
    assert stored.json()["document_id"] == document_id
    assert stored.json()["sha256"] == hashlib.sha256(content).hexdigest()
    assert metadata.status_code == 200
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert (tmp_path / "documents" / f"{document_id}.bin").read_bytes() == content
    assert (tmp_path / "metadata" / f"{document_id}.json").is_file()


def test_document_remains_available_after_application_restart(tmp_path: Path) -> None:
    content = b"persistent content"
    document_id = str(uuid4())
    first_application = create_app("node-math", SCHOOL, tmp_path, 1024)
    assert store_request(first_application, document_id, content).status_code == 201

    restarted_application = create_app("node-math", SCHOOL, tmp_path, 1024)
    response = asgi_request(
        restarted_application,
        "GET",
        f"/files/{document_id}",
    )

    assert response.status_code == 200
    assert response.content == content


def test_storage_rejects_empty_and_oversized_documents(tmp_path: Path) -> None:
    application = create_app("node-math", SCHOOL, tmp_path, 4)
    empty_id = str(uuid4())
    large_id = str(uuid4())

    empty = store_request(application, empty_id, b"")
    large = store_request(application, large_id, b"12345")

    assert empty.status_code == 400
    assert large.status_code == 413
    assert not (tmp_path / "documents" / f"{empty_id}.bin").exists()
    assert not (tmp_path / "documents" / f"{large_id}.bin").exists()


def test_storage_rejects_checksum_mismatch_without_publishing(tmp_path: Path) -> None:
    content = b"checksum content"
    document_id = str(uuid4())
    application = create_app("node-math", SCHOOL, tmp_path, 1024)

    response = asgi_request(
        application,
        "PUT",
        f"/files/{document_id}",
        data=document_form(content, checksum="0" * 64),
        files={"file": ("upload.bin", content, "application/pdf")},
    )

    assert response.status_code == 400
    assert not (tmp_path / "documents" / f"{document_id}.bin").exists()
    assert not (tmp_path / "metadata" / f"{document_id}.json").exists()


def test_storage_never_overwrites_existing_document(tmp_path: Path) -> None:
    document_id = str(uuid4())
    application = create_app("node-math", SCHOOL, tmp_path, 1024)

    first = store_request(application, document_id, b"first")
    second = store_request(application, document_id, b"second")

    assert first.status_code == 201
    assert second.status_code == 409
    assert (tmp_path / "documents" / f"{document_id}.bin").read_bytes() == b"first"


def test_storage_detects_corrupted_document(tmp_path: Path) -> None:
    document_id = str(uuid4())
    application = create_app("node-math", SCHOOL, tmp_path, 1024)
    assert store_request(application, document_id, b"original").status_code == 201
    (tmp_path / "documents" / f"{document_id}.bin").write_bytes(b"corrupted")

    response = asgi_request(application, "GET", f"/files/{document_id}")

    assert response.status_code == 409


def test_storage_returns_not_found_and_rejects_invalid_identifier(tmp_path: Path) -> None:
    application = create_app("node-math", SCHOOL, tmp_path, 1024)

    missing = asgi_request(application, "GET", f"/files/{uuid4()}/metadata")
    invalid = asgi_request(application, "GET", "/files/not-a-uuid/metadata")

    assert missing.status_code == 404
    assert invalid.status_code == 400


def test_concurrent_writes_allow_only_one_publication(tmp_path: Path) -> None:
    document_id = str(uuid4())
    application = create_app("node-math", SCHOOL, tmp_path, 1024)

    async def run_requests() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://storage",
        ) as client:
            async def store(content: bytes) -> httpx.Response:
                return await client.put(
                    f"/files/{document_id}",
                    data=document_form(content),
                    files={"file": ("upload.bin", content)},
                )

            return await asyncio.gather(store(b"alpha"), store(b"bravo"))

    responses = asyncio.run(run_requests())

    assert sorted(response.status_code for response in responses) == [201, 409]
    assert (tmp_path / "documents" / f"{document_id}.bin").read_bytes() in {
        b"alpha",
        b"bravo",
    }


def test_storage_roots_isolate_documents_between_nodes(tmp_path: Path) -> None:
    document_id = str(uuid4())
    math_root = tmp_path / "math"
    bio_root = tmp_path / "bio"
    math_app = create_app("node-math", SCHOOL, math_root, 1024)
    bio_school = "Escuela de Ciencias Biológicas e Ingeniería"
    bio_app = create_app("node-bio", bio_school, bio_root, 1024)

    assert store_request(math_app, document_id, b"math").status_code == 201
    bio_missing = asgi_request(bio_app, "GET", f"/files/{document_id}/metadata")

    assert bio_missing.status_code == 404
    assert (math_root / "documents" / f"{document_id}.bin").is_file()
    assert not (bio_root / "documents" / f"{document_id}.bin").exists()
