"""Subject management endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.app.auth import Principal, get_current_principal
from backend.app.models import Subject
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
def register_subject(
    request: Request,
    payload: SubjectCreate,
    principal: Principal = Depends(get_current_principal),
) -> SubjectResponse:
    if not principal.administers_school(payload.school_id):
        raise HTTPException(status_code=403, detail="school administration required")
    return create_subject(request.app.state.session_factory, payload)


@router.get("", response_model=list[SubjectResponse])
def read_subjects(
    request: Request,
    school_id: str | None = Query(None),
    principal: Principal = Depends(get_current_principal),
) -> list[SubjectResponse]:
    return list_subjects(request.app.state.session_factory, school_id)


@router.get("/{subject_id}", response_model=SubjectResponse)
def read_subject(
    request: Request,
    subject_id: str,
    principal: Principal = Depends(get_current_principal),
) -> SubjectResponse:
    return get_subject(request.app.state.session_factory, subject_id)


@router.patch("/{subject_id}", response_model=SubjectResponse)
def modify_subject(
    request: Request,
    subject_id: str,
    payload: SubjectUpdate,
    principal: Principal = Depends(get_current_principal),
) -> SubjectResponse:
    with request.app.state.session_factory() as session:
        try:
            parsed_subject_id = UUID(subject_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="subject_id must be a valid UUID") from error
        subject = session.get(Subject, parsed_subject_id)
        if subject is None:
            raise HTTPException(status_code=404, detail="subject does not exist")
        if not principal.administers_school(subject.school_id):
            raise HTTPException(status_code=403, detail="school administration required")
    return update_subject(request.app.state.session_factory, subject_id, payload)


@router.get(
    "/{subject_id}/documents",
    response_model=list[SubjectDocumentSummary],
)
def read_subject_documents(
    request: Request,
    subject_id: str,
    principal: Principal = Depends(get_current_principal),
) -> list[SubjectDocumentSummary]:
    return get_subject_documents(request.app.state.session_factory, subject_id)
