"""Public document endpoints exposed by the coordinator."""

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
import httpx
from pydantic import BeforeValidator
from sqlalchemy import select

from backend.app.auth import Principal, get_current_principal
from backend.app.config import CoordinatorSettings
from backend.app.models import (
    Document,
    DocumentReplica,
    DocumentType,
    DocumentVersion,
    ReconciliationJob,
    ReconciliationAttempt,
    SecurityAuditEvent,
)
from backend.app.schemas import (
    DocumentLifecycleResponse,
    DocumentMetadata,
    DocumentSubjectsResponse,
    DocumentSubjectsUpdate,
    DocumentUpdate,
    DocumentVersionHistory,
    StoredDocumentResponse,
)
from backend.app.services.document_service import (
    download_document,
    download_document_version,
    get_document_metadata,
    get_document_versions,
    list_documents,
    replace_document_subjects,
    restore_document,
    soft_delete_document,
    upload_document,
    upload_document_version,
    canonical_school_id,
    _active_subjects,
    _validate_subject_requirement,
)

router = APIRouter(prefix="/documents", tags=["documents"])


def _require_professor(principal: Principal, school_id: str) -> str:
    canonical = canonical_school_id(school_id)
    if not principal.has_school_role(canonical, "professor"):
        raise HTTPException(status_code=403, detail="professor membership required for this school")
    return canonical


def _managed_document(request: Request, principal: Principal, document_id: str) -> Document:
    try:
        parsed = UUID(document_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="document_id must be a valid UUID") from error
    with request.app.state.session_factory() as session:
        document = session.get(Document, parsed)
        if document is None:
            raise HTTPException(status_code=404, detail="document does not exist")
        if principal.administers_school(document.school_id):
            return document
        if (
            document.owner_id == principal.user_id
            and principal.has_school_role(document.school_id, "professor")
        ):
            return document
        raise HTTPException(status_code=403, detail="document management is not permitted")


def _audit_document_action(request: Request, principal: Principal, action: str, document_id: str) -> None:
    with request.app.state.session_factory.begin() as session:
        session.add(
            SecurityAuditEvent(
                id=uuid4(),
                actor_user_id=principal.user_id,
                action=action,
                resource_type="document",
                resource_id=document_id,
                outcome="success",
            )
        )


def _normalize_subject_ids(value: object) -> object:
    """Normalize repeated and comma-separated multipart subject identifiers."""

    if value is None or value == "":
        return []
    if isinstance(value, list):
        normalized: list[object] = []
        for subject_id in value:
            if isinstance(subject_id, str):
                normalized.extend(
                    item.strip() for item in subject_id.split(",") if item.strip()
                )
            else:
                normalized.append(subject_id)
        return normalized
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


DocumentSubjectIdsForm = Annotated[
    list[UUID],
    BeforeValidator(_normalize_subject_ids),
    Form(default_factory=list),
]


@router.get("", response_model=list[DocumentMetadata])
async def search_documents(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    school_id: str | None = Query(None),
    subject_id: UUID | None = Query(None),
    document_type_id: str | None = Query(None),
    document_id: UUID | None = Query(None),
    q: str | None = Query(None, max_length=200),
    offset: int = Query(0, ge=0),
    limit: int | None = Query(None, ge=1, le=100),
) -> list[DocumentMetadata]:
    return list_documents(
        request.app.state.session_factory,
        school_id,
        subject_id,
        document_type_id,
        document_id=document_id,
        search=q,
        offset=offset,
        limit=limit,
    )


@router.post(
    "",
    response_model=StoredDocumentResponse,
    status_code=201,
    responses={
        201: {"description": "Document stored successfully"},
        400: {"description": "Invalid school, document type, or subject selection"},
        422: {
            "description": "Invalid multipart form data",
            "content": {
                "application/json": {
                    "schema": {
                        "$ref": "#/components/schemas/HTTPValidationError",
                    }
                }
            },
        },
        503: {"description": "Document catalog or storage node unavailable"},
    },
)
async def create_document(
    request: Request,
    subject_ids: DocumentSubjectIdsForm,
    file: UploadFile = File(...),
    school_id: str = Form(...),
    title: str | None = Form(None),
    description: str | None = Form(None),
    document_type_id: str | None = Form(None),
    principal: Principal = Depends(get_current_principal),
) -> StoredDocumentResponse:
    client: httpx.AsyncClient = request.app.state.http_client
    settings: CoordinatorSettings = request.app.state.settings
    canonical_school = _require_professor(principal, school_id)
    return await upload_document(
        client,
        settings,
        file,
        canonical_school,
        request.app.state.session_factory,
        title,
        description,
        document_type_id,
        subject_ids,
        principal.user_id,
    )


