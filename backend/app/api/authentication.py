"""Login, current profile and self-service password changes."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from backend.app.auth import Principal, get_current_principal
from backend.app.models import User
from backend.app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    UserSummary,
)
from backend.app.services.auth_service import load_user_by_email, user_summary
from backend.auth.security import (
    create_access_token,
    hash_password,
    normalize_email,
    verify_password,
)


router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/login", response_model=LoginResponse)
def login(request: Request, payload: LoginRequest) -> LoginResponse:
    key = normalize_email(str(payload.email))
    request.app.state.login_throttle.check(key)
    with request.app.state.session_factory.begin() as session:
        user = load_user_by_email(session, key)
        if user is None or not user.is_active or not verify_password(
            payload.password, user.password_hash
        ):
            request.app.state.login_throttle.failure(key)
            raise HTTPException(status_code=401, detail="invalid email or password")
        user.last_login_at = datetime.now(timezone.utc)
        token, expires_at = create_access_token(
            user.id, user.token_version, request.app.state.settings
        )
        response_user = user_summary(session, user)
    request.app.state.login_throttle.success(key)
    return LoginResponse(
        access_token=token,
        expires_at=expires_at,
        user=response_user,
    )


@router.get("/me", response_model=UserSummary)
def me(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> UserSummary:
    with request.app.state.session_factory() as session:
        return user_summary(session, session.get(User, principal.user_id))


@router.post("/change-password", status_code=200)
def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    principal: Principal = Depends(get_current_principal),
) -> dict[str, str]:
    with request.app.state.session_factory.begin() as session:
        user = session.scalar(
            select(User).where(User.id == principal.user_id).with_for_update()
        )
        if user is None or not verify_password(payload.current_password, user.password_hash):
            raise HTTPException(status_code=400, detail="current password is incorrect")
        if verify_password(payload.new_password, user.password_hash):
            raise HTTPException(status_code=400, detail="new password must be different")
        try:
            user.password_hash = hash_password(payload.new_password)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        user.must_change_password = False
        user.token_version += 1
        user.updated_at = datetime.now(timezone.utc)
    return {"status": "password_changed"}
