"""FastAPI application shared by all six logical storage nodes."""

import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.schemas import DocumentMetadata, StoredDocumentResponse
from backend.storage.document_store import (
    DocumentConflict,
    DocumentCorrupted,
    DocumentNotFound,
    DocumentStore,
    DocumentTooLarge,
    InvalidDocument,
)


class StorageHealth(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["storage"] = "storage"
    node_id: str
    school: str


def create_app(
    node_id: str | None = None,
    school_name: str | None = None,
    data_root: Path | None = None,
    max_upload_size_bytes: int | None = None,
) -> FastAPI:
    """Build one node using explicit values or its container environment."""

    configured_node_id = node_id or os.getenv("NODE_ID")
    configured_school = school_name or os.getenv("SCHOOL_NAME")
    if not configured_node_id or not configured_school:
        raise RuntimeError("NODE_ID and SCHOOL_NAME must be configured")
    configured_data_root = data_root or Path(os.getenv("DATA_ROOT", "/data"))
    configured_max_size = max_upload_size_bytes or int(
        float(os.getenv("MAX_UPLOAD_SIZE_MB", "10")) * 1024 * 1024
    )
    if configured_max_size <= 0:
        raise RuntimeError("MAX_UPLOAD_SIZE_MB must be greater than zero")
    document_store = DocumentStore(configured_data_root, configured_max_size)

    application = FastAPI(
        title=f"YachayRepo Storage - {configured_node_id}",
        version="0.1.0",
    )

    @application.get("/health", response_model=StorageHealth)
    async def storage_health() -> StorageHealth:
        return StorageHealth(
            node_id=configured_node_id,
            school=configured_school,
        )

    def storage_error(error: Exception) -> HTTPException:
        if isinstance(error, InvalidDocument):
            return HTTPException(status_code=400, detail=str(error))
        if isinstance(error, DocumentNotFound):
            return HTTPException(status_code=404, detail=str(error))
        if isinstance(error, DocumentConflict):
            return HTTPException(status_code=409, detail=str(error))
        if isinstance(error, DocumentCorrupted):
            return HTTPException(status_code=409, detail=str(error))
        if isinstance(error, DocumentTooLarge):
            return HTTPException(status_code=413, detail=str(error))
        return HTTPException(status_code=500, detail="storage operation failed")

    @application.put(
        "/files/{document_id}",
        response_model=StoredDocumentResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def store_document(
        document_id: str,
        file: UploadFile = File(...),
        filename: str = Form(...),
        school_id: str = Form(...),
        school: str = Form(...),
        size_bytes: int = Form(...),
        sha256: str = Form(...),
        created_at: str = Form(...),
    ) -> StoredDocumentResponse:
        if school_id != configured_node_id:
            raise HTTPException(status_code=400, detail="school_id does not match node")
        if school != configured_school:
            raise HTTPException(status_code=400, detail="school does not match node")
        try:
            metadata = DocumentMetadata.model_validate(
                {
                    "document_id": document_id,
                    "filename": filename,
                    "school_id": school_id,
                    "school": school,
                    "size_bytes": size_bytes,
                    "sha256": sha256,
                    "created_at": created_at,
                    "node_id": configured_node_id,
                }
            )
            stored = await document_store.store(file, metadata)
            return StoredDocumentResponse(**stored.model_dump(), status="stored")
        except (InvalidDocument, DocumentTooLarge, DocumentConflict) as error:
            raise storage_error(error) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail="invalid document metadata") from error

    @application.get(
        "/files/{document_id}/metadata",
        response_model=DocumentMetadata,
    )
    async def document_metadata(document_id: str) -> DocumentMetadata:
        try:
            return await document_store.load_metadata(document_id)
        except (
            InvalidDocument,
            DocumentNotFound,
            DocumentCorrupted,
        ) as error:
            raise storage_error(error) from error

    @application.get("/files/{document_id}", response_class=StreamingResponse)
    async def download_document(document_id: str) -> StreamingResponse:
        try:
            path, metadata = await document_store.verified_document(document_id)
            async def stream_file():
                with path.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        yield chunk

            return StreamingResponse(
                stream_file(),
                media_type="application/octet-stream",
                headers={
                    "Content-Length": str(metadata.size_bytes),
                    "X-Document-SHA256": metadata.sha256,
                },
            )
        except (
            InvalidDocument,
            DocumentNotFound,
            DocumentCorrupted,
        ) as error:
            raise storage_error(error) from error

    return application
