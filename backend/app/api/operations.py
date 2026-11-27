"""Read-only operational views for authorized administrative frontends."""

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from backend.app.auth import Principal, get_current_principal, require_super_admin
from backend.app.models import (
    Document,
    DocumentReplica,
    DocumentVersion,
    PhysicalHost,
    ReconciliationAttempt,
    ReconciliationJob,
    SecurityAuditEvent,
    StorageNode,
)
from backend.app.services.replication_diagnostics import find_replication_deficits


router = APIRouter(prefix="/operations", tags=["operations"])


class Overview(BaseModel):
    documents: int
    deleted_documents: int
    versions: dict[str, int]
    replicas: dict[str, int]
    accessible_replicas: int
    versions_in_deficit: int
    versions_with_excess: int
    reconciliation: dict[str, int]


class OperationalVersion(BaseModel):
    id: UUID
    version_number: int
    status: str
    target_replicas: int
    size_bytes: int
    sha256: str


class OperationalDocument(BaseModel):
    id: UUID
    title: str
    school_id: str
    deleted_at: datetime | None
    current_version_id: UUID | None
    owner_id: UUID | None
    versions: list[OperationalVersion]


@router.get("/overview", response_model=Overview)
def overview(request: Request, principal: Principal = Depends(require_super_admin)) -> Overview:
    accessible = (
        (DocumentReplica.status == "healthy") & StorageNode.is_active.is_(True)
        & (StorageNode.health != "down") & PhysicalHost.is_active.is_(True)
    )
    with request.app.state.session_factory() as session:
        counts = {}
        for model in (DocumentVersion, DocumentReplica, ReconciliationJob):
            counts[model] = dict(session.execute(
                select(model.status, func.count()).group_by(model.status)
            ).all())
        documents, deleted = session.execute(
            select(func.count(Document.id), func.count(Document.deleted_at))
        ).one()
        by_version = (
            select(DocumentVersion.target_replicas.label("target"),
                   func.count(DocumentReplica.id).label("registered"),
                   func.count(DocumentReplica.id).filter(accessible).label("accessible"))
            .outerjoin(DocumentReplica, DocumentReplica.version_id == DocumentVersion.id)
            .outerjoin(StorageNode, StorageNode.id == DocumentReplica.node_id)
            .outerjoin(PhysicalHost, PhysicalHost.id == StorageNode.physical_host_id)
            .where(DocumentVersion.status == "available")
            .group_by(DocumentVersion.id, DocumentVersion.target_replicas)
        ).subquery()
        deficit, excess = session.execute(select(
            func.count().filter(by_version.c.accessible < by_version.c.target),
            func.count().filter(by_version.c.registered > by_version.c.target),
        )).one()
        accessible_total = session.scalar(
            select(func.count(DocumentReplica.id)).join(StorageNode, StorageNode.id == DocumentReplica.node_id)
            .join(PhysicalHost, PhysicalHost.id == StorageNode.physical_host_id).where(accessible)
        )
    return Overview(documents=documents, deleted_documents=deleted, versions=counts[DocumentVersion],
                    replicas=counts[DocumentReplica], reconciliation=counts[ReconciliationJob],
                    accessible_replicas=accessible_total, versions_in_deficit=deficit,
                    versions_with_excess=excess)


@router.get("/documents/{document_id}", response_model=OperationalDocument)
def operational_document(
    request: Request, document_id: UUID, principal: Principal = Depends(require_super_admin),
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
) -> OperationalDocument:
    with request.app.state.session_factory() as session:
        row = session.get(Document, document_id)
        if row is None:
            raise HTTPException(status_code=404, detail="document not found")
        versions = session.scalars(select(DocumentVersion).where(DocumentVersion.document_id == row.id)
                                   .order_by(DocumentVersion.version_number.desc()).offset(offset).limit(limit))
        return OperationalDocument(id=row.id, title=row.title, school_id=row.school_id, deleted_at=row.deleted_at,
                                   current_version_id=row.current_version_id, owner_id=row.owner_id,
                                   versions=[OperationalVersion(id=v.id, version_number=v.version_number,
                                                                status=v.status, target_replicas=v.target_replicas,
                                                                size_bytes=v.size_bytes, sha256=v.sha256)
                                             for v in versions])


