"""Filesystem-backed immutable document storage for one logical node."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID, uuid4

from fastapi import UploadFile
from pydantic import ValidationError

from backend.app.schemas import DocumentMetadata

CHUNK_SIZE = 1024 * 1024


class StorageError(Exception):
    """Base class for errors that can be mapped to an HTTP response."""


class InvalidDocument(StorageError):
    pass


class DocumentTooLarge(StorageError):
    pass


class DocumentConflict(StorageError):
    pass


class DocumentNotFound(StorageError):
    pass


class DocumentCorrupted(StorageError):
    pass


def canonical_document_id(value: str) -> str:
    """Accept only canonical UUIDs before using an identifier in a path."""

    try:
        parsed = UUID(value)
    except ValueError as error:
        raise InvalidDocument("document_id must be a valid UUID") from error
    canonical = str(parsed)
    if value != canonical:
        raise InvalidDocument("document_id must use canonical UUID format")
    return canonical


class DocumentStore:
    """Store immutable documents and JSON metadata below a node data root."""

    def __init__(self, data_root: Path, max_upload_size_bytes: int) -> None:
        self.data_root = data_root
        self.max_upload_size_bytes = max_upload_size_bytes
        self.documents_dir = data_root / "documents"
        self.metadata_dir = data_root / "metadata"
        self.temporary_dir = data_root / "temporary"
        self.claims_dir = data_root / "claims"

    def _ensure_directories(self) -> None:
        for directory in (
            self.documents_dir,
            self.metadata_dir,
            self.temporary_dir,
            self.claims_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def _paths(self, document_id: str) -> tuple[Path, Path, Path]:
        canonical = canonical_document_id(document_id)
        return (
            self.documents_dir / f"{canonical}.bin",
            self.metadata_dir / f"{canonical}.json",
            self.claims_dir / f"{canonical}.lock",
        )

    async def store(
        self,
        upload: UploadFile,
        metadata: DocumentMetadata,
    ) -> DocumentMetadata:
        """Stream, validate, and publish a document without overwriting data."""

        self._ensure_directories()
        document_id = str(metadata.document_id)
        document_path, metadata_path, claim_path = self._paths(document_id)

        try:
            claim_fd = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(claim_fd)
        except FileExistsError as error:
            raise DocumentConflict("document_id is currently being stored") from error

        document_temp: Path | None = None
        metadata_temp: Path | None = None
        document_published = False
        try:
            if document_path.exists() or metadata_path.exists():
                raise DocumentConflict("document_id already exists")

            temp_fd, temp_name = tempfile.mkstemp(
                prefix=f"{document_id}.",
                suffix=".part",
                dir=self.temporary_dir,
            )
            document_temp = Path(temp_name)
            digest = hashlib.sha256()
            size_bytes = 0
            with os.fdopen(temp_fd, "wb") as target:
                while chunk := await upload.read(CHUNK_SIZE):
                    size_bytes += len(chunk)
                    if size_bytes > self.max_upload_size_bytes:
                        raise DocumentTooLarge("document exceeds configured size limit")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())

            if size_bytes == 0:
                raise InvalidDocument("document must not be empty")
            if size_bytes != metadata.size_bytes:
                raise InvalidDocument("document size does not match metadata")
            if digest.hexdigest() != metadata.sha256:
                raise InvalidDocument("document checksum does not match metadata")

            try:
                os.link(document_temp, document_path)
            except FileExistsError as error:
                raise DocumentConflict("document_id already exists") from error
            document_published = True
            document_temp.unlink(missing_ok=True)
            document_temp = None

            metadata_fd, metadata_name = tempfile.mkstemp(
                prefix=f"{document_id}.",
                suffix=".json.part",
                dir=self.temporary_dir,
            )
            metadata_temp = Path(metadata_name)
            metadata_payload = metadata.model_dump_json().encode("utf-8")
            with os.fdopen(metadata_fd, "wb") as target:
                target.write(metadata_payload)
                target.flush()
                os.fsync(target.fileno())
            try:
                os.link(metadata_temp, metadata_path)
            except FileExistsError as error:
                raise DocumentConflict("document metadata already exists") from error
            metadata_temp.unlink(missing_ok=True)
            metadata_temp = None
            return metadata
        except Exception:
            if document_temp is not None:
                document_temp.unlink(missing_ok=True)
            if metadata_temp is not None:
                metadata_temp.unlink(missing_ok=True)
            if document_published and not metadata_path.exists():
                document_path.unlink(missing_ok=True)
            raise
        finally:
            claim_path.unlink(missing_ok=True)

    async def load_metadata(self, document_id: str) -> DocumentMetadata:
        document_path, metadata_path, _ = self._paths(document_id)
        if not document_path.is_file() or not metadata_path.is_file():
            raise DocumentNotFound("document does not exist")
        try:
            raw_metadata = metadata_path.read_text("utf-8")
            return DocumentMetadata.model_validate(json.loads(raw_metadata))
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise DocumentCorrupted("stored metadata is invalid") from error

    async def verified_document(
        self,
        document_id: str,
    ) -> tuple[Path, DocumentMetadata]:
        """Return a file only after checking size and SHA-256 against metadata."""

        metadata = await self.load_metadata(document_id)
        document_path, _, _ = self._paths(document_id)

        def calculate_integrity() -> tuple[int, str]:
            size = 0
            digest = hashlib.sha256()
            with document_path.open("rb") as source:
                while chunk := source.read(CHUNK_SIZE):
                    size += len(chunk)
                    digest.update(chunk)
            return size, digest.hexdigest()

        try:
            size_bytes, checksum = calculate_integrity()
        except OSError as error:
            raise DocumentNotFound("document content does not exist") from error
        if size_bytes != metadata.size_bytes or checksum != metadata.sha256:
            raise DocumentCorrupted("stored document failed integrity verification")
        return document_path, metadata