@router.put(
    "/{document_id}/subjects",
    response_model=DocumentSubjectsResponse,
)
def update_document_subjects(
    request: Request,
    document_id: str,
    payload: DocumentSubjectsUpdate,
    principal: Principal = Depends(get_current_principal),
) -> DocumentSubjectsResponse:
    _managed_document(request, principal, document_id)
    return replace_document_subjects(
        request.app.state.session_factory,
        document_id,
        payload.subject_ids,
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
    principal: Principal = Depends(get_current_principal),
) -> StoredDocumentResponse:
    document = _managed_document(request, principal, document_id)
    if document.owner_id != principal.user_id:
        raise HTTPException(status_code=403, detail="only the owning professor can create a version")
    _require_professor(principal, document.school_id)
    return await upload_document_version(
        request.app.state.http_client,
        request.app.state.settings,
        file,
        document_id,
        request.app.state.session_factory,
        principal.user_id,
    )


@router.get(
    "/{document_id}/versions",
    response_model=DocumentVersionHistory,
)
async def read_document_versions(
    request: Request,
    document_id: str,
    principal: Principal = Depends(get_current_principal),
) -> DocumentVersionHistory:
    history = get_document_versions(
        request.app.state.session_factory,
        document_id,
    )
    try:
        parsed = UUID(document_id)
    except ValueError:
        return history
    with request.app.state.session_factory() as session:
        document = session.get(Document, parsed)
        may_manage = document is not None and (
            document.owner_id == principal.user_id
            or principal.administers_school(document.school_id)
        )
    if may_manage:
        return history
    return DocumentVersionHistory(
        document_id=history.document_id,
        current_version_id=history.current_version_id,
        versions=[item for item in history.versions if item.status == "available"],
    )


@router.get(
    "/{document_id}/versions/{version_number}/download",
    response_class=StreamingResponse,
)
async def read_document_version(
    request: Request,
    document_id: str,
    version_number: int,
    principal: Principal = Depends(get_current_principal),
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
    principal: Principal = Depends(get_current_principal),
) -> DocumentLifecycleResponse:
    _managed_document(request, principal, document_id)
    response = soft_delete_document(
        request.app.state.session_factory,
        request.app.state.settings,
        document_id,
        principal.user_id,
    )
    _audit_document_action(request, principal, "soft_delete_document", document_id)
    return response


@router.post(
    "/{document_id}/restore",
    response_model=DocumentLifecycleResponse,
)
async def restore_deleted_document(
    request: Request,
    document_id: str,
    principal: Principal = Depends(get_current_principal),
) -> DocumentLifecycleResponse:
    _managed_document(request, principal, document_id)
    response = restore_document(
        request.app.state.session_factory,
        request.app.state.settings,
        document_id,
        principal.user_id,
    )
    _audit_document_action(request, principal, "restore_document", document_id)
    return response


@router.patch("/{document_id}", response_model=DocumentMetadata)
async def update_document_metadata(
    request: Request,
    document_id: str,
    payload: DocumentUpdate,
    principal: Principal = Depends(get_current_principal),
) -> DocumentMetadata:
    visible = _managed_document(request, principal, document_id)
    values = payload.model_dump(exclude_unset=True)
    with request.app.state.session_factory.begin() as session:
        document = session.scalar(
            select(Document).where(Document.id == visible.id).with_for_update()
        )
        if document is None or document.deleted_at is not None:
            raise HTTPException(status_code=404, detail="document does not exist")
        if "document_type_id" in values or "subject_ids" in values:
            type_id = values.get("document_type_id", document.document_type_id)
            document_type = session.get(DocumentType, type_id) if type_id else None
            if "document_type_id" in values and type_id and (document_type is None or not document_type.is_active):
                raise HTTPException(status_code=400, detail="document type does not exist")
            subjects = (
                _active_subjects(session, values["subject_ids"], document.school_id)
                if "subject_ids" in values else document.subjects
            )
            _validate_subject_requirement(document_type, subjects)
            if "subject_ids" in values:
                document.subjects = subjects
        for field in ("title", "description", "document_type_id"):
            if field in values:
                setattr(document, field, values[field])
        document.updated_at = datetime.now(timezone.utc)
        session.add(
            SecurityAuditEvent(
                id=uuid4(),
                actor_user_id=principal.user_id,
                action="update_document_metadata",
                resource_type="document",
                resource_id=str(document.id),
                outcome="success",
            )
        )
        school_id = document.school_id
    return await get_document_metadata(
        request.app.state.http_client,
        request.app.state.settings,
        school_id,
        document_id,
        request.app.state.session_factory,
    )


