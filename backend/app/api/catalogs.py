"""Authenticated read access to frontend academic catalogs."""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.auth import Principal, get_current_principal, require_super_admin
from backend.app.models import DocumentType, School, SecurityAuditEvent


router = APIRouter(prefix="/catalogs", tags=["catalogs"])


class DocumentTypeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=30)
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=100)
    requires_subject: bool = False


class DocumentTypeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    requires_subject: bool | None = None
    is_active: bool | None = None


@router.get("/schools")
def schools(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> list[dict]:
    with request.app.state.session_factory() as session:
        rows = session.scalars(select(School).order_by(School.name)).all()
        return [
            {"id": item.id, "code": item.code, "name": item.name, "is_active": item.is_active}
            for item in rows
        ]


@router.get("/document-types")
def document_types(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> list[dict]:
    with request.app.state.session_factory() as session:
        rows = session.scalars(select(DocumentType).order_by(DocumentType.name)).all()
        return [
            {
                "id": item.id,
                "code": item.code,
                "name": item.name,
                "requires_subject": item.requires_subject,
                "is_active": item.is_active,
            }
            for item in rows
        ]


@router.post("/document-types", status_code=201)
def create_document_type(
    request: Request,
    payload: DocumentTypeCreate,
    principal: Principal = Depends(require_super_admin),
) -> dict:
    try:
        with request.app.state.session_factory.begin() as session:
            item = DocumentType(
                id=payload.id,
                code=payload.code,
                name=payload.name,
                requires_subject=payload.requires_subject,
                is_active=True,
            )
            session.add(item)
            session.add(
                SecurityAuditEvent(
                    id=uuid4(), actor_user_id=principal.user_id,
                    action="create_document_type", resource_type="document_type",
                    resource_id=item.id, outcome="success",
                )
            )
            session.flush()
            return {"id": item.id, "code": item.code, "name": item.name, "requires_subject": item.requires_subject, "is_active": item.is_active}
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="document type already exists") from error


@router.patch("/document-types/{document_type_id}")
def update_document_type(
    request: Request,
    document_type_id: str,
    payload: DocumentTypeUpdate,
    principal: Principal = Depends(require_super_admin),
) -> dict:
    with request.app.state.session_factory.begin() as session:
        item = session.get(DocumentType, document_type_id)
        if item is None:
            raise HTTPException(status_code=404, detail="document type does not exist")
        for field, value in payload.model_dump(exclude_unset=True, exclude_none=True).items():
            setattr(item, field, value)
        session.add(
            SecurityAuditEvent(
                id=uuid4(), actor_user_id=principal.user_id,
                action="update_document_type", resource_type="document_type",
                resource_id=item.id, outcome="success",
            )
        )
        try:
            session.flush()
        except IntegrityError as error:
            raise HTTPException(status_code=409, detail="document type already exists") from error
        return {"id": item.id, "code": item.code, "name": item.name, "requires_subject": item.requires_subject, "is_active": item.is_active}
