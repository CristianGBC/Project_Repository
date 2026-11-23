"""Read-only diagnostics for versions with an operational replica deficit."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import (
    DocumentReplica,
    DocumentVersion,
    PhysicalHost,
    StorageNode,
)


@dataclass(frozen=True)
class ReplicationDeficit:
    version_id: UUID
    target_replicas: int
    registered_replicas: int
    healthy_replicas: int
    accessible_replicas: int

    @property
    def operational_deficit(self) -> int:
        return max(self.target_replicas - self.accessible_replicas, 0)


def find_replication_deficits(
    session_factory: sessionmaker[Session],
) -> list[ReplicationDeficit]:
    """Return available versions that have fewer accessible copies than target."""

    accessible = (
        (DocumentReplica.status == "healthy")
        & StorageNode.is_active.is_(True)
        & (StorageNode.health != "down")
        & PhysicalHost.is_active.is_(True)
    )
    healthy = DocumentReplica.status == "healthy"
    with session_factory() as session:
        rows = session.execute(
            select(
                DocumentVersion.id,
                DocumentVersion.target_replicas,
                func.count(DocumentReplica.id),
                func.sum(case((healthy, 1), else_=0)),
                func.sum(case((accessible, 1), else_=0)),
            )
            .join(
                DocumentReplica,
                DocumentReplica.version_id == DocumentVersion.id,
                isouter=True,
            )
            .join(
                StorageNode,
                StorageNode.id == DocumentReplica.node_id,
                isouter=True,
            )
            .join(
                PhysicalHost,
                PhysicalHost.id == StorageNode.physical_host_id,
                isouter=True,
            )
            .where(DocumentVersion.status == "available")
            .group_by(DocumentVersion.id, DocumentVersion.target_replicas)
        ).all()

    deficits = [
        ReplicationDeficit(
            version_id=row[0],
            target_replicas=row[1],
            registered_replicas=row[2] or 0,
            healthy_replicas=row[3] or 0,
            accessible_replicas=row[4] or 0,
        )
        for row in rows
    ]
    return [item for item in deficits if item.operational_deficit > 0]
