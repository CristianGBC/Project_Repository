"""Administrative user and school-membership management."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError

from backend.app.auth import Principal, get_current_principal
from backend.app.models import School, SchoolMembership, SecurityAuditEvent, User
from backend.app.schemas.auth import (
    MembershipCreate,
    MembershipResponse,
    ResetPasswordRequest,
    UserCreate,
    UserSummary,
    UserUpdate,
)
from backend.app.services.auth_service import user_summary
from backend.auth.security import hash_password, normalize_email


router = APIRouter(prefix="/users", tags=["users"])


def _admin_schools(principal: Principal) -> set[str]:
    return {
        school_id
        for school_id, roles in principal.roles_by_school.items()
        if "school_admin" in roles
    }


def _require_administrator(principal: Principal) -> None:
    if not principal.is_super_admin and not _admin_schools(principal):
        raise HTTPException(status_code=403, detail="administrator role required")


def _audit(session, actor: Principal, action: str, resource_id: UUID, detail: str | None = None) -> None:
    session.add(
        SecurityAuditEvent(
            id=uuid4(),
            actor_user_id=actor.user_id,
            action=action,
            resource_type="user",
            resource_id=str(resource_id),
            outcome="success",
            detail=detail,
        )
    )


def _visible_user_query(principal: Principal):
    if principal.is_super_admin:
        return select(User)
    schools = _admin_schools(principal)
    return select(User).distinct().join(SchoolMembership).where(
        SchoolMembership.school_id.in_(schools)
    )


def _get_visible_user(session, principal: Principal, user_id: UUID, *, lock: bool = False) -> User:
    query = _visible_user_query(principal).where(User.id == user_id)
    if lock:
        query = query.with_for_update()
    user = session.scalar(query)
    if user is None:
        raise HTTPException(status_code=404, detail="user does not exist")
    return user


def _validate_membership_scope(principal: Principal, item: MembershipCreate) -> None:
    if principal.is_super_admin:
        return
    if item.school_id not in _admin_schools(principal):
        raise HTTPException(status_code=403, detail="school is outside administrator scope")
    if item.role == "school_admin":
        raise HTTPException(status_code=403, detail="school administrators cannot grant administrator roles")


def _require_global_account_scope(session, principal: Principal, user: User) -> None:
    if principal.is_super_admin:
        return
    memberships = session.scalars(
        select(SchoolMembership).where(
            SchoolMembership.user_id == user.id,
            SchoolMembership.is_active.is_(True),
        )
    ).all()
    admin_schools = _admin_schools(principal)
    if user.global_role is not None or any(
        item.school_id not in admin_schools or item.role == "school_admin"
        for item in memberships
    ):
        raise HTTPException(status_code=403, detail="shared or privileged account is outside administrator scope")


@router.get("", response_model=list[UserSummary])
def list_users(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: str | None = Query(None, max_length=100),
    is_active: bool | None = Query(None),
) -> list[UserSummary]:
    _require_administrator(principal)
    with request.app.state.session_factory() as session:
        query = _visible_user_query(principal)
        if search:
            pattern = f"%{search.strip()}%"
            query = query.where(
                or_(User.email.ilike(pattern), User.full_name.ilike(pattern))
            )
        if is_active is not None:
            query = query.where(User.is_active.is_(is_active))
        users = session.scalars(query.order_by(User.email).offset(offset).limit(limit)).all()
        return [user_summary(session, user) for user in users]


@router.post("", response_model=UserSummary, status_code=201)
def create_user(
    request: Request,
    payload: UserCreate,
    principal: Principal = Depends(get_current_principal),
) -> UserSummary:
    _require_administrator(principal)
    if payload.global_role and not principal.is_super_admin:
        raise HTTPException(status_code=403, detail="global roles require a super administrator")
    if not principal.is_super_admin and not payload.memberships:
        raise HTTPException(status_code=400, detail="a school administrator must assign an in-scope membership")
    for item in payload.memberships:
        _validate_membership_scope(principal, item)
    email = normalize_email(str(payload.email))
    domain = email.rsplit("@", 1)[1]
    allowed = request.app.state.settings.auth_allowed_email_domains
    if allowed and domain not in allowed:
        raise HTTPException(status_code=422, detail="email domain is not allowed")
    try:
        password_hash = hash_password(payload.temporary_password)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        with request.app.state.session_factory.begin() as session:
            for item in payload.memberships:
                if session.get(School, item.school_id) is None:
                    raise HTTPException(status_code=400, detail="school does not exist")
            user = User(
                id=uuid4(),
                full_name=f"{payload.first_name.strip()} {payload.last_name.strip()}",
                first_name=payload.first_name.strip(),
                last_name=payload.last_name.strip(),
                email=email,
                password_hash=password_hash,
                is_admin=payload.global_role == "super_admin",
                global_role=payload.global_role,
                is_active=True,
                must_change_password=True,
                token_version=0,
            )
            session.add(user)
            session.flush()
            for item in payload.memberships:
                session.add(
                    SchoolMembership(
                        id=uuid4(),
                        user_id=user.id,
                        school_id=item.school_id,
                        role=item.role,
                        affiliation=item.role if item.role in {"student", "professor"} else "staff",
                        is_active=True,
                    )
                )
            _audit(session, principal, "create_user", user.id)
            session.flush()
            return user_summary(session, user)
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="email or membership already exists") from error


@router.get("/{user_id}", response_model=UserSummary)
def get_user(request: Request, user_id: UUID, principal: Principal = Depends(get_current_principal)) -> UserSummary:
    _require_administrator(principal)
    with request.app.state.session_factory() as session:
        return user_summary(session, _get_visible_user(session, principal, user_id))


@router.patch("/{user_id}", response_model=UserSummary)
def update_user(request: Request, user_id: UUID, payload: UserUpdate, principal: Principal = Depends(get_current_principal)) -> UserSummary:
    if not principal.is_super_admin:
        raise HTTPException(status_code=403, detail="global account changes require a super administrator")
    try:
        with request.app.state.session_factory.begin() as session:
            user = _get_visible_user(session, principal, user_id, lock=True)
            values = payload.model_dump(exclude_unset=True)
            if "first_name" in values:
                user.first_name = values["first_name"].strip()
            if "last_name" in values:
                user.last_name = values["last_name"].strip()
            if "email" in values:
                normalized = normalize_email(str(values["email"]))
                allowed = request.app.state.settings.auth_allowed_email_domains
                if allowed and normalized.rsplit("@", 1)[1] not in allowed:
                    raise HTTPException(status_code=422, detail="email domain is not allowed")
                user.email = normalized
            user.full_name = " ".join(filter(None, (user.first_name, user.last_name)))
            user.updated_at = datetime.now(timezone.utc)
            _audit(session, principal, "update_user", user.id)
            session.flush()
            return user_summary(session, user)
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="email already exists") from error


def _set_active(request: Request, user_id: UUID, active: bool, principal: Principal) -> UserSummary:
    if not principal.is_super_admin:
        raise HTTPException(status_code=403, detail="account activation requires a super administrator")
    with request.app.state.session_factory.begin() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(924225034)"))
        user = _get_visible_user(session, principal, user_id, lock=True)
        if not active and user.global_role == "super_admin" and user.is_active:
            active_super_admins = session.scalars(
                select(User).where(
                    User.global_role == "super_admin", User.is_active.is_(True)
                ).with_for_update()
            ).all()
            if len(active_super_admins) <= 1:
                raise HTTPException(status_code=409, detail="cannot deactivate the last active super administrator")
        user.is_active = active
        user.token_version += 1
        user.updated_at = datetime.now(timezone.utc)
        _audit(session, principal, "activate_user" if active else "deactivate_user", user.id)
        session.flush()
        return user_summary(session, user)


@router.post("/{user_id}/activate", response_model=UserSummary)
def activate_user(request: Request, user_id: UUID, principal: Principal = Depends(get_current_principal)) -> UserSummary:
    return _set_active(request, user_id, True, principal)


@router.post("/{user_id}/deactivate", response_model=UserSummary)
def deactivate_user(request: Request, user_id: UUID, principal: Principal = Depends(get_current_principal)) -> UserSummary:
    return _set_active(request, user_id, False, principal)


@router.post("/{user_id}/reset-password", response_model=UserSummary)
def reset_password(request: Request, user_id: UUID, payload: ResetPasswordRequest, principal: Principal = Depends(get_current_principal)) -> UserSummary:
    _require_administrator(principal)
    with request.app.state.session_factory.begin() as session:
        user = _get_visible_user(session, principal, user_id, lock=True)
        _require_global_account_scope(session, principal, user)
        user.password_hash = hash_password(payload.temporary_password)
        user.must_change_password = True
        user.token_version += 1
        user.updated_at = datetime.now(timezone.utc)
        _audit(session, principal, "reset_password", user.id)
        session.flush()
        return user_summary(session, user)


@router.get("/{user_id}/memberships", response_model=list[MembershipResponse])
def memberships(request: Request, user_id: UUID, principal: Principal = Depends(get_current_principal)) -> list[MembershipResponse]:
    _require_administrator(principal)
    with request.app.state.session_factory() as session:
        user = _get_visible_user(session, principal, user_id)
        return user_summary(session, user).memberships


@router.post("/{user_id}/memberships", response_model=MembershipResponse, status_code=201)
def add_membership(request: Request, user_id: UUID, payload: MembershipCreate, principal: Principal = Depends(get_current_principal)) -> MembershipResponse:
    _require_administrator(principal)
    _validate_membership_scope(principal, payload)
    if principal.user_id == user_id:
        raise HTTPException(status_code=403, detail="users cannot modify their own roles")
    try:
        with request.app.state.session_factory.begin() as session:
            user = _get_visible_user(session, principal, user_id, lock=True)
            if session.get(School, payload.school_id) is None:
                raise HTTPException(status_code=400, detail="school does not exist")
            item = SchoolMembership(
                id=uuid4(), user_id=user.id, school_id=payload.school_id,
                role=payload.role,
                affiliation=payload.role if payload.role in {"student", "professor"} else "staff",
                is_active=True,
            )
            session.add(item)
            user.token_version += 1
            _audit(session, principal, "add_membership", user.id, f"{payload.school_id}:{payload.role}")
            session.flush()
            return MembershipResponse(id=item.id, school_id=item.school_id, role=item.role, is_active=item.is_active)
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="membership already exists") from error


@router.delete("/{user_id}/memberships/{membership_id}", status_code=204)
def remove_membership(request: Request, user_id: UUID, membership_id: UUID, principal: Principal = Depends(get_current_principal)) -> None:
    _require_administrator(principal)
    if principal.user_id == user_id:
        raise HTTPException(status_code=403, detail="users cannot modify their own roles")
    with request.app.state.session_factory.begin() as session:
        _get_visible_user(session, principal, user_id, lock=True)
        item = session.get(SchoolMembership, membership_id)
        if item is None or item.user_id != user_id:
            raise HTTPException(status_code=404, detail="membership does not exist")
        _validate_membership_scope(principal, MembershipCreate(school_id=item.school_id, role=item.role))
        session.delete(item)
        user = session.get(User, user_id)
        user.token_version += 1
        _audit(session, principal, "remove_membership", user.id, f"{item.school_id}:{item.role}")
