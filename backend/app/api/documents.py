"""Public document endpoints exposed by the coordinator."""

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
import httpx

from backend.app.config import CoordinatorSettings
from backend.app.schemas import (
    DocumentLifecycleResponse,
    DocumentMetadata,
    DocumentVersionHistory,
    StoredDocumentResponse,
)
from backend.app.services.document_service import (
    download_document,
    download_document_version,
    get_document_metadata,
    get_document_versions,
    restore_document,
    soft_delete_document,
    upload_document,
    upload_document_version,
)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=StoredDocumentResponse, status_code=201)
async def create_document(
    request: Request,
    file: UploadFile = File(...),
    school_id: str = Form(...),
    title: str | None = Form(None),
    description: str | None = Form(None),
    document_type_id: str | None = Form(None),
) -> StoredDocumentResponse:
    client: httpx.AsyncClient = request.app.state.http_client
    settings: CoordinatorSettings = request.app.state.settings
    return await upload_document(
        client,
        settings,
        file,
        school_id,
        request.app.state.session_factory,
        title,
        description,
        document_type_id,
    )


@router.post(
    "/{document_id}/versions",
    response_model=StoredDocumentResponse,
    status_code=201,
)
async def create_document_version(
    request: Request,
    document_id: str,
    file: UploadFile = File(...),
) -> StoredDocumentResponse:
    return await upload_document_version(
        request.app.state.http_client,
        request.app.state.settings,
        file,
        document_id,
        request.app.state.session_factory,
    )


@router.get(
    "/{document_id}/versions",
    response_model=DocumentVersionHistory,
)
async def read_document_versions(
    request: Request,
    document_id: str,
) -> DocumentVersionHistory:
    return get_document_versions(
        request.app.state.session_factory,
        document_id,
    )


@router.get(
    "/{document_id}/versions/{version_number}/download",
    response_class=StreamingResponse,
)
async def read_document_version(
    request: Request,
    document_id: str,
    version_number: int,
) -> StreamingResponse:
    return await download_document_version(
        request.app.state.http_client,
        request.app.state.settings,
        document_id,
        version_number,
        request.app.state.session_factory,
    )


@router.delete(
    "/{document_id}",
    response_model=DocumentLifecycleResponse,
)
async def delete_document(
    request: Request,
    document_id: str,
) -> DocumentLifecycleResponse:
    return soft_delete_document(
        request.app.state.session_factory,
        request.app.state.settings,
        document_id,
    )


@router.post(
    "/{document_id}/restore",
    response_model=DocumentLifecycleResponse,
)
async def restore_deleted_document(
    request: Request,
    document_id: str,
) -> DocumentLifecycleResponse:
    return restore_document(
        request.app.state.session_factory,
        request.app.state.settings,
        document_id,
    )


@router.get("/{school_id}/{document_id}", response_model=DocumentMetadata)
async def read_document_metadata(
    request: Request,
    school_id: str,
    document_id: str,
) -> DocumentMetadata:
    client: httpx.AsyncClient = request.app.state.http_client
    settings: CoordinatorSettings = request.app.state.settings
    return await get_document_metadata(
        client,
        settings,
        school_id,
        document_id,
        request.app.state.session_factory,
    )


@router.get("/{school_id}/{document_id}/download", response_class=StreamingResponse)
async def read_document(
    request: Request,
    school_id: str,
    document_id: str,
) -> StreamingResponse:
    client: httpx.AsyncClient = request.app.state.http_client
    settings: CoordinatorSettings = request.app.state.settings
    return await download_document(
        client,
        settings,
        school_id,
        document_id,
        request.app.state.session_factory,
    )
