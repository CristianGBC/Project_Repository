"""Document operations coordinated between PostgreSQL and storage nodes.

Database transactions deliberately end before any HTTP transfer. A pending
replica is therefore also the durable reconciliation record for an interrupted
operation.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path
import tempfile
import time
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
    DocumentType,
    DocumentVersion,
    PhysicalHost,
    School,
    StorageNode,
    Subject,
    User,
    document_subjects,
)
from backend.app.schemas import (
    DocumentLifecycleResponse,
    DocumentMetadata,
    DocumentSubjectsResponse,
    DocumentVersionHistory,
    DocumentVersionSummary,
    StoredDocumentResponse,
    SubjectSummary,
)

CHUNK_SIZE = 1024 * 1024

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
    replicas: tuple[DocumentReplica, ...]
    document_type: DocumentType | None
    subjects: tuple[Subject, ...]

    @property
    def replica(self) -> DocumentReplica | None:
        return self.replicas[0] if self.replicas else None


class ReplicaReadError(Exception):
    """One replica-specific read failure that may permit another candidate."""

    def __init__(self, failure_type: str, detail: str) -> None:
        super().__init__(detail)
        self.failure_type = failure_type
        self.detail = detail


logger = logging.getLogger("uvicorn.error")


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
    status_code = (
        response.status_code if response.status_code in {400, 404, 409, 413} else 502
    )
    raise HTTPException(status_code=status_code, detail=detail)


def _service_unavailable(error: httpx.RequestError) -> HTTPException:
    return HTTPException(status_code=503, detail="storage node is unavailable")


def _database_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="document catalog is unavailable")


def _subject_summaries(
    subjects: list[Subject] | tuple[Subject, ...],
) -> list[SubjectSummary]:
    return [
        SubjectSummary(
            id=subject.id,
            school_id=subject.school_id,
            code=subject.code,
            name=subject.name,
            is_active=subject.is_active,
        )
        for subject in sorted(subjects, key=lambda item: (item.code, str(item.id)))
    ]


def _active_subjects(
    session: Session,
    subject_ids: list[UUID] | None,
    school_id: str,
) -> list[Subject]:
    unique_ids = list(dict.fromkeys(subject_ids or []))
    if not unique_ids:
        return []
    subjects = session.scalars(select(Subject).where(Subject.id.in_(unique_ids))).all()
    subjects_by_id = {subject.id: subject for subject in subjects}
    if set(subjects_by_id) != set(unique_ids):
        raise HTTPException(400, "one or more subjects do not exist")
    ordered = [subjects_by_id[subject_id] for subject_id in unique_ids]
    if any(not subject.is_active for subject in ordered):
        raise HTTPException(400, "one or more subjects are inactive")
    if any(subject.school_id != school_id for subject in ordered):
        raise HTTPException(400, "subjects must belong to the document school")
    return ordered


def _validate_subject_requirement(
    document_type: DocumentType | None,
    subjects: list[Subject],
) -> None:
    if document_type is not None and document_type.requires_subject and not subjects:
        raise HTTPException(400, "document type requires at least one subject")


async def _measure_upload(
    upload: UploadFile, max_size_bytes: int
) -> tuple[int, str, BinaryIO]:
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


def _select_storage_nodes(
    session: Session, settings: CoordinatorSettings, school_id: str
) -> list[StorageNode]:
    configured_ids = {node.node_id for node in settings.nodes}
    candidates = session.scalars(
        select(StorageNode)
        .join(PhysicalHost, PhysicalHost.id == StorageNode.physical_host_id)
        .where(
            StorageNode.id.in_(configured_ids),
            StorageNode.is_active.is_(True),
            StorageNode.health != "down",
            PhysicalHost.is_active.is_(True),
        )
    ).all()
    candidates.sort(key=lambda node: (node.home_school_id != school_id, node.id))
    selected: list[StorageNode] = []
    selected_hosts: set[str] = set()
    for node in candidates:
        if node.physical_host_id in selected_hosts:
            continue
        selected.append(node)
        selected_hosts.add(node.physical_host_id)
        if len(selected) == settings.replication_factor:
            return selected
    raise HTTPException(
        503,
        detail=(
            f"replication factor {settings.replication_factor} requires "
            f"{settings.replication_factor} eligible physical hosts; "
            f"found {len(selected_hosts)}"
        ),
    )


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
    document_type_id: str | None,
    subject_ids: list[UUID] | None,
    actor_user_id: UUID | None = None,
) -> tuple[
    Document,
    DocumentVersion,
    list[DocumentReplica],
    School,
    DocumentType | None,
    list[Subject],
]:
    try:
        with session_factory.begin() as session:
            school = session.get(School, school_id)
            if school is None or not school.is_active:
                raise HTTPException(400, "unknown or inactive school_id")
            document_type = None
            if document_type_id is not None:
                document_type = session.get(DocumentType, document_type_id)
                if document_type is None or not document_type.is_active:
                    raise HTTPException(400, "unknown or inactive document_type_id")
            subjects = _active_subjects(session, subject_ids, school.id)
            _validate_subject_requirement(document_type, subjects)
            user = (
                session.get(User, actor_user_id)
                if actor_user_id is not None
                else _technical_user(session, settings)
            )
            if user is None:
                raise HTTPException(401, "authenticated user no longer exists")
            session.flush()
            nodes = _select_storage_nodes(session, settings, school_id)
            document = Document(
                id=uuid4(),
                title=(title or Path(filename).stem)[:255],
                description=description,
                school_id=school.id,
                created_by=user.id,
                owner_id=(user.id if actor_user_id is not None else None),
                document_type_id=(document_type.id if document_type else None),
                visibility="school",
            )
            version = DocumentVersion(
                id=uuid4(),
                document_id=document.id,
                version_number=1,
                filename=filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
                sha256=checksum,
                status="pending",
                target_replicas=settings.replication_factor,
                created_by=user.id,
            )
            replicas = [
                DocumentReplica(
                    id=uuid4(),
                    version_id=version.id,
                    node_id=node.id,
                    physical_host_id=node.physical_host_id,
                    status="pending",
                    attempts=0,
                )
                for node in nodes
            ]
            # The schema intentionally has a deferred circular reference from
            # documents back to its current version. Explicit phase flushes
            # make the initial forward FK ordering unambiguous to SQLAlchemy.
            session.add(document)
            document.subjects = subjects
            session.flush()
            session.add(version)
            session.flush()
            session.add_all(replicas)
            session.flush()
            return document, version, replicas, school, document_type, subjects
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
    version_id: UUID,
    replica_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    try:
        with session_factory.begin() as session:
            version = session.get(DocumentVersion, version_id)
            replica = session.get(DocumentReplica, replica_id)
            if (
                version is None
                or replica is None
                or replica.version_id != version.id
            ):
                raise RuntimeError("pending catalog operation disappeared")
            replica.status = "healthy"
            replica.attempts += 1
            replica.stored_at = now
            replica.verified_at = now
            replica.last_error = None
    except (SQLAlchemyError, RuntimeError) as error:
        raise HTTPException(
            503,
            detail="file stored but replica confirmation is pending",
        ) from error


def _finalize_replicated_version(
    session_factory: sessionmaker[Session],
    document_id: UUID,
    version_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    try:
        with session_factory.begin() as session:
            document = session.scalar(
                select(Document).where(Document.id == document_id).with_for_update()
            )
            version = session.get(DocumentVersion, version_id)
            if document is None or version is None or version.status != "pending":
                raise RuntimeError("pending catalog operation disappeared")
            replicas = session.scalars(
                select(DocumentReplica)
                .where(DocumentReplica.version_id == version.id)
                .with_for_update()
            ).all()
            healthy = [replica for replica in replicas if replica.status == "healthy"]
            distinct_hosts = {replica.physical_host_id for replica in healthy}
            if (
                len(replicas) != version.target_replicas
                or len(healthy) != version.target_replicas
                or len(distinct_hosts) != version.target_replicas
            ):
                raise RuntimeError("replication target has not been satisfied")
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


def _copy_prepared_files(
    prepared_file: BinaryIO,
    count: int,
    max_size_bytes: int,
) -> list[BinaryIO]:
    """Create independent cursors so concurrent HTTP requests read identical bytes."""

    copies: list[BinaryIO] = []
    try:
        for _ in range(count):
            target = tempfile.SpooledTemporaryFile(
                max_size=min(max_size_bytes, CHUNK_SIZE), mode="w+b"
            )
            prepared_file.seek(0)
            while chunk := prepared_file.read(CHUNK_SIZE):
                target.write(chunk)
            target.seek(0)
            copies.append(target)
        return copies
    except Exception:
        for target in copies:
            target.close()
        raise


def _storage_metadata(
    document_id: UUID,
    filename: str,
    node: NodeSettings,
    size_bytes: int,
    checksum: str,
    created_at: datetime,
) -> DocumentMetadata:
    return DocumentMetadata(
        document_id=document_id,
        filename=filename,
        school_id=node.node_id,
        school=node.school,
        size_bytes=size_bytes,
        sha256=checksum,
        created_at=created_at,
        node_id=node.node_id,
    )


async def _store_on_node(
    client: httpx.AsyncClient,
    upload: UploadFile,
    prepared_file: BinaryIO,
    metadata: DocumentMetadata,
    node: NodeSettings,
    storage_internal_token: str,
) -> StoredDocumentResponse:
    try:
        response = await client.put(
            f"{node.url}/files/{metadata.document_id}",
            headers={"X-Storage-Token": storage_internal_token},
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
                    "document.bin",
                    prepared_file,
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


async def _replicate_to_nodes(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    upload: UploadFile,
    prepared_file: BinaryIO,
    session_factory: sessionmaker[Session],
    document: Document,
    version: DocumentVersion,
    replicas: list[DocumentReplica],
    storage_id: UUID,
    filename: str,
    size_bytes: int,
    checksum: str,
    created_at: datetime,
) -> NodeSettings:
    """Transfer one immutable version concurrently and persist every outcome."""

    nodes = [configured_node(settings, replica.node_id) for replica in replicas]
    prepared_copies = _copy_prepared_files(
        prepared_file,
        len(replicas),
        settings.max_upload_size_bytes,
    )

    async def transfer(
        node: NodeSettings,
        prepared_copy: BinaryIO,
    ) -> StoredDocumentResponse:
        metadata = _storage_metadata(
            storage_id,
            filename,
            node,
            size_bytes,
            checksum,
            created_at,
        )
        return await _store_on_node(
            client, upload, prepared_copy, metadata, node, settings.storage_internal_token
        )

    try:
        results = await asyncio.gather(
            *(
                transfer(node, prepared_copy)
                for node, prepared_copy in zip(nodes, prepared_copies, strict=True)
            ),
            return_exceptions=True,
        )
    finally:
        for prepared_copy in prepared_copies:
            prepared_copy.close()

    first_error: HTTPException | None = None
    for replica, result in zip(replicas, results, strict=True):
        if isinstance(result, BaseException):
            if isinstance(result, HTTPException):
                transfer_error = result
            else:
                transfer_error = HTTPException(503, "storage node is unavailable")
            _record_failed_transfer(
                session_factory,
                version.id,
                replica.id,
                str(transfer_error.detail),
            )
            if first_error is None:
                first_error = transfer_error
            continue
        try:
            _confirm_stored_replica(session_factory, version.id, replica.id)
        except HTTPException as error:
            if first_error is None:
                first_error = error

    if first_error is not None:
        raise first_error

    _finalize_replicated_version(session_factory, document.id, version.id)
    return nodes[0]


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
        return await _store_on_node(
            client, upload, prepared_file, metadata, node, settings.storage_internal_token
        )
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
    document_type_id: str | None = None,
    subject_ids: list[UUID] | None = None,
    actor_user_id: UUID | None = None,
) -> StoredDocumentResponse:
    if session_factory is None:
        return await _legacy_upload_document(client, settings, upload, school_id)

    canonical_school = canonical_school_id(school_id)
    filename = _validate_filename(upload.filename)
    size_bytes, checksum, prepared_file = await _measure_upload(
        upload, settings.max_upload_size_bytes
    )
    document, version, replicas, school, document_type, subjects = (
        _create_pending_catalog_records(
            session_factory,
            settings,
            canonical_school,
            filename,
            upload.content_type or "application/octet-stream",
            size_bytes,
            checksum,
            title,
            description,
            document_type_id,
            subject_ids,
            actor_user_id,
        )
    )
    created_at = document.created_at or datetime.now(timezone.utc)
    try:
        node = await _replicate_to_nodes(
            client,
            settings,
            upload,
            prepared_file,
            session_factory,
            document,
            version,
            replicas,
            document.id,
            filename,
            size_bytes,
            checksum,
            created_at,
        )
    finally:
        prepared_file.close()

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
        document_type_id=document.document_type_id,
        document_type_name=document_type.name if document_type else None,
        subjects=_subject_summaries(subjects),
        version_id=version.id,
        version_number=1,
        version_status="available",
        target_replicas=settings.replication_factor,
        owner_id=document.owner_id,
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
    actor_user_id: UUID | None = None,
) -> tuple[
    Document,
    DocumentVersion,
    list[DocumentReplica],
    School,
    DocumentType | None,
    list[Subject],
]:
    parsed_id = _document_uuid(document_id)
    try:
        with session_factory.begin() as session:
            document = session.scalar(
                select(Document).where(Document.id == parsed_id).with_for_update()
            )
            if document is None or document.deleted_at is not None:
                raise HTTPException(404, "document does not exist")
            school = session.get(School, document.school_id)
            if school is None or not school.is_active:
                raise HTTPException(409, "document school is unavailable")
            user = (
                session.get(User, actor_user_id)
                if actor_user_id is not None
                else _technical_user(session, settings)
            )
            if user is None:
                raise HTTPException(401, "authenticated user no longer exists")
            document_type = (
                session.get(DocumentType, document.document_type_id)
                if document.document_type_id
                else None
            )
            subjects = list(document.subjects)
            session.flush()
            nodes = _select_storage_nodes(session, settings, document.school_id)
            latest_number = (
                session.scalar(
                    select(func.max(DocumentVersion.version_number)).where(
                        DocumentVersion.document_id == document.id
                    )
                )
                or 0
            )
            version = DocumentVersion(
                id=uuid4(),
                document_id=document.id,
                version_number=latest_number + 1,
                filename=filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
                sha256=checksum,
                status="pending",
                target_replicas=settings.replication_factor,
                created_by=user.id,
            )
            session.add(version)
            session.flush()
            replicas = [
                DocumentReplica(
                    id=uuid4(),
                    version_id=version.id,
                    node_id=node.id,
                    physical_host_id=node.physical_host_id,
                    status="pending",
                    attempts=0,
                )
                for node in nodes
            ]
            session.add_all(replicas)
            session.flush()
            return document, version, replicas, school, document_type, subjects
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
    actor_user_id: UUID | None = None,
) -> StoredDocumentResponse:
    if session_factory is None:
        raise _database_unavailable()
    filename = _validate_filename(upload.filename)
    size_bytes, checksum, prepared_file = await _measure_upload(
        upload, settings.max_upload_size_bytes
    )
    document, version, replicas, school, document_type, subjects = (
        _create_pending_version(
            session_factory,
            settings,
            document_id,
            filename,
            upload.content_type or "application/octet-stream",
            size_bytes,
            checksum,
            actor_user_id,
        )
    )
    created_at = version.created_at or datetime.now(timezone.utc)
    # Version 1 historically uses document.id on disk. Every later immutable
    # version uses version.id, so no physical file can be overwritten.
    try:
        node = await _replicate_to_nodes(
            client,
            settings,
            upload,
            prepared_file,
            session_factory,
            document,
            version,
            replicas,
            version.id,
            filename,
            size_bytes,
            checksum,
            created_at,
        )
    finally:
        prepared_file.close()

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
        document_type_id=document.document_type_id,
        document_type_name=document_type.name if document_type else None,
        subjects=_subject_summaries(subjects),
        version_id=version.id,
        version_number=version.version_number,
        version_status="available",
        target_replicas=settings.replication_factor,
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


def replace_document_subjects(
    session_factory: sessionmaker[Session] | None,
    document_id: str,
    subject_ids: list[UUID],
) -> DocumentSubjectsResponse:
    if session_factory is None:
        raise _database_unavailable()
    parsed_id = _document_uuid(document_id)
    try:
        with session_factory.begin() as session:
            document = session.scalar(
                select(Document).where(Document.id == parsed_id).with_for_update()
            )
            if document is None or document.deleted_at is not None:
                raise HTTPException(404, "document does not exist")
            document_type = (
                session.get(DocumentType, document.document_type_id)
                if document.document_type_id
                else None
            )
            subjects = _active_subjects(session, subject_ids, document.school_id)
            _validate_subject_requirement(document_type, subjects)
            document.subjects = subjects
            document.updated_at = datetime.now(timezone.utc)
            session.flush()
            return DocumentSubjectsResponse(
                document_id=document.id,
                subjects=_subject_summaries(subjects),
            )
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise _database_unavailable() from error


def list_documents(
    session_factory: sessionmaker[Session] | None,
    school_id: str | None,
    subject_id: UUID | None,
    document_type_id: str | None,
    *,
    document_id: UUID | None = None,
    search: str | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> list[DocumentMetadata]:
    if session_factory is None:
        raise _database_unavailable()
    canonical_school = canonical_school_id(school_id) if school_id else None
    try:
        with session_factory() as session:
            query = select(Document.id, Document.school_id).join(
                DocumentVersion, Document.current_version_id == DocumentVersion.id
            ).where(
                Document.deleted_at.is_(None),
                DocumentVersion.status == "available",
            )
            if document_id is not None:
                query = query.where(Document.id == document_id)
            if search and search.strip():
                query = query.where(
                    func.coalesce(Document.title, DocumentVersion.filename)
                    .icontains(search.strip(), autoescape=True)
                )
            if canonical_school is not None:
                query = query.where(Document.school_id == canonical_school)
            if document_type_id is not None:
                query = query.where(Document.document_type_id == document_type_id)
            if subject_id is not None:
                query = query.where(
                    select(document_subjects.c.document_id).where(
                        document_subjects.c.document_id == Document.id,
                        document_subjects.c.subject_id == subject_id,
                    ).exists()
                )
            rows = session.execute(
                query.order_by(Document.created_at.desc(), Document.id)
                .offset(offset).limit(limit)
            ).all()
    except SQLAlchemyError as error:
        raise _database_unavailable() from error

    results: list[DocumentMetadata] = []
    for document_id_value, document_school in rows:
        record = _catalog_record(
            session_factory,
            document_school,
            str(document_id_value),
        )
        if record is not None:
            results.append(_metadata_from_catalog(record))
    return results


def _healthy_replicas(
    session: Session,
    version_id: UUID,
    school_id: str,
) -> tuple[DocumentReplica, ...]:
    """Return every eligible replica in deterministic failover order."""

    return tuple(
        session.scalars(
            select(DocumentReplica)
            .join(StorageNode, StorageNode.id == DocumentReplica.node_id)
            .join(PhysicalHost, PhysicalHost.id == StorageNode.physical_host_id)
            .where(
                DocumentReplica.version_id == version_id,
                DocumentReplica.status == "healthy",
                StorageNode.is_active.is_(True),
                StorageNode.health != "down",
                PhysicalHost.is_active.is_(True),
            )
            .order_by(StorageNode.home_school_id != school_id, StorageNode.id)
        ).all()
    )


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
            document_type = (
                session.get(DocumentType, document.document_type_id)
                if document.document_type_id
                else None
            )
            subjects = tuple(document.subjects)
            replicas = _healthy_replicas(session, version.id, document.school_id)
            if school is None:
                raise HTTPException(409, "document catalog is inconsistent")
            for value in (
                document,
                version,
                school,
                *replicas,
                document_type,
                *subjects,
            ):
                if value is not None:
                    session.expunge(value)
            return CatalogRecord(
                document,
                version,
                school,
                replicas,
                document_type,
                subjects,
            )
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
    record = _specific_version_record(session_factory, document_id, version_number)
    if record.version.status != "available" or not record.replicas:
        raise HTTPException(409, "document version has no healthy replica")
    storage_id = (
        record.document.id if record.version.version_number == 1 else record.version.id
    )
    return await _download_with_failover(
        client,
        settings,
        record,
        str(storage_id),
        session_factory,
    )


def soft_delete_document(
    session_factory: sessionmaker[Session] | None,
    settings: CoordinatorSettings,
    document_id: str,
    actor_user_id: UUID | None = None,
) -> DocumentLifecycleResponse:
    if session_factory is None:
        raise _database_unavailable()
    parsed_id = _document_uuid(document_id)
    try:
        with session_factory.begin() as session:
            document = session.scalar(
                select(Document).where(Document.id == parsed_id).with_for_update()
            )
            if document is None:
                raise HTTPException(404, "document does not exist")
            user = (
                session.get(User, actor_user_id)
                if actor_user_id is not None
                else _technical_user(session, settings)
            )
            if user is None:
                raise HTTPException(401, "authenticated user no longer exists")
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
    actor_user_id: UUID | None = None,
) -> DocumentLifecycleResponse:
    if session_factory is None:
        raise _database_unavailable()
    parsed_id = _document_uuid(document_id)
    try:
        with session_factory.begin() as session:
            document = session.scalar(
                select(Document).where(Document.id == parsed_id).with_for_update()
            )
            if document is None:
                raise HTTPException(404, "document does not exist")
            if actor_user_id is not None:
                if session.get(User, actor_user_id) is None:
                    raise HTTPException(401, "authenticated user no longer exists")
            else:
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
            document_type = (
                session.get(DocumentType, document.document_type_id)
                if document.document_type_id
                else None
            )
            subjects = tuple(document.subjects)
            if version is None or school is None:
                raise HTTPException(409, "document catalog is inconsistent")
            replicas = _healthy_replicas(session, version.id, document.school_id)
            for value in (
                document,
                version,
                school,
                *replicas,
                document_type,
                *subjects,
            ):
                if value is not None:
                    session.expunge(value)
            return CatalogRecord(
                document,
                version,
                school,
                replicas,
                document_type,
                subjects,
            )
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise _database_unavailable() from error


def _metadata_from_catalog(record: CatalogRecord) -> DocumentMetadata:
    version = record.version
    return DocumentMetadata(
        document_id=record.document.id,
        filename=version.filename,
        school_id=record.school.id,
        school=record.school.name,
        size_bytes=version.size_bytes,
        sha256=version.sha256,
        created_at=record.document.created_at,
        node_id=record.replica.node_id if record.replica else "unavailable",
        title=record.document.title,
        description=record.document.description,
        document_type_id=record.document.document_type_id,
        document_type_name=(
            record.document_type.name if record.document_type else None
        ),
        subjects=_subject_summaries(record.subjects),
        version_id=version.id,
        version_number=version.version_number,
        version_status=version.status,
        target_replicas=version.target_replicas,
        owner_id=record.document.owner_id,
    )


async def _legacy_get_document_metadata(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    school_id: str,
    document_id: str,
) -> DocumentMetadata:
    node = configured_node(settings, SCHOOL_TO_NODE.get(school_id, school_id))
    try:
        response = await client.get(
            f"{node.url}/files/{document_id}/metadata",
            headers={"X-Storage-Token": settings.storage_internal_token},
        )
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
        return await _legacy_get_document_metadata(
            client, settings, school_id, document_id
        )
    canonical_school = canonical_school_id(school_id)
    record = _catalog_record(session_factory, canonical_school, document_id)
    if record is not None:
        return _metadata_from_catalog(record)
    # Only a genuine catalog miss falls back to Stage 3 JSON. Database failures
    # and soft-deleted rows never do.
    return await _legacy_get_document_metadata(client, settings, school_id, document_id)


def _download_headers(filename: str) -> dict[str, str]:
    ascii_name = (
        "".join(
            character
            for character in filename
            if 32 <= ord(character) < 127 and character not in {'"', "\\"}
        )
        or "document"
    )
    encoded_name = quote(filename, safe="")
    return {
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"
    }


async def _stream_and_close(source: BinaryIO) -> AsyncIterator[bytes]:
    try:
        while chunk := source.read(CHUNK_SIZE):
            yield chunk
    finally:
        source.close()


async def _fetch_verified_file(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    node: NodeSettings,
    document_id: str,
    metadata: DocumentMetadata,
) -> BinaryIO:
    request = client.build_request(
        "GET",
        f"{node.url}/files/{document_id}",
        headers={"X-Storage-Token": settings.storage_internal_token},
    )
    try:
        response = await client.send(request, stream=True)
    except httpx.RequestError as error:
        raise ReplicaReadError("unreachable", type(error).__name__) from error
    temporary = tempfile.SpooledTemporaryFile(
        max_size=min(settings.max_upload_size_bytes, CHUNK_SIZE), mode="w+b"
    )
    size_bytes = 0
    digest = hashlib.sha256()
    try:
        if response.status_code == 404:
            raise ReplicaReadError("missing", "storage returned HTTP 404")
        if response.status_code == 409:
            raise ReplicaReadError("corrupted", "storage rejected replica integrity")
        if response.status_code >= 500:
            raise ReplicaReadError(
                "server_error", f"storage returned HTTP {response.status_code}"
            )
        if not response.is_success:
            raise ReplicaReadError(
                "server_error", f"storage returned HTTP {response.status_code}"
            )
        async for chunk in response.aiter_bytes(CHUNK_SIZE):
            size_bytes += len(chunk)
            if (
                size_bytes > settings.max_upload_size_bytes
                or size_bytes > metadata.size_bytes
            ):
                raise ReplicaReadError("corrupted", "replica size exceeds metadata")
            digest.update(chunk)
            temporary.write(chunk)
    except httpx.RequestError as error:
        temporary.close()
        raise ReplicaReadError("interrupted", type(error).__name__) from error
    except Exception:
        temporary.close()
        raise
    finally:
        await response.aclose()
    if size_bytes != metadata.size_bytes or digest.hexdigest() != metadata.sha256:
        temporary.close()
        raise ReplicaReadError(
            "corrupted", "replica size or checksum does not match metadata"
        )
    temporary.seek(0)
    return temporary


def _streaming_download(temporary: BinaryIO, filename: str) -> StreamingResponse:
    return StreamingResponse(
        _stream_and_close(temporary),
        media_type="application/octet-stream",
        headers=_download_headers(filename),
    )


def _record_replica_read_failure(
    session_factory: sessionmaker[Session],
    replica_id: UUID,
    failure: ReplicaReadError,
) -> None:
    try:
        with session_factory.begin() as session:
            replica = session.scalar(
                select(DocumentReplica)
                .where(DocumentReplica.id == replica_id)
                .with_for_update()
            )
            if replica is None or replica.status != "healthy":
                return
            replica.attempts += 1
            replica.last_error = (
                f"read {failure.failure_type}: {failure.detail}"
            )[:1000]
            if failure.failure_type == "missing":
                replica.status = "missing"
            elif failure.failure_type == "corrupted":
                replica.status = "corrupted"
    except SQLAlchemyError:
        return


def _record_replica_read_success(
    session_factory: sessionmaker[Session],
    replica_id: UUID,
) -> None:
    try:
        with session_factory.begin() as session:
            replica = session.scalar(
                select(DocumentReplica)
                .where(DocumentReplica.id == replica_id)
                .with_for_update()
            )
            if replica is None or replica.status != "healthy":
                return
            replica.verified_at = datetime.now(timezone.utc)
            replica.last_error = None
    except SQLAlchemyError:
        return


def _legacy_download_error(failure: ReplicaReadError) -> HTTPException:
    if failure.failure_type == "missing":
        return HTTPException(404, "document does not exist")
    if failure.failure_type == "corrupted":
        return HTTPException(409, "document integrity verification failed")
    return HTTPException(503, "storage node is unavailable")


async def _verified_download(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    node: NodeSettings,
    document_id: str,
    metadata: DocumentMetadata,
) -> StreamingResponse:
    """Preserve the single-node Stage 3 download contract."""

    try:
        temporary = await _fetch_verified_file(
            client, settings, node, document_id, metadata
        )
    except ReplicaReadError as failure:
        raise _legacy_download_error(failure) from failure
    return _streaming_download(temporary, metadata.filename)


async def _download_with_failover(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    record: CatalogRecord,
    storage_id: str,
    session_factory: sessionmaker[Session],
) -> StreamingResponse:
    metadata = _metadata_from_catalog(record)
    configured_nodes = {node.node_id: node for node in settings.nodes}
    candidates = [
        (replica, configured_nodes[replica.node_id])
        for replica in record.replicas
        if replica.node_id in configured_nodes
    ]
    if not candidates:
        raise HTTPException(503, "document is temporarily unavailable")

    failures: list[ReplicaReadError] = []
    for attempt, (replica, node) in enumerate(candidates, start=1):
        started = time.monotonic()
        try:
            temporary = await _fetch_verified_file(
                client,
                settings,
                node,
                storage_id,
                metadata,
            )
        except ReplicaReadError as failure:
            elapsed_ms = (time.monotonic() - started) * 1000
            failures.append(failure)
            _record_replica_read_failure(session_factory, replica.id, failure)
            logger.warning(
                "replica read failed document_id=%s version_id=%s replica_id=%s "
                "node_id=%s attempt=%d failure_type=%s elapsed_ms=%.1f",
                record.document.id,
                record.version.id,
                replica.id,
                replica.node_id,
                attempt,
                failure.failure_type,
                elapsed_ms,
            )
            continue

        elapsed_ms = (time.monotonic() - started) * 1000
        _record_replica_read_success(session_factory, replica.id)
        logger.info(
            "replica read succeeded document_id=%s version_id=%s replica_id=%s "
            "node_id=%s attempt=%d elapsed_ms=%.1f",
            record.document.id,
            record.version.id,
            replica.id,
            replica.node_id,
            attempt,
            elapsed_ms,
        )
        return _streaming_download(temporary, metadata.filename)

    conclusive = {"missing", "corrupted"}
    if failures and all(
        failure.failure_type in conclusive for failure in failures
    ):
        raise HTTPException(409, "no intact document replica is available")
    raise HTTPException(503, "document is temporarily unavailable")


async def download_document(
    client: httpx.AsyncClient,
    settings: CoordinatorSettings,
    school_id: str,
    document_id: str,
    session_factory: sessionmaker[Session] | None = None,
) -> StreamingResponse:
    if session_factory is None:
        metadata = await _legacy_get_document_metadata(
            client, settings, school_id, document_id
        )
        node = configured_node(settings, SCHOOL_TO_NODE.get(school_id, school_id))
        return await _verified_download(client, settings, node, document_id, metadata)
    canonical_school = canonical_school_id(school_id)
    record = _catalog_record(session_factory, canonical_school, document_id)
    if record is None:
        metadata = await _legacy_get_document_metadata(
            client, settings, school_id, document_id
        )
        node = configured_node(settings, metadata.node_id)
        return await _verified_download(client, settings, node, document_id, metadata)
    if record.version.status != "available" or not record.replicas:
        raise HTTPException(409, "document has no healthy replica")
    storage_id = (
        record.document.id if record.version.version_number == 1 else record.version.id
    )
    return await _download_with_failover(
        client,
        settings,
        record,
        str(storage_id),
        session_factory,
    )
