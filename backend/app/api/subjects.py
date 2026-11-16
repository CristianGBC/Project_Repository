"""Subject management endpoints."""

from fastapi import APIRouter, Query, Request

from backend.app.schemas import (
    SubjectCreate,
    SubjectDocumentSummary,
    SubjectResponse,
    SubjectUpdate,
)
from backend.app.services.subject_service import (
    create_subject,
    get_subject,
    get_subject_documents,
    list_subjects,
    update_subject,
)

router = APIRouter(prefix="/subjects", tags=["subjects"])


@router.post("", response_model=SubjectResponse, status_code=201)
def register_subject(request: Request, payload: SubjectCreate) -> SubjectResponse:
    return create_subject(request.app.state.session_factory, payload)


@router.get("", response_model=list[SubjectResponse])
def read_subjects(
    request: Request,
    school_id: str | None = Query(None),
) -> list[SubjectResponse]:
    return list_subjects(request.app.state.session_factory, school_id)


@router.get("/{subject_id}", response_model=SubjectResponse)
def read_subject(request: Request, subject_id: str) -> SubjectResponse:
    return get_subject(request.app.state.session_factory, subject_id)


@router.patch("/{subject_id}", response_model=SubjectResponse)
def modify_subject(
    request: Request,
    subject_id: str,
    payload: SubjectUpdate,
) -> SubjectResponse:
    return update_subject(request.app.state.session_factory, subject_id, payload)


@router.get(
    "/{subject_id}/documents",
    response_model=list[SubjectDocumentSummary],
)
def read_subject_documents(
    request: Request,
    subject_id: str,
) -> list[SubjectDocumentSummary]:
    return get_subject_documents(request.app.state.session_factory, subject_id)
