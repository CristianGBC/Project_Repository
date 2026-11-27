"""Recover interrupted uploads without creating a new logical version."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import BinaryIO
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError
from sqlalchemy import Engine, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import CoordinatorSettings, NodeSettings
from backend.app.models import (
    Document,
    DocumentReplica,
    DocumentVersion,
    PhysicalHost,
    ReconciliationAttempt,
    ReconciliationJob,
    School,
    StorageNode,
)
from backend.app.schemas import DocumentMetadata, StoredDocumentResponse
from backend.app.services.document_service import (
    ReplicaReadError,
    _fetch_verified_file,
)

logger = logging.getLogger(__name__)

TEMPORARY_SOURCE_UNAVAILABLE = "all candidate sources are temporarily unavailable"


@dataclass(frozen=True)
class ReconciliationNode:
    node_id: str
    physical_host_id: str
    home_school_id: str | None
    accessible: bool


@dataclass(frozen=True)
class ReconciliationReplica:
    replica_id: UUID
    node_id: str
    physical_host_id: str
    status: str


@dataclass(frozen=True)
class ReconciliationState:
    job_id: UUID
    attempt_id: UUID
    attempt_number: int
    version_id: UUID
    document_id: UUID
    version_number: int
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    target_replicas: int
    school_id: str
    school_name: str
    origin_node_id: str | None
    replicas: tuple[ReconciliationReplica, ...]
    nodes: dict[str, ReconciliationNode]

    @property
    def storage_id(self) -> UUID:
        return self.document_id if self.version_number == 1 else self.version_id


@dataclass(frozen=True)
class ReconciliationResult:
    version_id: UUID
    status: str
    detail: str


class UploadReconciler:
    """Periodic reconciler for versions that were never atomically published."""

    def __init__(
        self,
        settings: CoordinatorSettings,
        session_factory: sessionmaker[Session],
        client: httpx.AsyncClient,
        engine: Engine,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.client = client
        self.engine = engine
        self.configured_nodes = {node.node_id: node for node in settings.nodes}
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def discover_jobs(self) -> int:
        """Create one durable job for each incomplete catalog version."""

        with self.session_factory.begin() as session:
            version_ids = session.scalars(
                select(DocumentVersion.id)
                .join(DocumentReplica, DocumentReplica.version_id == DocumentVersion.id)
                .where(DocumentVersion.status.in_(("pending", "failed")))
                .where(DocumentVersion.publication_cancelled.is_(False))
                .distinct()
            ).all()
            created = 0
            for version_id in version_ids:
                result = session.execute(
                    insert(ReconciliationJob)
                    .values(
                        id=uuid4(),
                        version_id=version_id,
                        status="pending",
                        attempts=0,
                    )
                    .on_conflict_do_nothing(index_elements=["version_id"])
                )
                created += result.rowcount
            return created

    async def check_cycle(self) -> list[ReconciliationResult]:
        if not self.settings.reconciliation_enabled:
            return []
        self.discover_jobs()
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            job_ids = session.scalars(
                select(ReconciliationJob.id)
                .where(
                    ReconciliationJob.status.in_(
                        ("pending", "retry_wait", "in_progress")
                    ),
                    (ReconciliationJob.next_attempt_at.is_(None))
                    | (ReconciliationJob.next_attempt_at <= now),
                )
                .order_by(ReconciliationJob.created_at, ReconciliationJob.id)
            ).all()
        semaphore = asyncio.Semaphore(
            self.settings.reconciliation_max_concurrent_jobs
        )

        async def limited(job_id: UUID) -> ReconciliationResult | None:
            async with semaphore:
                return await self._reconcile_job(job_id)

        results = await asyncio.gather(*(limited(job_id) for job_id in job_ids))
        return [result for result in results if result is not None]

    async def _reconcile_job(
        self, job_id: UUID
    ) -> ReconciliationResult | None:
        with self.session_factory() as session:
            job = session.get(ReconciliationJob, job_id)
            if job is None:
                return None
            version_id = job.version_id
        lock_id = version_id.int & ((1 << 63) - 1)
        connection = self.engine.connect()
        acquired = False
        state: ReconciliationState | None = None
        try:
            acquired = bool(
                connection.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": lock_id},
                )
            )
            connection.commit()
            if not acquired:
                return None
            state = self._claim_job(job_id)
            if state is None:
                return None
            return await self._execute(state)
        except (httpx.RequestError, RuntimeError) as error:
            if state is None:
                return ReconciliationResult(
                    version_id, "retry_wait", type(error).__name__
                )
            return self._finish_failure(
                state, type(error).__name__, permanent=False
            )
        except SQLAlchemyError as error:
            logger.exception("reconciliation catalog failure job_id=%s", job_id)
            return ReconciliationResult(version_id, "retry_wait", type(error).__name__)
        finally:
            if acquired:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": lock_id},
                )
                connection.commit()
            connection.close()

    def _claim_job(self, job_id: UUID) -> ReconciliationState | None:
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            job = session.scalar(
                select(ReconciliationJob)
                .where(ReconciliationJob.id == job_id)
                .with_for_update()
            )
            if job is None or job.status in {"completed", "failed", "cancelled"}:
                return None
            version = session.get(DocumentVersion, job.version_id)
            if version is None:
                job.status = "failed"
                job.last_error = "version no longer exists"
                job.updated_at = now
                return None
            document = session.scalar(
                select(Document)
                .where(Document.id == version.document_id)
                .with_for_update()
            )
            if document is None:
                job.status = "failed"
                job.last_error = "document no longer exists"
                job.updated_at = now
                return None
            session.refresh(version)
            if version.status == "available":
                job.status = "completed"
                job.completed_at = now
                job.updated_at = now
                return None
            if document.deleted_at is not None or version.publication_cancelled:
                job.status = "cancelled"
                job.last_error = "document is soft-deleted"
                job.completed_at = now
                job.updated_at = now
                return None
            if (
                job.attempts >= self.settings.reconciliation_max_attempts
                and job.last_error != TEMPORARY_SOURCE_UNAVAILABLE
            ):
                job.status = "failed"
                job.last_error = "reconciliation attempt limit reached"
                job.updated_at = now
                return None
            school = session.get(School, document.school_id)
            if school is None:
                job.status = "failed"
                job.last_error = "document school is missing"
                job.updated_at = now
                return None
            node_rows = session.execute(
                select(StorageNode, PhysicalHost).join(
                    PhysicalHost, PhysicalHost.id == StorageNode.physical_host_id
                )
            ).all()
            nodes = {
                node.id: ReconciliationNode(
                    node_id=node.id,
                    physical_host_id=node.physical_host_id,
                    home_school_id=node.home_school_id,
                    accessible=(
                        node.is_active
                        and host.is_active
                        and node.health != "down"
                        and node.id in self.configured_nodes
                    ),
                )
                for node, host in node_rows
            }
            replicas = session.scalars(
                select(DocumentReplica)
                .where(DocumentReplica.version_id == version.id)
                .order_by(DocumentReplica.node_id)
                .with_for_update()
            ).all()
            snapshot = [
                {
                    "replica_id": str(replica.id),
                    "node_id": replica.node_id,
                    "physical_host_id": replica.physical_host_id,
                    "status": replica.status,
                    "attempts": replica.attempts,
                }
                for replica in replicas
            ]
            if job.status == "in_progress":
                interrupted = session.scalar(
                    select(ReconciliationAttempt)
                    .where(
                        ReconciliationAttempt.job_id == job.id,
                        ReconciliationAttempt.outcome == "in_progress",
                    )
                    .order_by(ReconciliationAttempt.attempt_number.desc())
                    .limit(1)
                    .with_for_update()
                )
                if interrupted is not None:
                    interrupted.outcome = "retry"
                    interrupted.detail = "process interrupted before completion"
                    interrupted.finished_at = now
            job.attempts += 1
            job.status = "in_progress"
            job.next_attempt_at = None
            job.updated_at = now
            attempt = ReconciliationAttempt(
                id=uuid4(),
                job_id=job.id,
                attempt_number=job.attempts,
                started_at=now,
                outcome="in_progress",
                replica_snapshot=snapshot,
            )
            session.add(attempt)
            session.flush()
            origin = next(
                (
                    node.node_id
                    for node in nodes.values()
                    if node.home_school_id == document.school_id
                ),
                None,
            )
            return ReconciliationState(
                job_id=job.id,
                attempt_id=attempt.id,
                attempt_number=job.attempts,
                version_id=version.id,
                document_id=document.id,
                version_number=version.version_number,
                filename=version.filename,
                mime_type=version.mime_type,
                size_bytes=version.size_bytes,
                sha256=version.sha256,
                created_at=version.created_at,
                target_replicas=version.target_replicas,
                school_id=school.id,
                school_name=school.name,
                origin_node_id=origin,
                replicas=tuple(
                    ReconciliationReplica(
                        replica_id=replica.id,
                        node_id=replica.node_id,
                        physical_host_id=replica.physical_host_id,
                        status=replica.status,
                    )
                    for replica in replicas
                ),
                nodes=nodes,
            )

    async def _execute(self, state: ReconciliationState) -> ReconciliationResult:
        verified_ids: set[UUID] = set()
        source: BinaryIO | None = None
        temporary_failure = False
        confirmed_invalid = 0
        try:
            for replica in self._ordered_replicas(state):
                node_state = state.nodes.get(replica.node_id)
                if node_state is None or not node_state.accessible:
                    temporary_failure = True
                    continue
                try:
                    temporary = await self._fetch_replica(state, replica)
                except ReplicaReadError as failure:
                    self._record_verification_failure(replica.replica_id, failure)
                    if failure.failure_type in {"missing", "corrupted"}:
                        confirmed_invalid += 1
                    else:
                        temporary_failure = True
                    continue
                self._confirm_existing_replica(replica.replica_id)
                verified_ids.add(replica.replica_id)
                if source is None:
                    source = temporary
                else:
                    temporary.close()

            state = self._reload_state(state)
            while len(verified_ids) < state.target_replicas:
                if source is None:
                    permanent = (
                        confirmed_invalid == len(state.replicas)
                        and not temporary_failure
                    )
                    detail = (
                        "no verified source remains"
                        if permanent
                        else TEMPORARY_SOURCE_UNAVAILABLE
                    )
                    return self._finish_failure(state, detail, permanent=permanent)
                destination = self._select_destination(state, verified_ids)
                if destination is None:
                    return self._finish_failure(
                        state,
                        "no eligible destination is currently available",
                        permanent=False,
                    )
                replica_id = self._reserve_destination(state, destination)
                if replica_id is None:
                    return self._finish_failure(
                        state, "destination reservation conflict", permanent=False
                    )
                self._set_attempt_destination(state.attempt_id, destination.node_id)
                try:
                    await self._upload(state, destination, source)
                    self._confirm_existing_replica(replica_id)
                except (httpx.RequestError, RuntimeError, SQLAlchemyError) as error:
                    self._record_transfer_failure(replica_id, str(error))
                    return self._finish_failure(
                        state, type(error).__name__, permanent=False
                    )
                verified_ids.add(replica_id)
                state = self._reload_state(state)

            self._publish(state, verified_ids)
            logger.info(
                "upload reconciled version_id=%s job_id=%s attempt=%d replicas=%d",
                state.version_id,
                state.job_id,
                state.attempt_number,
                len(verified_ids),
            )
            return ReconciliationResult(
                state.version_id, "completed", "version atomically published"
            )
        finally:
            if source is not None:
                source.close()

    def _ordered_replicas(
        self, state: ReconciliationState
    ) -> list[ReconciliationReplica]:
        return sorted(
            state.replicas,
            key=lambda replica: (
                replica.status != "healthy",
                replica.node_id != state.origin_node_id,
                replica.node_id,
            ),
        )

    async def _fetch_replica(
        self, state: ReconciliationState, replica: ReconciliationReplica
    ) -> BinaryIO:
        node = self.configured_nodes[replica.node_id]
        metadata = DocumentMetadata(
            document_id=state.storage_id,
            filename=state.filename,
            school_id=node.node_id,
            school=node.school,
            size_bytes=state.size_bytes,
            sha256=state.sha256,
            created_at=state.created_at,
            node_id=node.node_id,
        )
        return await _fetch_verified_file(
            self.client,
            self.settings,
            node,
            str(state.storage_id),
            metadata,
        )

    def _confirm_existing_replica(self, replica_id: UUID) -> None:
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            replica = session.scalar(
                select(DocumentReplica)
                .where(DocumentReplica.id == replica_id)
                .with_for_update()
            )
            if replica is None:
                raise RuntimeError("replica reservation disappeared")
            replica.status = "healthy"
            replica.attempts += 1
            replica.stored_at = replica.stored_at or now
            replica.verified_at = now
            replica.last_error = None

    def _record_verification_failure(
        self, replica_id: UUID, failure: ReplicaReadError
    ) -> None:
        with self.session_factory.begin() as session:
            replica = session.get(DocumentReplica, replica_id)
            if replica is None:
                return
            replica.attempts += 1
            replica.last_error = (
                f"reconciliation {failure.failure_type}: {failure.detail}"
            )[:1000]
            if failure.failure_type == "missing":
                replica.status = "missing"
            elif failure.failure_type == "corrupted":
                replica.status = "corrupted"

    def _reload_state(self, original: ReconciliationState) -> ReconciliationState:
        with self.session_factory() as session:
            replicas = session.scalars(
                select(DocumentReplica)
                .where(DocumentReplica.version_id == original.version_id)
                .order_by(DocumentReplica.node_id)
            ).all()
            return ReconciliationState(
                **{
                    **original.__dict__,
                    "replicas": tuple(
                        ReconciliationReplica(
                            replica_id=replica.id,
                            node_id=replica.node_id,
                            physical_host_id=replica.physical_host_id,
                            status=replica.status,
                        )
                        for replica in replicas
                    ),
                }
            )

    def _select_destination(
        self, state: ReconciliationState, verified_ids: set[UUID]
    ) -> ReconciliationNode | None:
        existing = [
            replica
            for replica in state.replicas
            if replica.replica_id not in verified_ids
            and replica.status != "healthy"
            and state.nodes.get(replica.node_id) is not None
            and state.nodes[replica.node_id].accessible
        ]
        existing.sort(
            key=lambda replica: (
                replica.node_id != state.origin_node_id,
                replica.node_id,
            )
        )
        if existing:
            return state.nodes[existing[0].node_id]
        used_nodes = {replica.node_id for replica in state.replicas}
        used_hosts = {replica.physical_host_id for replica in state.replicas}
        candidates = [
            node
            for node in state.nodes.values()
            if node.accessible
            and node.node_id not in used_nodes
            and node.physical_host_id not in used_hosts
        ]
        candidates.sort(
            key=lambda node: (
                node.node_id != state.origin_node_id,
                node.home_school_id != state.school_id,
                node.node_id,
            )
        )
        return candidates[0] if candidates else None

    def _reserve_destination(
        self, state: ReconciliationState, destination: ReconciliationNode
    ) -> UUID | None:
        try:
            with self.session_factory.begin() as session:
                version = session.scalar(
                    select(DocumentVersion)
                    .where(
                        DocumentVersion.id == state.version_id,
                        DocumentVersion.status.in_(("pending", "failed")),
                    )
                    .with_for_update()
                )
                if version is None:
                    return None
                replica = session.scalar(
                    select(DocumentReplica).where(
                        DocumentReplica.version_id == version.id,
                        DocumentReplica.node_id == destination.node_id,
                    )
                )
                if replica is None:
                    replica = DocumentReplica(
                        id=uuid4(),
                        version_id=version.id,
                        node_id=destination.node_id,
                        physical_host_id=destination.physical_host_id,
                        status="pending",
                        attempts=0,
                    )
                    session.add(replica)
                replica.status = "pending"
                replica.last_error = None
                session.flush()
                return replica.id
        except IntegrityError:
            return None

    async def _upload(
        self,
        state: ReconciliationState,
        destination: ReconciliationNode,
        source: BinaryIO,
    ) -> None:
        node: NodeSettings = self.configured_nodes[destination.node_id]
        source.seek(0)
        response = await self.client.put(
            f"{node.url}/internal/repair/{state.storage_id}",
            headers={"X-Repair-Token": self.settings.repair_internal_token},
            data={
                "filename": state.filename,
                "school_id": node.node_id,
                "school": node.school,
                "size_bytes": str(state.size_bytes),
                "sha256": state.sha256,
                "created_at": state.created_at.isoformat(),
            },
            files={"file": ("reconciliation.bin", source, state.mime_type)},
            timeout=self.settings.reconciliation_transfer_timeout_seconds,
        )
        if not response.is_success:
            raise RuntimeError(
                f"reconciliation destination returned HTTP {response.status_code}"
            )
        try:
            stored = StoredDocumentResponse.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise RuntimeError("invalid reconciliation confirmation") from error
        if (
            stored.document_id != state.storage_id
            or stored.node_id != node.node_id
            or stored.size_bytes != state.size_bytes
            or stored.sha256 != state.sha256
        ):
            raise RuntimeError("reconciliation confirmation does not match version")

    def _record_transfer_failure(self, replica_id: UUID, detail: str) -> None:
        with self.session_factory.begin() as session:
            replica = session.get(DocumentReplica, replica_id)
            if replica is None:
                return
            replica.status = "pending"
            replica.attempts += 1
            replica.last_error = detail[:1000]

    def _set_attempt_destination(self, attempt_id: UUID, node_id: str) -> None:
        with self.session_factory.begin() as session:
            attempt = session.get(ReconciliationAttempt, attempt_id)
            if attempt is not None:
                attempt.destination_node_id = node_id

    def _finish_failure(
        self, state: ReconciliationState, detail: str, permanent: bool
    ) -> ReconciliationResult:
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            job = session.get(ReconciliationJob, state.job_id)
            attempt = session.get(ReconciliationAttempt, state.attempt_id)
            if job is None or attempt is None:
                raise RuntimeError("reconciliation audit disappeared")
            version = session.get(DocumentVersion, state.version_id)
            if version is not None and version.publication_cancelled:
                job.status = attempt.outcome = "cancelled"
                job.next_attempt_at = None
                job.completed_at = job.updated_at = attempt.finished_at = now
                return ReconciliationResult(state.version_id, "cancelled", "publication cancelled")
            # Waiting for every potential source to come back is a probe state,
            # not an attempted transfer. Keep its durable backoff active even
            # after the transfer-attempt budget has been reached; otherwise a
            # temporary outage would be misclassified as confirmed data loss.
            exhausted = (
                job.attempts >= self.settings.reconciliation_max_attempts
                and detail != TEMPORARY_SOURCE_UNAVAILABLE
            )
            terminal = permanent or exhausted
            job.status = "failed" if terminal else "retry_wait"
            job.last_error = detail[:1000]
            job.next_attempt_at = (
                None
                if terminal
                else now
                + timedelta(
                    seconds=self.settings.reconciliation_retry_backoff_seconds
                )
            )
            job.updated_at = now
            attempt.outcome = "failed" if terminal else "retry"
            attempt.detail = detail[:1000]
            attempt.finished_at = now
        status = "failed" if terminal else "retry_wait"
        logger.warning(
            "upload reconciliation deferred version_id=%s job_id=%s status=%s "
            "attempt=%d detail=%s",
            state.version_id,
            state.job_id,
            status,
            state.attempt_number,
            detail,
        )
        return ReconciliationResult(state.version_id, status, detail)

    def _publish(self, state: ReconciliationState, verified_ids: set[UUID]) -> None:
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            document = session.scalar(
                select(Document)
                .where(Document.id == state.document_id)
                .with_for_update()
            )
            version = session.scalar(
                select(DocumentVersion)
                .where(DocumentVersion.id == state.version_id)
                .with_for_update()
            )
            job = session.get(ReconciliationJob, state.job_id)
            attempt = session.get(ReconciliationAttempt, state.attempt_id)
            if document is None or version is None or job is None or attempt is None:
                raise RuntimeError("publication catalog state disappeared")
            if document.deleted_at is not None or version.publication_cancelled:
                raise RuntimeError("document was soft-deleted during reconciliation")
            replicas = session.scalars(
                select(DocumentReplica)
                .where(
                    DocumentReplica.version_id == version.id,
                    DocumentReplica.id.in_(verified_ids),
                    DocumentReplica.status == "healthy",
                )
                .with_for_update()
            ).all()
            distinct_hosts = {replica.physical_host_id for replica in replicas}
            if (
                len(replicas) < version.target_replicas
                or len(distinct_hosts) < version.target_replicas
            ):
                raise RuntimeError("verified replication target is incomplete")
            version.status = "available"
            version.committed_at = version.committed_at or now
            current = (
                session.get(DocumentVersion, document.current_version_id)
                if document.current_version_id is not None
                else None
            )
            newer_available = session.scalar(
                select(func.count(DocumentVersion.id)).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.status == "available",
                    DocumentVersion.version_number > version.version_number,
                )
            )
            if (
                not newer_available
                and (current is None or current.version_number < version.version_number)
            ):
                document.current_version_id = version.id
                document.updated_at = now
            job.status = "completed"
            job.last_error = None
            job.next_attempt_at = None
            job.completed_at = now
            job.updated_at = now
            attempt.outcome = "completed"
            attempt.detail = "version atomically published"
            attempt.finished_at = now

    async def run(self) -> None:
        logger.info(
            "upload reconciler started interval_seconds=%.1f backoff_seconds=%.1f "
            "max_attempts=%d max_concurrent_jobs=%d",
            self.settings.reconciliation_interval_seconds,
            self.settings.reconciliation_retry_backoff_seconds,
            self.settings.reconciliation_max_attempts,
            self.settings.reconciliation_max_concurrent_jobs,
        )
        try:
            while not self._stop_event.is_set():
                await self.check_cycle()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.settings.reconciliation_interval_seconds,
                    )
                except TimeoutError:
                    continue
        finally:
            logger.info("upload reconciler stopped")
