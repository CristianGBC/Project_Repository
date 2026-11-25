"""Authentication helpers with generic failures and bounded in-memory throttling."""

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from threading import Lock

from fastapi import HTTPException
from sqlalchemy import func, select

from backend.app.auth import principal_from_user
from backend.app.models import SchoolMembership, User
from backend.app.schemas.auth import MembershipResponse, UserSummary
from backend.auth.security import normalize_email


class LoginThrottle:
    """Small per-process limiter; deployments should add an edge-wide limiter."""

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window = timedelta(seconds=window_seconds)
        self._attempts: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            attempts = self._attempts[key]
            while attempts and now - attempts[0] > self.window:
                attempts.popleft()
            if len(attempts) >= self.max_attempts:
                raise HTTPException(status_code=429, detail="too many login attempts")

    def failure(self, key: str) -> None:
        with self._lock:
            self._attempts[key].append(datetime.now(timezone.utc))

    def success(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


def load_user_by_email(session, email: str) -> User | None:
    return session.scalar(
        select(User).where(func.lower(User.email) == normalize_email(email))
    )


def user_summary(session, user: User) -> UserSummary:
    memberships = session.scalars(
        select(SchoolMembership)
        .where(SchoolMembership.user_id == user.id)
        .order_by(SchoolMembership.school_id, SchoolMembership.role)
    ).all()
    return UserSummary(
        id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        is_active=user.is_active,
        global_role=user.global_role,
        must_change_password=user.must_change_password,
        created_at=user.created_at,
        memberships=[
            MembershipResponse(
                id=item.id,
                school_id=item.school_id,
                role=item.role,
                is_active=item.is_active,
            )
            for item in memberships
        ],
    )