@router.get("/my/uploads")
def my_uploads(
    request: Request,
    response: Response,
    principal: Principal = Depends(get_current_principal),
    deleted: bool | None = Query(None),
    school_id: str | None = Query(None),
    document_type_id: str | None = Query(None),
    document_id: UUID | None = Query(None),
    q: str | None = Query(None, max_length=200),
    offset: int = Query(0, ge=0),
    limit: int | None = Query(None, ge=1, le=100),
) -> list[dict]:
    response.headers["X-Max-Upload-Bytes"] = str(request.app.state.settings.max_upload_size_bytes)
    with request.app.state.session_factory() as session:
        query = select(Document).where(Document.owner_id == principal.user_id)
        if deleted is not None:
            query = query.where(Document.deleted_at.is_not(None) if deleted else Document.deleted_at.is_(None))
        if school_id is not None:
            query = query.where(Document.school_id == school_id)
        if document_type_id is not None:
            query = query.where(Document.document_type_id == document_type_id)
        if document_id is not None:
            query = query.where(Document.id == document_id)
        if q and q.strip():
            query = query.where(Document.title.icontains(q.strip(), autoescape=True))
        documents = session.scalars(query.order_by(Document.created_at.desc(), Document.id).offset(offset).limit(limit)).all()
        results = []
        for document in documents:
            versions = session.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document.id)
                .order_by(DocumentVersion.version_number.desc())
            ).all()
            version_rows = []
            for version in versions:
                job = session.scalar(
                    select(ReconciliationJob).where(ReconciliationJob.version_id == version.id)
                )
                last_attempt = session.scalar(
                    select(ReconciliationAttempt.started_at)
                    .where(ReconciliationAttempt.job_id == job.id)
                    .order_by(ReconciliationAttempt.attempt_number.desc()).limit(1)
                ) if job else None
                version_rows.append(
                    {
                        "version_id": str(version.id),
                        "version_number": version.version_number,
                        "status": version.status,
                        "filename": version.filename,
                        "size_bytes": version.size_bytes,
                        "created_at": version.created_at,
                        "last_attempt_at": last_attempt,
                        "next_attempt_at": job.next_attempt_at if job else None,
                        "reconciliation_status": job.status if job else None,
                        "awaiting_reconciliation": bool(
                            job is None and version.status == "failed"
                            and session.scalar(
                                select(DocumentReplica.id)
                                .where(
                                    DocumentReplica.version_id == version.id,
                                    DocumentReplica.status == "healthy",
                                ).limit(1)
                            )
                        ),
                        "attempts": job.attempts if job else 0,
                        "requires_intervention": bool(job and job.status == "failed"),
                    }
                )
            results.append({
                "document_id": str(document.id),
                "school_id": document.school_id,
                "title": document.title,
                "description": document.description,
                "document_type_id": document.document_type_id,
                "subject_ids": [str(subject.id) for subject in document.subjects],
                "created_at": document.created_at,
                "updated_at": document.updated_at,
                "current_version_id": str(document.current_version_id) if document.current_version_id else None,
                "deleted_at": document.deleted_at,
                "versions": version_rows,
            })
        return results


@router.get("/{school_id}/{document_id}", response_model=DocumentMetadata)
async def read_document_metadata(
    request: Request,
    school_id: str,
    document_id: str,
    principal: Principal = Depends(get_current_principal),
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
    principal: Principal = Depends(get_current_principal),
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
