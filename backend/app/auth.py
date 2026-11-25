"""FastAPI authentication dependencies backed by current PostgreSQL state."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from backend.app.models import SchoolMembership, User
from backend.auth.security import decode_access_token


bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    first_name: str | None
    last_name: str | None
    email: str
    is_active: bool
    global_role: str | None
    must_change_password: bool
    token_version: int
    roles_by_school: dict[str, frozenset[str]]

    @property
    def is_super_admin(self) -> bool:
        return self.global_role == "super_admin"

    def has_school_role(self, school_id: str, role: str) -> bool:
        return role in self.roles_by_school.get(school_id, frozenset())

    def administers_school(self, school_id: str) -> bool:
        return self.is_super_admin or self.has_school_role(school_id, "school_admin")


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def principal_from_user(session, user: User) -> Principal:
    memberships = session.scalars(
        select(SchoolMembership).where(
            SchoolMembership.user_id == user.id,
            SchoolMembership.is_active.is_(True),
        )
    ).all()
    roles: dict[str, set[str]] = {}
    for membership in memberships:
        roles.setdefault(membership.school_id, set()).add(membership.role)
    return Principal(
        user_id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        is_active=user.is_active,
        global_role=user.global_role,
        must_change_password=user.must_change_password,
        token_version=user.token_version,
        roles_by_school={key: frozenset(value) for key, value in roles.items()},
    )


def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    session_factory = request.app.state.session_factory
    if session_factory is None:
        raise _unauthorized()
    try:
        payload = decode_access_token(credentials.credentials, request.app.state.settings)
        user_id = UUID(payload["sub"])
        token_version = int(payload["ver"])
    except (ValueError, KeyError, TypeError):
        raise _unauthorized() from None
    with session_factory() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active or user.token_version != token_version:
            raise _unauthorized()
        principal = principal_from_user(session, user)
    if principal.must_change_password and request.url.path not in {
        "/auth/me",
        "/auth/change-password",
    }:
        raise HTTPException(status_code=403, detail="password change required")
    return principal


def require_super_admin(
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    if not principal.is_super_admin:
        raise HTTPException(status_code=403, detail="super administrator required")
    return principal
