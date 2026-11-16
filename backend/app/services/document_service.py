"""Document operations coordinated between PostgreSQL and storage nodes.

Database transactions deliberately end before any HTTP transfer. A pending
replica is therefore also the durable reconciliation record for an interrupted
operation.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import tempfile
from typing import AsyncIterator, BinaryIO
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from fastapi.responses import StreamingResponse
import httpx
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import CoordinatorSettings, NodeSettings
from backend.app.models import (
    Document,
    DocumentReplica,
    DocumentVersion,
    School,
    StorageNode,
    User,
)
from backend.app.schemas import (
    DocumentLifecycleResponse,
    DocumentMetadata,
    DocumentVersionHistory,
    DocumentVersionSummary,
    StoredDocumentResponse,
)

CHUNK_SIZE = 1024 * 1024
TARGET_REPLICAS = 1

SCHOOL_TO_NODE = {
    "school-bio": "node-bio",
    "school-phy": "node-phy",
    "school-math": "node-math",
    "school-chem": "node-chem",
    "school-earth": "node-earth",
    "school-agro": "node-agro",
}
NODE_TO_SCHOOL = {node_id: school_id for school_id, node_id in SCHOOL_TO_NODE.items()}


@dataclass(frozen=True)
class CatalogRecord:
    document: Document
    version: DocumentVersion
    school: School
    replica: DocumentReplica | None


def configured_node(settings: CoordinatorSettings, node_id: str) -> NodeSettings:
    for node in settings.nodes:
        if node.node_id == node_id:
            return node
    raise HTTPException(status_code=400, detail="unknown storage node")


def canonical_school_id(value: str) -> str:
    """Accept Stage 3 node identifiers and Stage 4 school identifiers."""
    if value in SCHOOL_TO_NODE:
        return value
    if value in NODE_TO_SCHOOL:
        return NODE_TO_SCHOOL[value]
    raise HTTPException(status_code=400, detail="unknown school_id")


async def _raise_remote_error(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        detail = response.json().get("detail", "storage request failed")
    except ValueError:
        detail = "storage request failed"
    status_code = response.status_code if response.status_code in {400, 404, 409, 413} else 502
    raise HTTPException(status_code=status_code, detail=detail)


def _service_unavailable(error: httpx.RequestError) -> HTTPException:
    return HTTPException(status_code=503, detail="storage node is unavailable")


def _database_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="document catalog is unavailable")


async def _measure_upload(upload: UploadFile, max_size_bytes: int) -> tuple[int, str, BinaryIO]:
    size_bytes = 0
    digest = hashlib.sha256()
    prepared = tempfile.SpooledTemporaryFile(
        max_size=min(max_size_bytes, CHUNK_SIZE), mode="w+b"
    )
    try:
        while chunk := await upload.read(CHUNK_SIZE):
            size_bytes += len(chunk)
            if size_bytes > max_size_bytes:
                raise HTTPException(413, "document exceeds configured size limit")
            digest.update(chunk)
            prepared.write(chunk)
        if size_bytes == 0:
            raise HTTPException(400, "document must not be empty")
        prepared.seek(0)
        return size_bytes, digest.hexdigest(), prepared
    except Exception:
        prepared.close()
        raise


def _validate_filename(filename: str | None) -> str:
    if not filename or "\x00" in filename or len(filename) > 255:
        raise HTTPException(400, "invalid filename")
    return filename


def _technical_user(session: Session, settings: CoordinatorSettings) -> User:
    user = session.get(User, settings.technical_user_id)
    if user is None:
        user = User(
            id=settings.technical_user_id,
            full_name="YachayRepo Development Operations",
            email=settings.technical_user_email,
            password_hash=settings.technical_user_password_hash,
            is_admin=False,
            is_active=True,
        )
        session.add(user)
    return user


def _select_storage_node(
    session: Session, settings: CoordinatorSettings, school_id: str
) -> StorageNode:
    configured_ids = {node.node_id for node in settings.nodes}
    candidates = session.scalars(
        select(StorageNode).where(
            StorageNode.id.in_(configured_ids),
            StorageNode.is_active.is_(True),
            StorageNode.health != "down",
        )
    ).all()
    candidates.sort(key=lambda node: (node.home_school_id != school_id, node.id))
    if not candidates:
        raise HTTPException(503, "no storage node is available")
    return candidates[0]


def _create_pending_catalog_records(
    session_factory: sessionmaker[Session],
    settings: CoordinatorSettings,
    school_id: str,
    filename: str,
    mime_type: str,
    size_bytes: int,
    checksum: str,
    title: str | None,
    description: str | None,
) -> tuple[Document, DocumentVersion, DocumentReplica, School]:
    try:
        with session_factory.begin() as session:
            school = session.get(School, school_id)
            if school is None or not school.is_active:
                raise HTTPException(400, "unknown or inactive school_id")
            user = _technical_user(session, settings)
            session.flush()
            node = _select_storage_node(session, settings, school_id)
            document = Document(
                id=uuid4(), title=(title or Path(filename).stem)[:255],
                description=description, school_id=school.id, created_by=user.id,
                visibility="school",
            )
            version = DocumentVersion(
                id=uuid4(), document_id=document.id, version_number=1,
                filename=filename, mime_type=mime_type, size_bytes=size_bytes,
                sha256=checksum, status="pending", target_replicas=TARGET_REPLICAS,
                created_by=user.id,
            )
            replica = DocumentReplica(
                id=uuid4(), version_id=version.id, node_id=node.id,
                physical_host_id=node.physical_host_id, status="pending", attempts=0,
            )
            # The schema intentionally has a deferred circular reference from
            # documents back to its current version. Explicit phase flushes
            # make the initial forward FK ordering unambiguous to SQLAlchemy.
            session.add(document)
            session.flush()
            session.add(version)
            session.flush()
            session.add(replica)
            session.flush()
            return document, version, replica, school
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise _database_unavailable() from error


def _record_failed_transfer(
    session_factory: sessionmaker[Session],
    version_id: UUID,
    replica_id: UUID,
    message: str,
) -> None:
    try:
        with session_factory.begin() as session:
            version = session.get(DocumentVersion, version_id)
            replica = session.get(DocumentReplica, replica_id)
            if version is not None and version.status == "pending":
                version.status = "failed"
            if replica is not None:
                replica.status = "failed"
                replica.attempts += 1
                replica.last_error = message[:1000]
    except SQLAlchemyError:
        # A pending row is itself a durable reconciliation signal.
        return


def _confirm_stored_replica(
    session_factory: sessionmaker[Session],
    document_id: UUID,
    version_id: UUID,
    replica_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    try:
        with session_factory.begin() as session:
            document = session.scalar(
                select(Document)
                .where(Document.id == document_id)
                .with_for_update()
            )
            version = session.get(DocumentVersion, version_id)
            replica = session.get(DocumentReplica, replica_id)
            if document is None or version is None or replica is None:
                raise RuntimeError("pending catalog operation disappeared")
            replica.status = "healthy"
            replica.attempts += 1
            replica.stored_at = now
            replica.verified_at = now
            replica.last_error = None
            version.status = "available"
            version.committed_at = now
            current_number = 0
            if document.current_version_id is not None:
                current = session.get(DocumentVersion, document.current_version_id)
                if current is not None:
                    current_number = current.version_number
            if version.version_number > current_number:
                document.current_version_id = version.id
            document.updated_at = now
    except (SQLAlchemyError, RuntimeError) as error:
        raise HTTPException(
            503,
            detail={
                "message": "file stored but catalog confirmation is pending",
                "document_id": str(document_id),
            },
        ) from error


def _storage_metadata(
    document_id: UUID,
    filename: str,
    node: NodeSettings,
    size_bytes: int,
    checksum: str,
    created_at: datetime,
) -> DocumentMetadata:
    return DocumentMetadata(
        document_id=document_id, filename=filename, school_id=node.node_id,
        school=node.school, size_bytes=size_bytes, sha256=checksum,
        created_at=created_at, node_id=node.node_id,
    )


async def _store_on_node(
    client: httpx.AsyncClient,
    upload: UploadFile,
    prepared_file: BinaryIO,
    metadata: DocumentMetadata,
    node: NodeSettings,
) -> StoredDocumentResponse:
    try:
        response = await client.put(
            f"{node.url}/files/{metadata.document_id}",
            data={
                "filename": metadata.filename,
                "school_id": node.node_id,
                "school": node.school,
                "size_bytes": str(metadata.size_bytes),
                "sha256": metadata.sha256,
                "created_at": metadata.created_at.isoformat(),
            },
            files={
                "file": (
                    "document.bin", prepared_file,
                    upload.content_type or "application/octet-stream",
                )
            },
        )
    except httpx.RequestError as error:
        raise _service_unavailable(error) from error
    await _raise_remote_error(response)
    try:
        stored = StoredDocumentResponse.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise HTTPException(502, "invalid storage response") from error
    expected = StoredDocumentResponse(**metadata.model_dump(), status="stored")
    if stored != expected:
        raise HTTPException(502, "storage confirmation does not match request")
    return stored


async def _legacy_upload_document(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    upload: UploadFile,
    school_id: str,
) -> StoredDocumentResponse:
    node = configured_node(settings, SCHOOL_TO_NODE.get(school_id, school_id))
    filename = _validate_filename(upload.filename)
    size_bytes, checksum, prepared_file = await _measure_upload(
        upload, settings.max_upload_size_bytes
    )
    metadata = _storage_metadata(
        uuid4(), filename, node, size_bytes, checksum, datetime.now(timezone.utc)
    )
    try:
        return await _store_on_node(client, upload, prepared_file, metadata, node)
    finally:
        prepared_file.close()


async def upload_document(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    upload: UploadFile,
    school_id: str,
    session_factory: sessionmaker[Session] | None = None,
    title: str | None = None,
    description: str | None = None,
) -> StoredDocumentResponse:
    if session_factory is None:
        return await _legacy_upload_document(client, settings, upload, school_id)

    canonical_school = canonical_school_id(school_id)
    filename = _validate_filename(upload.filename)
    size_bytes, checksum, prepared_file = await _measure_upload(
        upload, settings.max_upload_size_bytes
    )
    document, version, replica, school = _create_pending_catalog_records(
        session_factory, settings, canonical_school, filename,
        upload.content_type or "application/octet-stream", size_bytes, checksum,
        title, description,
    )
    node = configured_node(settings, replica.node_id)
    created_at = document.created_at or datetime.now(timezone.utc)
    metadata = _storage_metadata(
        document.id, filename, node, size_bytes, checksum, created_at
    )
    try:
        try:
            await _store_on_node(client, upload, prepared_file, metadata, node)
        except HTTPException as error:
            _record_failed_transfer(
                session_factory, version.id, replica.id, str(error.detail)
            )
            raise
    finally:
        prepared_file.close()

    _confirm_stored_replica(session_factory, document.id, version.id, replica.id)
    return StoredDocumentResponse(
        document_id=document.id, filename=filename, school_id=school.id,
        school=school.name, size_bytes=size_bytes, sha256=checksum,
        created_at=created_at, node_id=node.node_id, status="stored",
        title=document.title, description=document.description,
        version_id=version.id, version_number=1, version_status="available",
        target_replicas=TARGET_REPLICAS,
    )


def _document_uuid(document_id: str) -> UUID:
    try:
        return UUID(document_id)
    except ValueError as error:
        raise HTTPException(400, "document_id must be a valid UUID") from error


def _create_pending_version(
    session_factory: sessionmaker[Session],
    settings: CoordinatorSettings,
    document_id: str,
    filename: str,
    mime_type: str,
    size_bytes: int,
    checksum: str,
) -> tuple[Document, DocumentVersion, DocumentReplica, School]:
    parsed_id = _document_uuid(document_id)
    try:
        with session_factory.begin() as session:
            document = session.scalar(
                select(Document)
                .where(Document.id == parsed_id)
                .with_for_update()
            )
            if document is None or document.deleted_at is not None:
                raise HTTPException(404, "document does not exist")
            school = session.get(School, document.school_id)
            if school is None or not school.is_active:
                raise HTTPException(409, "document school is unavailable")
            user = _technical_user(session, settings)
            session.flush()
            node = _select_storage_node(session, settings, document.school_id)
            latest_number = session.scalar(
                select(func.max(DocumentVersion.version_number)).where(
                    DocumentVersion.document_id == document.id
                )
            ) or 0
            version = DocumentVersion(
                id=uuid4(),
                document_id=document.id,
                version_number=latest_number + 1,
                filename=filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
                sha256=checksum,
                status="pending",
                target_replicas=TARGET_REPLICAS,
                created_by=user.id,
            )
            session.add(version)
            session.flush()
            replica = DocumentReplica(
                id=uuid4(),
                version_id=version.id,
                node_id=node.id,
                physical_host_id=node.physical_host_id,
                status="pending",
                attempts=0,
            )
            session.add(replica)
            session.flush()
            return document, version, replica, school
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise _database_unavailable() from error


async def upload_document_version(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    upload: UploadFile,
    document_id: str,
    session_factory: sessionmaker[Session] | None,
) -> StoredDocumentResponse:
    if session_factory is None:
        raise _database_unavailable()
    filename = _validate_filename(upload.filename)
    size_bytes, checksum, prepared_file = await _measure_upload(
        upload, settings.max_upload_size_bytes
    )
    document, version, replica, school = _create_pending_version(
        session_factory,
        settings,
        document_id,
        filename,
        upload.content_type or "application/octet-stream",
        size_bytes,
        checksum,
    )
    node = configured_node(settings, replica.node_id)
    created_at = version.created_at or datetime.now(timezone.utc)
    # Version 1 historically uses document.id on disk. Every later immutable
    # version uses version.id, so no physical file can be overwritten.
    storage_metadata = _storage_metadata(
        version.id, filename, node, size_bytes, checksum, created_at
    )
    try:
        try:
            await _store_on_node(
                client, upload, prepared_file, storage_metadata, node
            )
        except HTTPException as error:
            _record_failed_transfer(
                session_factory, version.id, replica.id, str(error.detail)
            )
            raise
    finally:
        prepared_file.close()

    _confirm_stored_replica(session_factory, document.id, version.id, replica.id)
    return StoredDocumentResponse(
        document_id=document.id,
        filename=filename,
        school_id=school.id,
        school=school.name,
        size_bytes=size_bytes,
        sha256=checksum,
        created_at=created_at,
        node_id=node.node_id,
        status="stored",
        title=document.title,
        description=document.description,
        version_id=version.id,
        version_number=version.version_number,
        version_status="available",
        target_replicas=TARGET_REPLICAS,
    )


def get_document_versions(
    session_factory: sessionmaker[Session] | None,
    document_id: str,
) -> DocumentVersionHistory:
    if session_factory is None:
        raise _database_unavailable()
    parsed_id = _document_uuid(document_id)
    try:
        with session_factory() as session:
            document = session.get(Document, parsed_id)
            if document is None or document.deleted_at is not None:
                raise HTTPException(404, "document does not exist")
            versions = session.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == parsed_id)
                .order_by(DocumentVersion.version_number.desc())
            ).all()
            return DocumentVersionHistory(
                document_id=document.id,
                current_version_id=document.current_version_id,
                versions=[
                    DocumentVersionSummary(
                        version_id=version.id,
                        version_number=version.version_number,
                        filename=version.filename,
                        mime_type=version.mime_type,
                        size_bytes=version.size_bytes,
                        sha256=version.sha256,
                        created_at=version.created_at,
                        status=version.status,
                        is_current=version.id == document.current_version_id,
                    )
                    for version in versions
                ],
            )
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise _database_unavailable() from error


def _specific_version_record(
    session_factory: sessionmaker[Session],
    document_id: str,
    version_number: int,
) -> CatalogRecord:
    if version_number < 1:
        raise HTTPException(404, "document version does not exist")
    parsed_id = _document_uuid(document_id)
    try:
        with session_factory() as session:
            document = session.get(Document, parsed_id)
            if document is None or document.deleted_at is not None:
                raise HTTPException(404, "document does not exist")
            version = session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == parsed_id,
                    DocumentVersion.version_number == version_number,
                )
            )
            if version is None:
                raise HTTPException(404, "document version does not exist")
            school = session.get(School, document.school_id)
            replica = session.scalar(
                select(DocumentReplica).where(
                    DocumentReplica.version_id == version.id,
                    DocumentReplica.status == "healthy",
                )
            )
            if school is None:
                raise HTTPException(409, "document catalog is inconsistent")
            for value in (document, version, school, replica):
                if value is not None:
                    session.expunge(value)
            return CatalogRecord(document, version, school, replica)
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise _database_unavailable() from error


async def download_document_version(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    document_id: str,
    version_number: int,
    session_factory: sessionmaker[Session] | None,
) -> StreamingResponse:
    if session_factory is None:
        raise _database_unavailable()
    record = _specific_version_record(
        session_factory, document_id, version_number
    )
    if record.version.status != "available" or record.replica is None:
        raise HTTPException(409, "document version has no healthy replica")
    metadata = _metadata_from_catalog(record)
    node = configured_node(settings, record.replica.node_id)
    storage_id = (
        record.document.id
        if record.version.version_number == 1
        else record.version.id
    )
    return await _verified_download(
        client, settings, node, str(storage_id), metadata
    )


def soft_delete_document(
    session_factory: sessionmaker[Session] | None,
    settings: CoordinatorSettings,
    document_id: str,
) -> DocumentLifecycleResponse:
    if session_factory is None:
        raise _database_unavailable()
    parsed_id = _document_uuid(document_id)
    try:
        with session_factory.begin() as session:
            document = session.scalar(
                select(Document)
                .where(Document.id == parsed_id)
                .with_for_update()
            )
            if document is None:
                raise HTTPException(404, "document does not exist")
            user = _technical_user(session, settings)
            session.flush()
            if document.deleted_at is not None:
                return DocumentLifecycleResponse(
                    document_id=document.id,
                    status="already_deleted",
                    deleted_at=document.deleted_at,
                )
            document.deleted_at = datetime.now(timezone.utc)
            document.deleted_by = user.id
            document.updated_at = document.deleted_at
            return DocumentLifecycleResponse(
                document_id=document.id,
                status="deleted",
                deleted_at=document.deleted_at,
            )
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise _database_unavailable() from error


def restore_document(
    session_factory: sessionmaker[Session] | None,
    settings: CoordinatorSettings,
    document_id: str,
) -> DocumentLifecycleResponse:
    if session_factory is None:
        raise _database_unavailable()
    parsed_id = _document_uuid(document_id)
    try:
        with session_factory.begin() as session:
            document = session.scalar(
                select(Document)
                .where(Document.id == parsed_id)
                .with_for_update()
            )
            if document is None:
                raise HTTPException(404, "document does not exist")
            _technical_user(session, settings)
            session.flush()
            if document.deleted_at is None:
                return DocumentLifecycleResponse(
                    document_id=document.id,
                    status="already_active",
                    deleted_at=None,
                )
            document.deleted_at = None
            document.deleted_by = None
            document.updated_at = datetime.now(timezone.utc)
            return DocumentLifecycleResponse(
                document_id=document.id,
                status="restored",
                deleted_at=None,
            )
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise _database_unavailable() from error


def _catalog_record(
    session_factory: sessionmaker[Session], school_id: str, document_id: str
) -> CatalogRecord | None:
    try:
        parsed_id = UUID(document_id)
    except ValueError as error:
        raise HTTPException(400, "document_id must be a valid UUID") from error
    try:
        with session_factory() as session:
            document = session.get(Document, parsed_id)
            if document is None:
                return None
            if document.deleted_at is not None or document.school_id != school_id:
                raise HTTPException(404, "document does not exist")
            if document.current_version_id is None:
                raise HTTPException(409, "document upload is incomplete")
            version = session.get(DocumentVersion, document.current_version_id)
            school = session.get(School, document.school_id)
            if version is None or school is None:
                raise HTTPException(409, "document catalog is inconsistent")
            replica = session.scalar(
                select(DocumentReplica).where(
                    DocumentReplica.version_id == version.id,
                    DocumentReplica.status == "healthy",
                )
            )
            for value in (document, version, school, replica):
                if value is not None:
                    session.expunge(value)
            return CatalogRecord(document, version, school, replica)
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise _database_unavailable() from error


def _metadata_from_catalog(record: CatalogRecord) -> DocumentMetadata:
    version = record.version
    return DocumentMetadata(
        document_id=record.document.id, filename=version.filename,
        school_id=record.school.id, school=record.school.name,
        size_bytes=version.size_bytes, sha256=version.sha256,
        created_at=record.document.created_at,
        node_id=record.replica.node_id if record.replica else "unavailable",
        title=record.document.title, description=record.document.description,
        version_id=version.id, version_number=version.version_number,
        version_status=version.status, target_replicas=version.target_replicas,
    )


async def _legacy_get_document_metadata(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    school_id: str,
    document_id: str,
) -> DocumentMetadata:
    node = configured_node(settings, SCHOOL_TO_NODE.get(school_id, school_id))
    try:
        response = await client.get(f"{node.url}/files/{document_id}/metadata")
    except httpx.RequestError as error:
        raise _service_unavailable(error) from error
    await _raise_remote_error(response)
    try:
        metadata = DocumentMetadata.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise HTTPException(502, "invalid storage response") from error
    if str(metadata.document_id) != document_id or metadata.node_id != node.node_id:
        raise HTTPException(502, "storage metadata identity mismatch")
    return metadata


async def get_document_metadata(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    school_id: str,
    document_id: str,
    session_factory: sessionmaker[Session] | None = None,
) -> DocumentMetadata:
    if session_factory is None:
        return await _legacy_get_document_metadata(client, settings, school_id, document_id)
    canonical_school = canonical_school_id(school_id)
    record = _catalog_record(session_factory, canonical_school, document_id)
    if record is not None:
        return _metadata_from_catalog(record)
    # Only a genuine catalog miss falls back to Stage 3 JSON. Database failures
    # and soft-deleted rows never do.
    return await _legacy_get_document_metadata(client, settings, school_id, document_id)


def _download_headers(filename: str) -> dict[str, str]:
    ascii_name = "".join(
        character for character in filename
        if 32 <= ord(character) < 127 and character not in {'"', "\\"}
    ) or "document"
    encoded_name = quote(filename, safe="")
    return {
        "Content-Disposition":
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"
    }


async def _stream_and_close(source: BinaryIO) -> AsyncIterator[bytes]:
    try:
        while chunk := source.read(CHUNK_SIZE):
            yield chunk
    finally:
        source.close()


async def _verified_download(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    node: NodeSettings,
    document_id: str,
    metadata: DocumentMetadata,
) -> StreamingResponse:
    request = client.build_request("GET", f"{node.url}/files/{document_id}")
    try:
        response = await client.send(request, stream=True)
    except httpx.RequestError as error:
        raise _service_unavailable(error) from error
    temporary = tempfile.SpooledTemporaryFile(
        max_size=min(settings.max_upload_size_bytes, CHUNK_SIZE), mode="w+b"
    )
    size_bytes = 0
    digest = hashlib.sha256()
    try:
        if not response.is_success:
            await response.aread()
            await _raise_remote_error(response)
        async for chunk in response.aiter_bytes(CHUNK_SIZE):
            size_bytes += len(chunk)
            if size_bytes > settings.max_upload_size_bytes:
                raise HTTPException(502, "storage returned oversized content")
            digest.update(chunk)
            temporary.write(chunk)
    except httpx.RequestError as error:
        temporary.close()
        raise _service_unavailable(error) from error
    except Exception:
        temporary.close()
        raise
    finally:
        await response.aclose()
    if size_bytes != metadata.size_bytes or digest.hexdigest() != metadata.sha256:
        temporary.close()
        raise HTTPException(409, "document integrity verification failed")
    temporary.seek(0)
    return StreamingResponse(
        _stream_and_close(temporary), media_type="application/octet-stream",
        headers=_download_headers(metadata.filename),
    )


async def download_document(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    school_id: str,
    document_id: str,
    session_factory: sessionmaker[Session] | None = None,
) -> StreamingResponse:
    if session_factory is None:
        metadata = await _legacy_get_document_metadata(client, settings, school_id, document_id)
        node = configured_node(settings, SCHOOL_TO_NODE.get(school_id, school_id))
        return await _verified_download(client, settings, node, document_id, metadata)
    canonical_school = canonical_school_id(school_id)
    record = _catalog_record(session_factory, canonical_school, document_id)
    if record is None:
        metadata = await _legacy_get_document_metadata(client, settings, school_id, document_id)
        node = configured_node(settings, metadata.node_id)
        return await _verified_download(client, settings, node, document_id, metadata)
    if record.version.status != "available" or record.replica is None:
        raise HTTPException(409, "document has no healthy replica")
    metadata = _metadata_from_catalog(record)
    node = configured_node(settings, record.replica.node_id)
    storage_id = (
        record.document.id
        if record.version.version_number == 1
        else record.version.id
    )
    try:
        return await _verified_download(
            client, settings, node, str(storage_id), metadata
        )
    except HTTPException as error:
        if error.status_code == 404:
            try:
                with session_factory.begin() as session:
                    replica = session.get(DocumentReplica, record.replica.id)
                    if replica is not None:
                        replica.status = "missing"
                        replica.last_error = "registered file is missing from storage node"
            except SQLAlchemyError:
                pass
            raise HTTPException(409, "registered replica is missing") from error
        raise
