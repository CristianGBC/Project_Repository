"""Automatic, version-scoped repair of available document replicas."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import time
from typing import BinaryIO, Literal
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import CoordinatorSettings, NodeSettings
from backend.app.models import (
    Document,
    DocumentReplica,
    DocumentVersion,
    PhysicalHost,
    School,
    StorageNode,
)
from backend.app.schemas import DocumentMetadata, StoredDocumentResponse
from backend.app.services.document_service import (
    ReplicaReadError,
    _fetch_verified_file,
    _record_replica_read_failure,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatalogNode:
    node_id: str
    physical_host_id: str
    home_school_id: str | None
    health: str
    health_changed_at: datetime
    node_active: bool
    host_active: bool

    @property
    def accessible(self) -> bool:
        return self.node_active and self.host_active and self.health != "down"


@dataclass(frozen=True)
class ReplicaState:
    replica_id: UUID
    node_id: str
    physical_host_id: str
    status: str
    repair_after: datetime | None


@dataclass(frozen=True)
class VersionState:
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
    replicas: tuple[ReplicaState, ...]
    nodes: dict[str, CatalogNode]

    @property
    def storage_id(self) -> UUID:
        return self.document_id if self.version_number == 1 else self.version_id


@dataclass(frozen=True)
class RepairAction:
    kind: Literal["repair", "cleanup"]
    node_id: str
    replica_id: UUID | None = None


@dataclass(frozen=True)
class RepairResult:
    version_id: UUID
    action: str
    node_id: str | None
    success: bool
    detail: str


class ReplicaRepair:
    """Reconcile available versions without participating in user requests."""

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

    async def check_cycle(self) -> list[RepairResult]:
        if not self.settings.repair_enabled:
            return []
        with self.session_factory() as session:
            version_ids = session.scalars(
                select(DocumentVersion.id)
                .where(DocumentVersion.status == "available")
                .order_by(DocumentVersion.id)
            ).all()
        semaphore = asyncio.Semaphore(self.settings.repair_max_concurrent_jobs)

        async def limited(version_id: UUID) -> list[RepairResult]:
            async with semaphore:
                return await self._repair_version(version_id)

        batches = await asyncio.gather(*(limited(value) for value in version_ids))
        return [result for batch in batches for result in batch]

    async def _repair_version(self, version_id: UUID) -> list[RepairResult]:
        lock_id = version_id.int & ((1 << 63) - 1)
        connection = self.engine.connect()
        acquired = False
        try:
            acquired = bool(
                connection.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": lock_id},
                )
            )
            connection.commit()
            if not acquired:
                return []
            results: list[RepairResult] = []
            for _ in range(6):
                state = self._load_state(version_id)
                if state is None:
                    break
                action = self._plan_action(state)
                if action is None:
                    break
                if action.kind == "cleanup":
                    result = await self._cleanup_replica(state, action)
                else:
                    result = await self._repair_replica(state, action)
                results.append(result)
                if not result.success:
                    break
            return results
        except SQLAlchemyError:
            logger.exception("repair catalog failure version_id=%s", version_id)
            return [RepairResult(version_id, "catalog", None, False, "database error")]
        finally:
            if acquired:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": lock_id},
                )
                connection.commit()
            connection.close()

    def _load_state(self, version_id: UUID) -> VersionState | None:
        with self.session_factory() as session:
            version = session.get(DocumentVersion, version_id)
            if version is None or version.status != "available":
                return None
            document = session.get(Document, version.document_id)
            if document is None:
                return None
            school = session.get(School, document.school_id)
            if school is None:
                return None
            nodes = session.execute(
                select(StorageNode, PhysicalHost).join(
                    PhysicalHost, PhysicalHost.id == StorageNode.physical_host_id
                )
            ).all()
            node_states = {
                node.id: CatalogNode(
                    node_id=node.id,
                    physical_host_id=node.physical_host_id,
                    home_school_id=node.home_school_id,
                    health=node.health,
                    health_changed_at=node.health_changed_at,
                    node_active=node.is_active,
                    host_active=host.is_active,
                )
                for node, host in nodes
                if node.id in self.configured_nodes
            }
            replicas = session.scalars(
                select(DocumentReplica)
                .where(DocumentReplica.version_id == version.id)
                .order_by(DocumentReplica.node_id)
            ).all()
            origin = next(
                (
                    node.node_id
                    for node in node_states.values()
                    if node.home_school_id == document.school_id
                ),
                None,
            )
            return VersionState(
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
                    ReplicaState(
                        replica_id=replica.id,
                        node_id=replica.node_id,
                        physical_host_id=replica.physical_host_id,
                        status=replica.status,
                        repair_after=replica.repair_after,
                    )
                    for replica in replicas
                ),
                nodes=node_states,
            )

    @staticmethod
    def _due(replica: ReplicaState, now: datetime) -> bool:
        return replica.repair_after is None or replica.repair_after <= now

    def _plan_action(self, state: VersionState) -> RepairAction | None:
        now = datetime.now(timezone.utc)
        accessible = [
            replica
            for replica in state.replicas
            if replica.status == "healthy"
            and state.nodes.get(replica.node_id) is not None
            and state.nodes[replica.node_id].accessible
        ]
        actionable = [
            replica
            for replica in state.replicas
            if replica.status in {"missing", "corrupted", "pending", "failed"}
            and self._due(replica, now)
            and state.nodes.get(replica.node_id) is not None
            and state.nodes[replica.node_id].accessible
        ]
        actionable.sort(
            key=lambda replica: (
                replica.status not in {"missing", "corrupted"},
                replica.node_id != state.origin_node_id,
                replica.node_id,
            )
        )
        if actionable:
            replica = actionable[0]
            return RepairAction("repair", replica.node_id, replica.replica_id)

        origin_replica = next(
            (
                replica
                for replica in state.replicas
                if replica.node_id == state.origin_node_id
                and replica.status == "healthy"
            ),
            None,
        )
        origin_node = state.nodes.get(state.origin_node_id or "")
        if origin_node is not None and origin_node.accessible and origin_replica is None:
            destination = self._new_destination(state, preferred=origin_node.node_id)
            if destination is not None:
                return RepairAction("repair", destination)

        if len(accessible) < state.target_replicas:
            invalid_exists = any(
                replica.status in {"missing", "corrupted"}
                for replica in state.replicas
            )
            down_replicas = [
                replica
                for replica in state.replicas
                if replica.status == "healthy"
                and state.nodes.get(replica.node_id) is not None
                and state.nodes[replica.node_id].health == "down"
            ]
            grace_elapsed = any(
                now - state.nodes[replica.node_id].health_changed_at
                >= timedelta(seconds=self.settings.repair_down_grace_seconds)
                for replica in down_replicas
            )
            pending_exists = any(
                replica.status in {"pending", "failed"}
                for replica in state.replicas
            )
            if invalid_exists or grace_elapsed or pending_exists:
                destination = self._new_destination(state)
                if destination is not None:
                    return RepairAction("repair", destination)

        if (
            self.settings.repair_excess_cleanup_enabled
            and len(accessible) > state.target_replicas
        ):
            keep = sorted(
                accessible,
                key=lambda replica: (
                    replica.node_id != state.origin_node_id,
                    replica.node_id,
                ),
            )[: state.target_replicas]
            kept_ids = {replica.replica_id for replica in keep}
            removable = sorted(
                (replica for replica in accessible if replica.replica_id not in kept_ids),
                key=lambda replica: replica.node_id,
                reverse=True,
            )
            if removable:
                replica = removable[0]
                return RepairAction("cleanup", replica.node_id, replica.replica_id)
        return None

    def _new_destination(
        self, state: VersionState, preferred: str | None = None
    ) -> str | None:
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
                node.node_id != preferred if preferred else True,
                node.home_school_id != state.school_id,
                node.node_id,
            )
        )
        return candidates[0].node_id if candidates else None

    def _reserve_replica(
        self, state: VersionState, action: RepairAction
    ) -> UUID | None:
        try:
            with self.session_factory.begin() as session:
                version = session.scalar(
                    select(DocumentVersion)
                    .where(
                        DocumentVersion.id == state.version_id,
                        DocumentVersion.status == "available",
                    )
                    .with_for_update()
                )
                if version is None:
                    return None
                if action.replica_id is not None:
                    replica = session.get(DocumentReplica, action.replica_id)
                    if replica is None or replica.version_id != version.id:
                        return None
                else:
                    node = session.get(StorageNode, action.node_id)
                    if node is None:
                        return None
                    replica = DocumentReplica(
                        id=uuid4(),
                        version_id=version.id,
                        node_id=node.id,
                        physical_host_id=node.physical_host_id,
                        status="pending",
                        attempts=0,
                    )
                    session.add(replica)
                replica.status = "pending"
                replica.attempts += 1
                replica.last_error = None
                replica.repair_after = None
                session.flush()
                return replica.id
        except IntegrityError:
            return None

    def _source_candidates(self, state: VersionState) -> list[ReplicaState]:
        candidates = [
            replica
            for replica in state.replicas
            if replica.status == "healthy"
            and state.nodes.get(replica.node_id) is not None
            and state.nodes[replica.node_id].accessible
        ]
        return sorted(
            candidates,
            key=lambda replica: (
                replica.node_id != state.origin_node_id,
                replica.node_id,
            ),
        )

    async def _fetch_source(
        self, state: VersionState
    ) -> tuple[BinaryIO, ReplicaState] | None:
        for replica in self._source_candidates(state):
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
            try:
                source = await _fetch_verified_file(
                    self.client,
                    self.settings,
                    node,
                    str(state.storage_id),
                    metadata,
                )
                return source, replica
            except ReplicaReadError as failure:
                _record_replica_read_failure(
                    self.session_factory, replica.replica_id, failure
                )
                logger.warning(
                    "repair source rejected version_id=%s replica_id=%s "
                    "node_id=%s failure_type=%s",
                    state.version_id,
                    replica.replica_id,
                    replica.node_id,
                    failure.failure_type,
                )
        return None

    async def _upload_repair(
        self, state: VersionState, destination: NodeSettings, source: BinaryIO
    ) -> None:
        source.seek(0)
        response = await self.client.put(
            f"{destination.url}/internal/repair/{state.storage_id}",
            headers={"X-Repair-Token": self.settings.repair_internal_token},
            data={
                "filename": state.filename,
                "school_id": destination.node_id,
                "school": destination.school,
                "size_bytes": str(state.size_bytes),
                "sha256": state.sha256,
                "created_at": state.created_at.isoformat(),
            },
            files={"file": ("repair.bin", source, state.mime_type)},
            timeout=self.settings.repair_transfer_timeout_seconds,
        )
        if not response.is_success:
            raise RuntimeError(f"repair target returned HTTP {response.status_code}")
        try:
            stored = StoredDocumentResponse.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise RuntimeError("invalid repair confirmation") from error
        if (
            stored.document_id != state.storage_id
            or stored.node_id != destination.node_id
            or stored.size_bytes != state.size_bytes
            or stored.sha256 != state.sha256
        ):
            raise RuntimeError("repair confirmation does not match version")

    def _complete_repair(self, replica_id: UUID) -> None:
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            replica = session.scalar(
                select(DocumentReplica)
                .where(DocumentReplica.id == replica_id)
                .with_for_update()
            )
            if replica is None or replica.status != "pending":
                raise RuntimeError("repair reservation disappeared")
            replica.status = "healthy"
            replica.stored_at = now
            replica.verified_at = now
            replica.last_error = None
            replica.repair_after = None

    def _defer_repair(self, replica_id: UUID, detail: str) -> None:
        with self.session_factory.begin() as session:
            replica = session.get(DocumentReplica, replica_id)
            if replica is None:
                return
            replica.status = "pending"
            replica.last_error = detail[:1000]
            replica.repair_after = datetime.now(timezone.utc) + timedelta(
                seconds=self.settings.repair_retry_backoff_seconds
            )

    async def _repair_replica(
        self, state: VersionState, action: RepairAction
    ) -> RepairResult:
        started = time.monotonic()
        replica_id = self._reserve_replica(state, action)
        if replica_id is None:
            return RepairResult(
                state.version_id, "repair", action.node_id, False, "reservation failed"
            )
        fetched = await self._fetch_source(state)
        if fetched is None:
            self._defer_repair(replica_id, "no verified source is currently accessible")
            return RepairResult(
                state.version_id, "repair", action.node_id, False, "no verified source"
            )
        source, source_replica = fetched
        try:
            await self._upload_repair(
                state, self.configured_nodes[action.node_id], source
            )
            self._complete_repair(replica_id)
        except (httpx.RequestError, RuntimeError, SQLAlchemyError) as error:
            self._defer_repair(replica_id, str(error))
            logger.warning(
                "replica repair failed version_id=%s destination_node_id=%s "
                "source_node_id=%s detail=%s",
                state.version_id,
                action.node_id,
                source_replica.node_id,
                type(error).__name__,
            )
            return RepairResult(
                state.version_id, "repair", action.node_id, False, type(error).__name__
            )
        finally:
            source.close()
        elapsed_ms = (time.monotonic() - started) * 1000
        logger.info(
            "replica repaired version_id=%s replica_id=%s source_node_id=%s "
            "destination_node_id=%s elapsed_ms=%.1f",
            state.version_id,
            replica_id,
            source_replica.node_id,
            action.node_id,
            elapsed_ms,
        )
        return RepairResult(
            state.version_id, "repair", action.node_id, True, "replica verified"
        )

    async def _cleanup_replica(
        self, state: VersionState, action: RepairAction
    ) -> RepairResult:
        if action.replica_id is None or action.node_id == state.origin_node_id:
            return RepairResult(
                state.version_id, "cleanup", action.node_id, False, "unsafe target"
            )
        with self.session_factory() as session:
            alternatives_before = session.execute(
                select(DocumentReplica, StorageNode, PhysicalHost)
                .join(StorageNode, StorageNode.id == DocumentReplica.node_id)
                .join(PhysicalHost, PhysicalHost.id == StorageNode.physical_host_id)
                .where(
                    DocumentReplica.version_id == state.version_id,
                    DocumentReplica.id != action.replica_id,
                    DocumentReplica.status == "healthy",
                    StorageNode.is_active.is_(True),
                    StorageNode.health != "down",
                    PhysicalHost.is_active.is_(True),
                )
            ).all()
        if (
            len(alternatives_before) < state.target_replicas
            or len({item[0].physical_host_id for item in alternatives_before})
            < state.target_replicas
        ):
            return RepairResult(
                state.version_id,
                "cleanup",
                action.node_id,
                False,
                "pre-delete safety check failed",
            )
        logger.info(
            "replica cleanup intended version_id=%s replica_id=%s node_id=%s",
            state.version_id,
            action.replica_id,
            action.node_id,
        )
        node = self.configured_nodes[action.node_id]
        try:
            response = await self.client.delete(
                f"{node.url}/internal/repair/{state.storage_id}",
                headers={"X-Repair-Token": self.settings.repair_internal_token},
                params={"expected_sha256": state.sha256},
                timeout=self.settings.repair_transfer_timeout_seconds,
            )
            if response.status_code != 204:
                raise RuntimeError(
                    f"cleanup target returned HTTP {response.status_code}"
                )
            with self.session_factory.begin() as session:
                replica = session.scalar(
                    select(DocumentReplica)
                    .where(DocumentReplica.id == action.replica_id)
                    .with_for_update()
                )
                if replica is None:
                    return RepairResult(
                        state.version_id, "cleanup", action.node_id, True, "already removed"
                    )
                alternatives = session.execute(
                    select(DocumentReplica, StorageNode, PhysicalHost)
                    .join(StorageNode, StorageNode.id == DocumentReplica.node_id)
                    .join(PhysicalHost, PhysicalHost.id == StorageNode.physical_host_id)
                    .where(
                        DocumentReplica.version_id == state.version_id,
                        DocumentReplica.id != replica.id,
                        DocumentReplica.status == "healthy",
                        StorageNode.is_active.is_(True),
                        StorageNode.health != "down",
                        PhysicalHost.is_active.is_(True),
                    )
                    .with_for_update()
                ).all()
                if (
                    len(alternatives) < state.target_replicas
                    or len({item[0].physical_host_id for item in alternatives})
                    < state.target_replicas
                ):
                    raise RuntimeError("cleanup safety check failed after delete")
                session.delete(replica)
        except (httpx.RequestError, RuntimeError, SQLAlchemyError) as error:
            logger.warning(
                "replica cleanup failed version_id=%s replica_id=%s node_id=%s "
                "detail=%s",
                state.version_id,
                action.replica_id,
                action.node_id,
                type(error).__name__,
            )
            return RepairResult(
                state.version_id, "cleanup", action.node_id, False, type(error).__name__
            )
        logger.info(
            "replica cleanup complete version_id=%s replica_id=%s node_id=%s",
            state.version_id,
            action.replica_id,
            action.node_id,
        )
        return RepairResult(
            state.version_id, "cleanup", action.node_id, True, "replica retired"
        )

    async def run(self) -> None:
        logger.info(
            "replica repair started interval_seconds=%.1f down_grace_seconds=%.1f "
            "max_concurrent_jobs=%d cleanup_enabled=%s",
            self.settings.repair_interval_seconds,
            self.settings.repair_down_grace_seconds,
            self.settings.repair_max_concurrent_jobs,
            self.settings.repair_excess_cleanup_enabled,
        )
        try:
            while not self._stop_event.is_set():
                await self.check_cycle()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.settings.repair_interval_seconds,
                    )
                except TimeoutError:
                    continue
        finally:
            logger.info("replica repair stopped")