@router.get("/nodes")
def nodes(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> list[dict]:
    if not principal.is_super_admin and not any(
        "school_admin" in roles for roles in principal.roles_by_school.values()
    ):
        raise HTTPException(status_code=403, detail="administrator role required")
    with request.app.state.session_factory() as session:
        rows = session.scalars(select(StorageNode).order_by(StorageNode.id)).all()
        replica_counts = dict(session.execute(
            select(DocumentReplica.node_id, func.count()).group_by(DocumentReplica.node_id)
        ).all()) if principal.is_super_admin else {}
        return [
            {
                "id": row.id,
                "home_school_id": row.home_school_id,
                **({"port": row.port, "replica_count": replica_counts.get(row.id, 0)}
                   if principal.is_super_admin else {}),
                "physical_host_id": row.physical_host_id,
                "is_active": row.is_active,
                "health": row.health,
                "last_seen_at": row.last_seen_at,
                "health_changed_at": row.health_changed_at,
            }
            for row in rows
        ]


@router.get("/hosts")
def hosts(request: Request, principal: Principal = Depends(require_super_admin)) -> list[dict]:
    with request.app.state.session_factory() as session:
        rows = session.scalars(select(PhysicalHost).order_by(PhysicalHost.id)).all()
        return [
            {
                "id": row.id,
                "hostname": row.hostname,
                "device_type": row.device_type,
                "is_active": row.is_active,
                "ip_address": str(row.ip_address) if row.ip_address else None,
            }
            for row in rows
        ]


@router.get("/replicas")
def replicas(
    request: Request,
    principal: Principal = Depends(require_super_admin),
    version_id: UUID | None = Query(None),
    node_id: str | None = None,
    document_id: UUID | None = None,
    status: str | None = None,
    host_id: str | None = None,
    limit: int = Query(500, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    with request.app.state.session_factory() as session:
        query = select(DocumentReplica, DocumentVersion.document_id).join(DocumentVersion)
        if version_id:
            query = query.where(DocumentReplica.version_id == version_id)
        for column, value in ((DocumentReplica.node_id, node_id), (DocumentVersion.document_id, document_id),
                              (DocumentReplica.status, status), (DocumentReplica.physical_host_id, host_id)):
            if value is not None:
                query = query.where(column == value)
        rows = session.execute(query.order_by(DocumentReplica.created_at.desc(), DocumentReplica.id)
                               .offset(offset).limit(limit)).all()
        return [
            {
                "id": str(row.id),
                "version_id": str(row.version_id),
                "document_id": str(document),
                "node_id": row.node_id,
                "physical_host_id": row.physical_host_id,
                "status": row.status,
                "attempts": row.attempts,
                "last_error": "Error registrado" if row.last_error else None,
                "stored_at": row.stored_at,
                "verified_at": row.verified_at,
                "repair_after": row.repair_after,
            }
            for row, document in rows
        ]


@router.get("/reconciliation/jobs")
def reconciliation_jobs(
    request: Request,
    principal: Principal = Depends(require_super_admin),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    document_id: UUID | None = None,
    job_id: UUID | None = None,
) -> list[dict]:
    with request.app.state.session_factory() as session:
        query = select(ReconciliationJob, DocumentVersion.document_id).join(DocumentVersion)
        for column, value in ((ReconciliationJob.status, status), (DocumentVersion.document_id, document_id),
                              (ReconciliationJob.id, job_id)):
            if value is not None:
                query = query.where(column == value)
        rows = session.execute(query.order_by(ReconciliationJob.created_at.desc(), ReconciliationJob.id)
                               .offset(offset).limit(limit)).all()
        return [
            {
                "id": str(row.id),
                "version_id": str(row.version_id),
                "document_id": str(document),
                "status": row.status,
                "attempts": row.attempts,
                "next_attempt_at": row.next_attempt_at,
                "last_error": "Error registrado" if row.last_error else None,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "completed_at": row.completed_at,
            }
            for row, document in rows
        ]


@router.get("/reconciliation/jobs/{job_id}/attempts")
def reconciliation_attempts(
    request: Request,
    job_id: UUID,
    principal: Principal = Depends(require_super_admin),
) -> list[dict]:
    with request.app.state.session_factory() as session:
        rows = session.scalars(
            select(ReconciliationAttempt)
            .where(ReconciliationAttempt.job_id == job_id)
            .order_by(ReconciliationAttempt.attempt_number)
        ).all()
        return [
            {
                "id": str(row.id),
                "attempt_number": row.attempt_number,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "outcome": row.outcome,
                "destination_node_id": row.destination_node_id,
                "detail": None,
            }
            for row in rows
        ]


@router.get("/deficits")
def deficits(
    request: Request, principal: Principal = Depends(require_super_admin),
    limit: int | None = Query(None, ge=1, le=500), offset: int = Query(0, ge=0),
) -> list[dict]:
    return [
        {**item.__dict__, "operational_deficit": item.operational_deficit}
        for item in find_replication_deficits(request.app.state.session_factory, limit, offset)
    ]


@router.get("/security-audit")
def security_audit(
    request: Request,
    principal: Principal = Depends(require_super_admin),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None, max_length=100),
    actor_user_id: UUID | None = Query(None),
    resource_id: str | None = Query(None, max_length=100),
    day: date | None = Query(None, lt=date.max),
) -> list[dict]:
    query = select(SecurityAuditEvent)
    if action:
        query = query.where(SecurityAuditEvent.action == action)
    if actor_user_id:
        query = query.where(SecurityAuditEvent.actor_user_id == actor_user_id)
    if resource_id:
        query = query.where(SecurityAuditEvent.resource_id == resource_id)
    if day:
        start = datetime.combine(day, time.min, timezone.utc)
        query = query.where(SecurityAuditEvent.created_at >= start, SecurityAuditEvent.created_at < start + timedelta(days=1))
    with request.app.state.session_factory() as session:
        rows = session.scalars(
            query.order_by(SecurityAuditEvent.created_at.desc(), SecurityAuditEvent.id)
            .offset(offset).limit(limit)
        ).all()
        return [
            {
                "id": str(row.id),
                "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
                "action": row.action,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "outcome": row.outcome,
                "detail": None,
                "created_at": row.created_at,
            }
            for row in rows
        ]
