"""Request and response contracts for academic subjects."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SubjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    school_id: str = Field(min_length=1, max_length=30)
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=150)
    description: str | None = None


class SubjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=30)
    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = None
    is_active: bool | None = None


class SubjectSummary(BaseModel):
    id: UUID
    school_id: str
    code: str
    name: str
    is_active: bool


class SubjectResponse(SubjectSummary):
    description: str | None
    created_at: datetime


class SubjectDocumentSummary(BaseModel):
    document_id: UUID
    title: str
    description: str | None
    school_id: str
    document_type_id: str | None
    created_at: datetime
    current_version_id: UUID | None


class DocumentSubjectsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_ids: list[UUID]


class DocumentSubjectsResponse(BaseModel):
    document_id: UUID
    subjects: list[SubjectSummary]
