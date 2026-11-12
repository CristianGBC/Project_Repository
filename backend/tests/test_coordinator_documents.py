"""Public document workflow tests through the coordinator."""

import hashlib
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from uuid import UUID

import httpx

from backend.app.config import load_settings
from backend.tests.conftest import coordinator_request

MATH_SCHOOL = "Escuela de Ciencias Matemáticas y Computacionales"


async def parse_multipart_request(
    request: httpx.Request,
) -> tuple[dict[str, str], bytes]:
    body = await request.aread()
    mime_message = BytesParser(policy=default).parsebytes(
        b"Content-Type: "
        + request.headers["content-type"].encode("ascii")
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + body
    )
    fields: dict[str, str] = {}
    file_content = b""
    for part in mime_message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        value = part.get_payload(decode=True)
        if part.get_filename() is not None:
            file_content = value
        elif name is not None:
            fields[name] = value.decode("utf-8")
    return fields, file_content


def memory_storage_handler(expected_host: str = "node-math"):
    records: dict[str, tuple[dict, bytes]] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == expected_host
        path_parts = request.url.path.strip("/").split("/")
        document_id = path_parts[1]
        if request.method == "PUT":
            fields, content = await parse_multipart_request(request)
            metadata = {
                "document_id": document_id,
                "filename": fields["filename"],
                "school_id": fields["school_id"],
                "school": fields["school"],
                "size_bytes": int(fields["size_bytes"]),
                "sha256": fields["sha256"],
                "created_at": fields["created_at"],
                "node_id": expected_host,
            }
            records[document_id] = (metadata, content)
            return httpx.Response(201, json={**metadata, "status": "stored"})
        if document_id not in records:
            return httpx.Response(404, json={"detail": "document does not exist"})
        metadata, content = records[document_id]
        if request.url.path.endswith("/metadata"):
            return httpx.Response(200, json=metadata)
        return httpx.Response(200, content=content)

    return handler


def upload_through_coordinator(handler, content: bytes, filename: str = "tesis.pdf"):
    return coordinator_request(
        handler,
        "/documents",
        method="POST",
        data={"school_id": "node-math"},
        files={"file": (filename, content, "application/pdf")},
    )


def test_complete_upload_metadata_and_download_flow(tmp_path: Path) -> None:
    content = b"real PDF-like document content"
    handler = memory_storage_handler()

    uploaded = upload_through_coordinator(handler, content, "investigación.pdf")
    payload = uploaded.json()
    document_id = payload["document_id"]
    metadata = coordinator_request(
        handler,
        f"/documents/node-math/{document_id}",
    )
    downloaded = coordinator_request(
        handler,
        f"/documents/node-math/{document_id}/download",
    )

    assert uploaded.status_code == 201
    assert str(UUID(document_id)) == document_id
    assert payload["school_id"] == "node-math"
    assert payload["node_id"] == "node-math"
    assert payload["sha256"] == hashlib.sha256(content).hexdigest()
    assert payload["status"] == "stored"
    assert metadata.status_code == 200
    assert metadata.json()["filename"] == "investigación.pdf"
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert "filename*=UTF-8''" in downloaded.headers["content-disposition"]


def test_coordinator_selects_node_from_school_id(tmp_path: Path) -> None:
    visited_hosts: list[str] = []
    storage = memory_storage_handler()

    async def handler(request: httpx.Request) -> httpx.Response:
        visited_hosts.append(request.url.host)
        return await storage(request)

    response = upload_through_coordinator(handler, b"math document")

    assert response.status_code == 201
    assert visited_hosts == ["node-math"]


def test_coordinator_rejects_unknown_school_without_contacting_storage() -> None:
    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected storage request to {request.url}")

    response = coordinator_request(
        unexpected_request,
        "/documents",
        method="POST",
        data={"school_id": "node-unknown"},
        files={"file": ("file.pdf", b"content")},
    )

    assert response.status_code == 400


def test_coordinator_rejects_empty_and_oversized_uploads(monkeypatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "0.000004")
    settings = load_settings()

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected storage request to {request.url}")

    empty = coordinator_request(
        unexpected_request,
        "/documents",
        settings,
        method="POST",
        data={"school_id": "node-math"},
        files={"file": ("empty.pdf", b"")},
    )
    oversized = coordinator_request(
        unexpected_request,
        "/documents",
        settings,
        method="POST",
        data={"school_id": "node-math"},
        files={"file": ("large.pdf", b"12345")},
    )

    assert empty.status_code == 400
    assert oversized.status_code == 413


def test_coordinator_maps_unavailable_node_and_timeout_to_503() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    unavailable_response = upload_through_coordinator(unavailable, b"content")
    timeout_response = upload_through_coordinator(timeout, b"content")

    assert unavailable_response.status_code == 503
    assert timeout_response.status_code == 503


def test_coordinator_rejects_malformed_storage_confirmation() -> None:
    def malformed_response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"status": "stored"})

    response = upload_through_coordinator(malformed_response, b"content")

    assert response.status_code == 502


def test_coordinator_rejects_confirmation_with_wrong_checksum(tmp_path: Path) -> None:
    storage = memory_storage_handler()

    async def tampered_confirmation(request: httpx.Request) -> httpx.Response:
        response = await storage(request)
        payload = response.json()
        payload["sha256"] = "0" * 64
        return httpx.Response(response.status_code, json=payload)

    response = upload_through_coordinator(tampered_confirmation, b"content")

    assert response.status_code == 502


def test_coordinator_returns_404_for_missing_document(tmp_path: Path) -> None:
    response = coordinator_request(
        memory_storage_handler(),
        "/documents/node-math/00000000-0000-0000-0000-000000000001",
    )

    assert response.status_code == 404
