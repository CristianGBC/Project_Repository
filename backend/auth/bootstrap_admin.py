"""Create the first SUPER_ADMIN without embedding credentials in source code."""

import getpass
import os
from uuid import uuid4

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select

from backend.app.config import load_settings
from backend.app.database import create_database_engine, create_session_factory
from backend.app.models import SecurityAuditEvent, User
from backend.auth.security import hash_password, normalize_email


def bootstrap_admin(
    email: str,
    password: str,
    first_name: str,
    last_name: str,
) -> tuple[User, bool]:
    settings = load_settings()
    try:
        normalized = normalize_email(validate_email(email, check_deliverability=False).normalized)
    except EmailNotValidError as error:
        raise ValueError("BOOTSTRAP_ADMIN_EMAIL is invalid") from error
    domain = normalized.rsplit("@", 1)[1]
    if settings.auth_allowed_email_domains and domain not in settings.auth_allowed_email_domains:
        raise ValueError("administrator email domain is not allowed")
    if not first_name.strip() or not last_name.strip():
        raise ValueError("administrator first and last names are required")
    engine = create_database_engine()
    session_factory = create_session_factory(engine)
    try:
        with session_factory.begin() as session:
            existing = session.scalar(
                select(User).where(func.lower(User.email) == normalized).with_for_update()
            )
            if existing is not None:
                if existing.global_role != "super_admin":
                    raise RuntimeError("email already belongs to a non-super-admin account")
                return existing, False
            user = User(
                id=uuid4(),
                full_name=f"{first_name.strip()} {last_name.strip()}",
                first_name=first_name.strip(),
                last_name=last_name.strip(),
                email=normalized,
                password_hash=hash_password(password),
                global_role="super_admin",
                is_admin=True,
                is_active=True,
                must_change_password=False,
                token_version=0,
            )
            session.add(user)
            session.flush()
            session.add(
                SecurityAuditEvent(
                    id=uuid4(),
                    actor_user_id=None,
                    action="bootstrap_super_admin",
                    resource_type="user",
                    resource_id=str(user.id),
                    outcome="success",
                )
            )
            return user, True
    finally:
        engine.dispose()


def main() -> None:
    email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip()
    first_name = os.getenv("BOOTSTRAP_ADMIN_FIRST_NAME", "").strip()
    last_name = os.getenv("BOOTSTRAP_ADMIN_LAST_NAME", "").strip()
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
    if not email or not first_name or not last_name:
        raise SystemExit(
            "BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_FIRST_NAME and "
            "BOOTSTRAP_ADMIN_LAST_NAME are required"
        )
    if password is None:
        password = getpass.getpass("Bootstrap administrator password: ")
    _, created = bootstrap_admin(email, password, first_name, last_name)
    print("super administrator created" if created else "super administrator already exists")


if __name__ == "__main__":
    main()
