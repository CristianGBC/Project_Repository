"""Authentication and RBAC integration tests against isolated PostgreSQL."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import httpx
import jwt
import pytest
from sqlalchemy import create_engine, func, select

from backend.app.config import load_settings
from backend.app.database import create_session_factory
from backend.app.main import create_app
from backend.app.models import Document, SchoolMembership, User
from backend.app.seed import seed_initial_catalog
from backend.auth.bootstrap_admin import bootstrap_admin
from backend.auth.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from backend.tests.test_document_catalog_integration import replicated_storage_handler


PASSWORD = "Correct horse battery staple 123!"
NEW_PASSWORD = "A different secure password 456!"


@pytest.fixture(scope="module")
def auth_catalog():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not database_url.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must target a test database")
    os.environ["DATABASE_URL"] = database_url
    backend_dir = Path(__file__).resolve().parents[1]
    command.upgrade(Config(str(backend_dir / "alembic.ini")), "head")
    seed_initial_catalog()
    engine = create_engine(database_url, pool_pre_ping=True)
    settings = replace(
        load_settings(),
        database_url=database_url,
        auth_jwt_secret="test-jwt-secret-with-more-than-thirty-two-characters",
        replication_factor=1,
    )
    try:
        yield create_session_factory(engine), settings
    finally:
        engine.dispose()


def create_user(
    session_factory,
    *,
    email: str,
    password: str = PASSWORD,
    global_role: str | None = None,
    memberships: tuple[tuple[str, str], ...] = (),
    active: bool = True,
    must_change: bool = False,
) -> User:
    with session_factory.begin() as session:
        user = User(
            id=uuid4(),
            full_name="Phase Six User",
            first_name="Phase",
            last_name="Six",
            email=email.lower(),
            password_hash=hash_password(password),
            is_admin=global_role == "super_admin",
            global_role=global_role,
            is_active=active,
            must_change_password=must_change,
            token_version=0,
        )
        session.add(user)
        session.flush()
        for school_id, role in memberships:
            session.add(
                SchoolMembership(
                    id=uuid4(),
                    user_id=user.id,
                    school_id=school_id,
                    role=role,
                    affiliation=role if role in {"student", "professor"} else "staff",
                    is_active=True,
                )
            )
        return user


def api_request(
    session_factory,
    settings,
    method: str,
    path: str,
    *,
    token: str | None = None,
    handler=None,
    **kwargs,
) -> httpx.Response:
    async def run() -> httpx.Response:
        outgoing = httpx.AsyncClient(
            transport=httpx.MockTransport(
                handler or (lambda request: httpx.Response(503))
            )
        )
        application = create_app(settings, outgoing, session_factory)
        async with application.router.lifespan_context(application):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application),
                base_url="http://testserver",
            ) as client:
                headers = kwargs.pop("headers", {})
                if token:
                    headers = {**headers, "Authorization": f"Bearer {token}"}
                response = await client.request(method, path, headers=headers, **kwargs)
        await outgoing.aclose()
        return response

    return asyncio.run(run())


def token_for(user: User, settings) -> str:
    return create_access_token(user.id, user.token_version, settings)[0]


def test_argon2id_password_storage_and_policy() -> None:
    encoded = hash_password(PASSWORD)
    assert encoded.startswith("$argon2id$")
    assert verify_password(PASSWORD, encoded)
    assert not verify_password("wrong password", encoded)
    with pytest.raises(ValueError, match="at least 12"):
        hash_password("too short")


def test_bootstrap_admin_is_idempotent_and_never_resets_password(auth_catalog) -> None:
    session_factory, _ = auth_catalog
    email = f"bootstrap-{uuid4()}@example.edu"
    first, created = bootstrap_admin(email, PASSWORD, "Initial", "Admin")
    second, created_again = bootstrap_admin(email.upper(), NEW_PASSWORD, "Other", "Name")
    assert created is True and created_again is False
    assert first.id == second.id
    with session_factory() as session:
        stored = session.get(User, first.id)
        assert stored.global_role == "super_admin"
        assert verify_password(PASSWORD, stored.password_hash)
        assert not verify_password(NEW_PASSWORD, stored.password_hash)


def test_login_profile_password_change_and_old_token_revocation(auth_catalog) -> None:
    session_factory, settings = auth_catalog
    email = f"professor-{uuid4()}@example.edu"
    user = create_user(
        session_factory,
        email=email,
        memberships=(("school-math", "professor"),),
        must_change=True,
    )
    login = api_request(
        session_factory, settings, "POST", "/auth/login",
        json={"email": email.upper(), "password": PASSWORD},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    assert "password_hash" not in login.text
    profile = api_request(session_factory, settings, "GET", "/auth/me", token=token)
    assert profile.status_code == 200
    blocked = api_request(session_factory, settings, "GET", "/documents", token=token)
    assert blocked.status_code == 403
    wrong = api_request(
        session_factory, settings, "POST", "/auth/change-password", token=token,
        json={"current_password": "incorrect", "new_password": NEW_PASSWORD},
    )
    assert wrong.status_code == 400
    changed = api_request(
        session_factory, settings, "POST", "/auth/change-password", token=token,
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 200
    assert api_request(session_factory, settings, "GET", "/auth/me", token=token).status_code == 401
    relogin = api_request(
        session_factory, settings, "POST", "/auth/login",
        json={"email": email, "password": NEW_PASSWORD},
    )
    assert relogin.status_code == 200 and relogin.json()["user"]["must_change_password"] is False


def test_login_failures_are_generic_and_inactive_accounts_cannot_login(auth_catalog) -> None:
    session_factory, settings = auth_catalog
    email = f"inactive-{uuid4()}@example.edu"
    create_user(session_factory, email=email, active=False)
    inactive = api_request(session_factory, settings, "POST", "/auth/login", json={"email": email, "password": PASSWORD})
    missing = api_request(session_factory, settings, "POST", "/auth/login", json={"email": f"missing-{uuid4()}@example.edu", "password": PASSWORD})
    wrong = api_request(session_factory, settings, "POST", "/auth/login", json={"email": email, "password": "wrong"})
    assert {inactive.status_code, missing.status_code, wrong.status_code} == {401}
    assert inactive.json() == missing.json() == wrong.json()


def test_repeated_login_failures_are_rate_limited(auth_catalog) -> None:
    session_factory, settings = auth_catalog

    async def scenario() -> list[int]:
        application = create_app(settings, session_factory=session_factory)
        async with application.router.lifespan_context(application):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application),
                base_url="http://testserver",
            ) as client:
                responses = [
                    await client.post(
                        "/auth/login",
                        json={
                            "email": "rate-limited@example.edu",
                            "password": "incorrect",
                        },
                    )
                    for _ in range(settings.auth_login_max_attempts + 1)
                ]
                return [response.status_code for response in responses]

    statuses = asyncio.run(scenario())
    assert statuses[:-1] == [401] * settings.auth_login_max_attempts
    assert statuses[-1] == 429


@pytest.mark.parametrize("kind", ["expired", "issuer", "audience", "tampered"])
def test_invalid_jwt_variants_are_rejected(auth_catalog, kind: str) -> None:
    session_factory, settings = auth_catalog
    user = create_user(session_factory, email=f"jwt-{kind}-{uuid4()}@example.edu")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id), "ver": 0, "iss": settings.auth_jwt_issuer,
        "aud": settings.auth_jwt_audience, "iat": now, "nbf": now,
        "exp": now + timedelta(minutes=5),
    }
    if kind == "expired":
        payload["exp"] = now - timedelta(seconds=1)
    elif kind == "issuer":
        payload["iss"] = "wrong"
    elif kind == "audience":
        payload["aud"] = "wrong"
    token = jwt.encode(payload, settings.auth_jwt_secret, algorithm="HS256")
    if kind == "tampered":
        token = token[:-1] + ("a" if token[-1] != "a" else "b")
    response = api_request(session_factory, settings, "GET", "/auth/me", token=token)
    assert response.status_code == 401


def test_anonymous_access_and_public_registration_are_absent(auth_catalog) -> None:
    session_factory, settings = auth_catalog
    assert api_request(session_factory, settings, "GET", "/documents").status_code == 401
    assert api_request(session_factory, settings, "POST", "/auth/register", json={}).status_code == 404


def test_student_upload_is_rejected_before_catalog_or_storage(auth_catalog) -> None:
    session_factory, settings = auth_catalog
    student = create_user(
        session_factory,
        email=f"student-{uuid4()}@example.edu",
        memberships=(("school-math", "student"),),
    )
    with session_factory() as session:
        before = session.scalar(select(func.count(Document.id)))

    def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unauthorized request reached storage: {request.url}")

    response = api_request(
        session_factory, settings, "POST", "/documents",
        token=token_for(student, settings), handler=unexpected,
        data={"school_id": "school-math"},
        files={"file": ("forbidden.txt", b"content", "text/plain")},
    )
    with session_factory() as session:
        after = session.scalar(select(func.count(Document.id)))
    assert response.status_code == 403
    assert before == after


def test_professor_upload_uses_authenticated_owner_and_school_scope(auth_catalog) -> None:
    session_factory, settings = auth_catalog
    professor = create_user(
        session_factory,
        email=f"owner-{uuid4()}@example.edu",
        memberships=(("school-math", "professor"), ("school-phy", "student")),
    )
    token = token_for(professor, settings)
    handler = replicated_storage_handler()
    allowed = api_request(
        session_factory, settings, "POST", "/documents", token=token, handler=handler,
        data={"school_id": "school-math"},
        files={"file": ("owned.txt", b"owned content", "text/plain")},
    )
    denied = api_request(
        session_factory, settings, "POST", "/documents", token=token, handler=handler,
        data={"school_id": "school-phy"},
        files={"file": ("denied.txt", b"denied content", "text/plain")},
    )
    document_id = allowed.json()["document_id"]
    student = create_user(
        session_factory,
        email=f"cross-school-student-{uuid4()}@example.edu",
        memberships=(("school-bio", "student"),),
    )
    downloaded = api_request(
        session_factory, settings, "GET",
        f"/documents/school-math/{document_id}/download",
        token=token_for(student, settings), handler=handler,
    )
    outsider = create_user(
        session_factory,
        email=f"outside-owner-{uuid4()}@example.edu",
        memberships=(("school-math", "professor"),),
    )
    forbidden_update = api_request(
        session_factory, settings, "PATCH", f"/documents/{document_id}",
        token=token_for(outsider, settings), handler=handler,
        json={"title": "Stolen title"},
    )
    owner_update = api_request(
        session_factory, settings, "PATCH", f"/documents/{document_id}",
        token=token, handler=handler, json={"title": "Updated by owner"},
    )
    new_version = api_request(
        session_factory, settings, "POST", f"/documents/{document_id}/versions",
        token=token, handler=handler,
        files={"file": ("owned-v2.txt", b"owned version two", "text/plain")},
    )
    assert allowed.status_code == 201 and denied.status_code == 403
    assert downloaded.status_code == 200 and downloaded.content == b"owned content"
    assert forbidden_update.status_code == 403
    assert owner_update.status_code == 200
    assert new_version.status_code == 201 and new_version.json()["version_number"] == 2
    with session_factory() as session:
        document = session.get(Document, UUID(allowed.json()["document_id"]))
        assert document.owner_id == professor.id
        assert document.created_by == professor.id
        assert document.school_id == "school-math"


def test_school_admin_scope_and_admin_without_professor_cannot_upload(auth_catalog) -> None:
    session_factory, settings = auth_catalog
    admin = create_user(
        session_factory,
        email=f"school-admin-{uuid4()}@example.edu",
        memberships=(("school-math", "school_admin"),),
    )
    token = token_for(admin, settings)
    created = api_request(
        session_factory, settings, "POST", "/users", token=token,
        json={
            "first_name": "New", "last_name": "Student",
            "email": f"managed-{uuid4()}@example.edu",
            "temporary_password": PASSWORD,
            "memberships": [{"school_id": "school-math", "role": "student"}],
        },
    )
    escalated = api_request(
        session_factory, settings, "POST", "/users", token=token,
        json={
            "first_name": "Bad", "last_name": "Admin",
            "email": f"escalation-{uuid4()}@example.edu",
            "temporary_password": PASSWORD, "global_role": "super_admin",
        },
    )
    outside = api_request(
        session_factory, settings, "POST", "/users", token=token,
        json={
            "first_name": "Other", "last_name": "School",
            "email": f"outside-{uuid4()}@example.edu",
            "temporary_password": PASSWORD,
            "memberships": [{"school_id": "school-phy", "role": "student"}],
        },
    )
    upload = api_request(
        session_factory, settings, "POST", "/documents", token=token,
        data={"school_id": "school-math"},
        files={"file": ("admin.txt", b"content", "text/plain")},
    )
    assert created.status_code == 201
    assert escalated.status_code == outside.status_code == upload.status_code == 403


def test_membership_revocation_takes_effect_with_existing_jwt(auth_catalog) -> None:
    session_factory, settings = auth_catalog
    professor = create_user(
        session_factory,
        email=f"revoked-{uuid4()}@example.edu",
        memberships=(("school-math", "professor"),),
    )
    token = token_for(professor, settings)
    with session_factory.begin() as session:
        membership = session.scalar(
            select(SchoolMembership).where(
                SchoolMembership.user_id == professor.id,
                SchoolMembership.role == "professor",
            )
        )
        session.delete(membership)
    denied = api_request(
        session_factory, settings, "POST", "/documents", token=token,
        data={"school_id": "school-math"},
        files={"file": ("revoked.txt", b"content", "text/plain")},
    )
    assert denied.status_code == 403


def test_super_admin_creation_normalizes_email_and_deactivation_revokes_jwt(auth_catalog) -> None:
    session_factory, settings = auth_catalog
    admin = create_user(
        session_factory,
        email=f"manager-{uuid4()}@example.edu",
        global_role="super_admin",
    )
    admin_token = token_for(admin, settings)
    mixed_email = f"Mixed-{uuid4()}@Example.EDU"
    payload = {
        "first_name": "Managed", "last_name": "User",
        "email": mixed_email, "temporary_password": PASSWORD,
        "memberships": [{"school_id": "school-bio", "role": "student"}],
    }
    created = api_request(
        session_factory, settings, "POST", "/users", token=admin_token, json=payload
    )
    duplicate = api_request(
        session_factory, settings, "POST", "/users", token=admin_token,
        json={**payload, "email": mixed_email.upper()},
    )
    assert created.status_code == 201
    assert created.json()["email"] == mixed_email.lower()
    assert duplicate.status_code == 409
    managed_id = created.json()["id"]
    login = api_request(
        session_factory, settings, "POST", "/auth/login",
        json={"email": mixed_email, "password": PASSWORD},
    )
    managed_token = login.json()["access_token"]
    deactivated = api_request(
        session_factory, settings, "POST", f"/users/{managed_id}/deactivate",
        token=admin_token,
    )
    assert deactivated.status_code == 200
    assert api_request(
        session_factory, settings, "GET", "/auth/me", token=managed_token
    ).status_code == 401


def test_last_active_super_admin_cannot_be_deactivated(auth_catalog) -> None:
    session_factory, settings = auth_catalog
    with session_factory.begin() as session:
        for user in session.scalars(
            select(User).where(User.global_role == "super_admin", User.is_active.is_(True))
        ).all():
            user.is_active = False
    admin = create_user(
        session_factory,
        email=f"last-admin-{uuid4()}@example.edu",
        global_role="super_admin",
    )
    response = api_request(
        session_factory, settings, "POST", f"/users/{admin.id}/deactivate",
        token=token_for(admin, settings),
    )
    assert response.status_code == 409
