"""Document contracts shared by the coordinator and storage services."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        return value.astimezone(timezone.utc)


class StoredDocumentResponse(DocumentMetadata):
    status: Literal["stored"] = "stored"
