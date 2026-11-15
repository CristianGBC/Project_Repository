"""Public document endpoints exposed by the coordinator."""

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
import httpx

from backend.app.config import CoordinatorSettings
from backend.app.schemas import DocumentMetadata, StoredDocumentResponse
from backend.app.services.document_service import (
    download_document,
    get_document_metadata,
    upload_document,
)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=StoredDocumentResponse, status_code=201)
async def create_document(
    request: Request,
    file: UploadFile = File(...),
    school_id: str = Form(...),
    title: str | None = Form(None),
    description: str | None = Form(None),
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
