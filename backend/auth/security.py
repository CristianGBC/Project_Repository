"""Argon2id password handling and strictly configured JWT access tokens."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
import jwt
from jwt import InvalidTokenError

from backend.app.config import CoordinatorSettings


PASSWORD_MIN_LENGTH = 12
password_hasher = PasswordHasher()


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def validate_password(password: str) -> str:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"password must contain at least {PASSWORD_MIN_LENGTH} characters")
    if len(password) > 1024:
        raise ValueError("password must not exceed 1024 characters")
    return password


def hash_password(password: str) -> str:
    return password_hasher.hash(validate_password(password))


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        return password_hasher.verify(encoded_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def create_access_token(
    user_id: UUID,
    token_version: int,
    settings: CoordinatorSettings,
    *,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(
        minutes=settings.auth_access_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "iss": settings.auth_jwt_issuer,
        "aud": settings.auth_jwt_audience,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
        "jti": str(uuid4()),
    }
    return (
        jwt.encode(
            payload,
            settings.auth_jwt_secret,
            algorithm=settings.auth_jwt_algorithm,
        ),
        expires_at,
    )


def decode_access_token(token: str, settings: CoordinatorSettings) -> dict:
    try:
        return jwt.decode(
            token,
            settings.auth_jwt_secret,
            algorithms=[settings.auth_jwt_algorithm],
            issuer=settings.auth_jwt_issuer,
            audience=settings.auth_jwt_audience,
            options={"require": ["sub", "ver", "iss", "aud", "iat", "nbf", "exp"]},
        )
    except InvalidTokenError as error:
        raise ValueError("invalid access token") from error
