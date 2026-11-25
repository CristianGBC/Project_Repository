"""Read-only operational views for authorized administrative frontends."""

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from backend.app.auth import Principal, get_current_principal, require_super_admin
from backend.app.models import (
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


@router.get("/nodes")
def nodes(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> list[dict]:
    if not principal.is_super_admin and not any(
        "school_admin" in roles for roles in principal.roles_by_school.values()
    ):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="administrator role required")
    with request.app.state.session_factory() as session:
        rows = session.scalars(select(StorageNode).order_by(StorageNode.id)).all()
        return [
            {
                "id": row.id,
                "home_school_id": row.home_school_id,
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
            }
            for row in rows
        ]


@router.get("/replicas")
def replicas(
    request: Request,
    principal: Principal = Depends(require_super_admin),
    version_id: str | None = Query(None),
) -> list[dict]:
    with request.app.state.session_factory() as session:
        query = select(DocumentReplica).order_by(DocumentReplica.created_at.desc())
        if version_id:
            query = query.where(DocumentReplica.version_id == version_id)
        rows = session.scalars(query.limit(500)).all()
        return [
            {
                "id": str(row.id),
                "version_id": str(row.version_id),
                "node_id": row.node_id,
                "physical_host_id": row.physical_host_id,
                "status": row.status,
                "attempts": row.attempts,
                "last_error": row.last_error,
                "stored_at": row.stored_at,
                "verified_at": row.verified_at,
            }
            for row in rows
        ]


@router.get("/reconciliation/jobs")
def reconciliation_jobs(
    request: Request,
    principal: Principal = Depends(require_super_admin),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    with request.app.state.session_factory() as session:
        rows = session.scalars(
            select(ReconciliationJob).order_by(ReconciliationJob.created_at.desc()).limit(limit)
        ).all()
        return [
            {
                "id": str(row.id),
                "version_id": str(row.version_id),
                "status": row.status,
                "attempts": row.attempts,
                "next_attempt_at": row.next_attempt_at,
                "last_error": row.last_error,
                "created_at": row.created_at,
                "completed_at": row.completed_at,
            }
            for row in rows
        ]


@router.get("/reconciliation/jobs/{job_id}/attempts")
def reconciliation_attempts(
    request: Request,
    job_id: str,
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
                "detail": row.detail,
            }
            for row in rows
        ]


@router.get("/deficits")
def deficits(request: Request, principal: Principal = Depends(require_super_admin)) -> list[dict]:
    return [
        {**item.__dict__, "operational_deficit": item.operational_deficit}
        for item in find_replication_deficits(request.app.state.session_factory)
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
