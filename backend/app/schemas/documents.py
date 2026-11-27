"""Document contracts shared by the coordinator and storage services."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.schemas.subjects import SubjectSummary


class DocumentMetadata(BaseModel):
    """Persistent metadata for one immutable document."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    school_id: str
    school: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    node_id: str
    title: str | None = None
    description: str | None = None
    document_type_id: str | None = None
    document_type_name: str | None = None
    subjects: list[SubjectSummary] = Field(default_factory=list)
    version_id: UUID | None = None
    version_number: int | None = None
    version_status: Literal["pending", "available", "failed"] | None = None
    target_replicas: int | None = None
    owner_id: UUID | None = None
    metadata_revision: int | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        return value.astimezone(timezone.utc)


class StoredDocumentResponse(DocumentMetadata):
    status: Literal["stored"] = "stored"


class DocumentVersionSummary(BaseModel):
    """One immutable version in a document history."""

    version_id: UUID
    version_number: int = Field(gt=0)
    filename: str
    mime_type: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    status: Literal["pending", "available", "failed"]
    is_current: bool


class DocumentVersionHistory(BaseModel):
    document_id: UUID
    current_version_id: UUID | None
    versions: list[DocumentVersionSummary]


class DocumentLifecycleResponse(BaseModel):
    document_id: UUID
    status: Literal["deleted", "already_deleted", "restored", "already_active"]
    deleted_at: datetime | None


class DocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metadata_revision: int = Field(ge=1)

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    document_type_id: str | None = Field(default=None, max_length=30)
    subject_ids: list[UUID] = Field(default_factory=list)
