"""Document operations coordinated across institutional storage nodes."""

from datetime import datetime, timezone
import hashlib
import tempfile
from typing import AsyncIterator, BinaryIO
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from fastapi.responses import StreamingResponse
import httpx
from pydantic import ValidationError

from backend.app.config import CoordinatorSettings, NodeSettings
from backend.app.schemas import DocumentMetadata, StoredDocumentResponse

CHUNK_SIZE = 1024 * 1024


def configured_node(
    settings: CoordinatorSettings,
    school_id: str,
) -> NodeSettings:
    for node in settings.nodes:
        if node.node_id == school_id:
            return node
    raise HTTPException(status_code=400, detail="unknown school_id")


async def _raise_remote_error(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        payload = response.json()
        detail = payload.get("detail", "storage request failed")
    except ValueError:
        detail = "storage request failed"
    status_code = response.status_code if response.status_code in {400, 404, 409, 413} else 502
    raise HTTPException(status_code=status_code, detail=detail)


def _service_unavailable(error: httpx.RequestError) -> HTTPException:
    return HTTPException(status_code=503, detail="storage node is unavailable")


async def _measure_upload(
    upload: UploadFile,
    max_size_bytes: int,
) -> tuple[int, str, BinaryIO]:
    size_bytes = 0
    digest = hashlib.sha256()
    prepared = tempfile.SpooledTemporaryFile(
        max_size=min(max_size_bytes, CHUNK_SIZE),
        mode="w+b",
    )
    try:
        while chunk := await upload.read(CHUNK_SIZE):
            size_bytes += len(chunk)
            if size_bytes > max_size_bytes:
                raise HTTPException(
                    status_code=413,
                    detail="document exceeds configured size limit",
                )
            digest.update(chunk)
            prepared.write(chunk)
        if size_bytes == 0:
            raise HTTPException(status_code=400, detail="document must not be empty")
        prepared.seek(0)
        return size_bytes, digest.hexdigest(), prepared
    except Exception:
        prepared.close()
        raise


def _validate_filename(filename: str | None) -> str:
    if not filename or "\x00" in filename or len(filename) > 255:
        raise HTTPException(status_code=400, detail="invalid filename")
    return filename


async def upload_document(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    upload: UploadFile,
    school_id: str,
) -> StoredDocumentResponse:
    node = configured_node(settings, school_id)
    filename = _validate_filename(upload.filename)
    size_bytes, checksum, prepared_file = await _measure_upload(
        upload,
        settings.max_upload_size_bytes,
    )
    metadata = DocumentMetadata(
        document_id=uuid4(),
        filename=filename,
        school_id=node.node_id,
        school=node.school,
        size_bytes=size_bytes,
        sha256=checksum,
        created_at=datetime.now(timezone.utc),
        node_id=node.node_id,
    )
    data = {
        "filename": metadata.filename,
        "school_id": metadata.school_id,
        "school": metadata.school,
        "size_bytes": str(metadata.size_bytes),
        "sha256": metadata.sha256,
        "created_at": metadata.created_at.isoformat(),
    }
    files = {
        "file": (
            "document.bin",
            prepared_file,
            upload.content_type or "application/octet-stream",
        )
    }
    try:
        response = await client.put(
            f"{node.url}/files/{metadata.document_id}",
            data=data,
            files=files,
        )
    except httpx.RequestError as error:
        raise _service_unavailable(error) from error
    finally:
        prepared_file.close()
    await _raise_remote_error(response)
    try:
        stored = StoredDocumentResponse.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise HTTPException(status_code=502, detail="invalid storage response") from error
    expected = StoredDocumentResponse(**metadata.model_dump(), status="stored")
    if stored != expected:
        raise HTTPException(status_code=502, detail="storage confirmation does not match request")
    return stored


async def get_document_metadata(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    school_id: str,
    document_id: str,
) -> DocumentMetadata:
    node = configured_node(settings, school_id)
    try:
        response = await client.get(f"{node.url}/files/{document_id}/metadata")
    except httpx.RequestError as error:
        raise _service_unavailable(error) from error
    await _raise_remote_error(response)
    try:
        metadata = DocumentMetadata.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise HTTPException(status_code=502, detail="invalid storage response") from error
    if (
        str(metadata.document_id) != document_id
        or metadata.school_id != node.node_id
        or metadata.node_id != node.node_id
        or metadata.school != node.school
    ):
        raise HTTPException(status_code=502, detail="storage metadata identity mismatch")
    return metadata


def _download_headers(filename: str) -> dict[str, str]:
    ascii_name = "".join(
        character
        for character in filename
        if 32 <= ord(character) < 127 and character not in {'"', "\\"}
    ) or "document"
    encoded_name = quote(filename, safe="")
    return {
        "Content-Disposition": (
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"
        )
    }


async def _stream_and_close(source: BinaryIO) -> AsyncIterator[bytes]:
    try:
        while chunk := source.read(CHUNK_SIZE):
            yield chunk
    finally:
        source.close()


async def download_document(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    school_id: str,
    document_id: str,
) -> StreamingResponse:
    node = configured_node(settings, school_id)
    metadata = await get_document_metadata(client, settings, school_id, document_id)
    request = client.build_request("GET", f"{node.url}/files/{document_id}")
    try:
        response = await client.send(request, stream=True)
    except httpx.RequestError as error:
        raise _service_unavailable(error) from error

    temporary = tempfile.SpooledTemporaryFile(
        max_size=min(settings.max_upload_size_bytes, CHUNK_SIZE),
        mode="w+b",
    )
    size_bytes = 0
    digest = hashlib.sha256()
    try:
        if not response.is_success:
            await response.aread()
            await _raise_remote_error(response)
        async for chunk in response.aiter_bytes(CHUNK_SIZE):
            size_bytes += len(chunk)
            if size_bytes > settings.max_upload_size_bytes:
                raise HTTPException(status_code=502, detail="storage returned oversized content")
            digest.update(chunk)
            temporary.write(chunk)
    except httpx.RequestError as error:
        temporary.close()
        raise _service_unavailable(error) from error
    except Exception:
        temporary.close()
        raise
    finally:
        await response.aclose()

    if size_bytes != metadata.size_bytes or digest.hexdigest() != metadata.sha256:
        temporary.close()
        raise HTTPException(status_code=409, detail="document integrity verification failed")
    temporary.seek(0)
    return StreamingResponse(
        _stream_and_close(temporary),
        media_type="application/octet-stream",
        headers=_download_headers(metadata.filename),
    )
