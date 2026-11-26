"""PostgreSQL integration tests; require an explicitly isolated test database."""

import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from backend.app.database import create_session_factory, get_database_url
from backend.app.models import Base, PhysicalHost, School, StorageNode
from backend.app.physical_catalog import configure, runtime_urls, url_key
from backend.app.seed import NODES, SCHOOLS

EXPECTED_TABLES = {
    "schools",
    "users",
    "school_memberships",
    "documents",
    "document_subjects",
    "document_types",
    "document_versions",
    "physical_hosts",
    "storage_nodes",
    "subjects",
    "document_replicas",
    "reconciliation_jobs",
    "reconciliation_attempts",
    "security_audit_events",
}


def test_model_metadata_declares_the_fourteen_catalog_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_database_helper_requires_explicit_psycopg_url(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        get_database_url()

    monkeypatch.setenv("DATABASE_URL", "sqlite:///local.db")
    with pytest.raises(ValueError, match="postgresql\\+psycopg"):
        get_database_url()


@pytest.fixture(scope="module")
def database_engine():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    database_name = database_url.rsplit("/", maxsplit=1)[-1].split("?", maxsplit=1)[0]
    if not database_name.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must target a database whose name ends in _test")

    os.environ["DATABASE_URL"] = database_url
    backend_dir = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(backend_dir / "alembic.ini"))
    command.upgrade(alembic_config, "head")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE security_audit_events, reconciliation_attempts, "
                "reconciliation_jobs, document_replicas, document_subjects, "
                "document_versions, documents, subjects, school_memberships, "
                "storage_nodes, physical_hosts, document_types, users, schools "
                "CASCADE"
            )
        )
    try:
        yield engine
    finally:
        engine.dispose()


def expect_integrity_error(database_engine, statement: str, parameters: dict) -> None:
    with pytest.raises(IntegrityError):
        with database_engine.begin() as connection:
            connection.execute(text(statement), parameters)


def test_physical_catalog_preview_and_idempotent_apply(database_engine, monkeypatch) -> None:
    addresses = {
        "raspberry-1": "192.0.2.1",
        "raspberry-2": "192.0.2.2",
        "raspberry-3": "192.0.2.3",
        "raspberry-4": "192.0.2.4",
    }
    with create_session_factory(database_engine)() as session:
        session.add_all(School(**school) for school in SCHOOLS)
        session.add_all(
            PhysicalHost(id=host, hostname=host, device_type="raspberry_pi")
            for host in addresses
        )
        session.flush()
        session.add_all(StorageNode(**node) for node in NODES)
        session.flush()
        for node in NODES:
            monkeypatch.setenv(
                url_key(node["id"]),
                f'http://{addresses[node["physical_host_id"]]}:{node["port"]}',
            )
        configure(session, apply=False, verified=False)
        assert session.get(PhysicalHost, "raspberry-4").ip_address is None
        configure(session, apply=True, verified=False)
        configure(session, apply=True, verified=False)
        assert runtime_urls(session)["NODE_AGRO_URL"] == "http://192.0.2.4:8006"
        assert session.get(StorageNode, "node-agro").physical_host_id == "raspberry-4"
        assert session.get(PhysicalHost, "raspberry-4").hostname == "IdeaPad-5"


@pytest.fixture(scope="module")
def catalog_ids(database_engine):
    user_id = uuid4()
    other_user_id = uuid4()
    document_id = uuid4()
    other_document_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO schools (id, code, name) VALUES ('school-test', 'TEST', 'Test School')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users (id, full_name, email, password_hash) VALUES (:id, 'Test User', 'user@example.edu', 'hash')"
            ),
            {"id": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO users (id, full_name, email, password_hash) VALUES (:id, 'Other User', 'other@example.edu', 'hash')"
            ),
            {"id": other_user_id},
        )
        for value in (document_id, other_document_id):
            connection.execute(
                text(
                    "INSERT INTO documents (id, title, school_id, created_by) VALUES (:id, 'Document', 'school-test', :user_id)"
                ),
                {"id": value, "user_id": user_id},
            )
    return {
        "user": user_id,
        "other_user": other_user_id,
        "document": document_id,
        "other_document": other_document_id,
    }


def test_migration_creates_the_eleven_catalog_tables(database_engine) -> None:
    tables = set(inspect(database_engine).get_table_names())
    assert EXPECTED_TABLES <= tables


def test_case_insensitive_email_uniqueness(database_engine, catalog_ids) -> None:
    expect_integrity_error(
        database_engine,
        "INSERT INTO users (id, full_name, email, password_hash) VALUES (:id, 'Duplicate', 'USER@EXAMPLE.EDU', 'hash')",
        {"id": uuid4()},
    )


def test_user_can_hold_multiple_unique_roles_in_the_same_school(
    database_engine, catalog_ids
) -> None:
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO school_memberships (id, user_id, school_id, role) "
                "VALUES (:first, :user, 'school-test', 'student'), "
                "(:second, :user, 'school-test', 'professor')"
            ),
            {"first": uuid4(), "second": uuid4(), **catalog_ids},
        )
    expect_integrity_error(
        database_engine,
        "INSERT INTO school_memberships (id, user_id, school_id, role) "
        "VALUES (:id, :user, 'school-test', 'professor')",
        {"id": uuid4(), **catalog_ids},
    )


def test_legacy_document_owner_is_nullable_and_security_audit_is_persistent(
    database_engine, catalog_ids
) -> None:
    event_id = uuid4()
    with database_engine.begin() as connection:
        owner = connection.execute(
            text("SELECT owner_id FROM documents WHERE id = :document"), catalog_ids
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO security_audit_events "
                "(id, actor_user_id, action, resource_type, resource_id, outcome) "
                "VALUES (:id, :user, 'test_action', 'user', :resource, 'success')"
            ),
            {"id": event_id, "resource": str(catalog_ids["user"]), **catalog_ids},
        )
        stored = connection.execute(
            text("SELECT action FROM security_audit_events WHERE id = :id"),
            {"id": event_id},
        ).scalar_one()
    assert owner is None
    assert stored == "test_action"


@pytest.mark.parametrize(
    ("statement", "parameters"),
    [
        (
            "INSERT INTO school_memberships (id, user_id, school_id, role) VALUES (:id, :user, 'school-test', 'owner')",
            {},
        ),
        (
            "INSERT INTO document_versions (id, document_id, version_number, filename, mime_type, size_bytes, sha256, created_by) "
            "VALUES (:id, :document, 0, 'a.pdf', 'application/pdf', 1, :sha, :user)",
            {"sha": "a" * 64},
        ),
        (
            "INSERT INTO document_versions (id, document_id, version_number, filename, mime_type, size_bytes, sha256, created_by) "
            "VALUES (:id, :document, 1, 'a.pdf', 'application/pdf', 1, 'not-a-checksum', :user)",
            {},
        ),
        (
            "UPDATE documents SET deleted_at = now() WHERE id = :document",
            {},
        ),
    ],
    ids=[
        "invalid-role",
        "nonpositive-version",
        "invalid-sha256",
        "incomplete-soft-delete",
    ],
)
def test_check_constraints(database_engine, catalog_ids, statement, parameters) -> None:
    expect_integrity_error(
        database_engine,
        statement,
        {"id": uuid4(), **catalog_ids, **parameters},
    )


def test_current_version_must_belong_to_same_document(
    database_engine, catalog_ids
) -> None:
    version_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO document_versions "
                "(id, document_id, version_number, filename, mime_type, size_bytes, sha256, created_by) "
                "VALUES (:id, :document, 1, 'a.pdf', 'application/pdf', 1, :sha, :user)"
            ),
            {"id": version_id, "sha": "a" * 64, **catalog_ids},
        )
    expect_integrity_error(
        database_engine,
        "UPDATE documents SET current_version_id = :version WHERE id = :other_document",
        {"version": version_id, **catalog_ids},
    )


def test_replica_is_unique_per_physical_host(database_engine, catalog_ids) -> None:
    version_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO physical_hosts (id, hostname, device_type) VALUES ('host-test', 'host-test', 'test')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO storage_nodes (id, physical_host_id, port) VALUES ('node-one', 'host-test', 8001), ('node-two', 'host-test', 8002)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO document_versions "
                "(id, document_id, version_number, filename, mime_type, size_bytes, sha256, created_by) "
                "VALUES (:id, :document, 2, 'b.pdf', 'application/pdf', 1, :sha, :user)"
            ),
            {"id": version_id, "sha": "b" * 64, **catalog_ids},
        )
        connection.execute(
            text(
                "INSERT INTO document_replicas (id, version_id, node_id, physical_host_id) VALUES (:id, :version, 'node-one', 'host-test')"
            ),
            {"id": uuid4(), "version": version_id},
        )
    expect_integrity_error(
        database_engine,
        "INSERT INTO document_replicas (id, version_id, node_id, physical_host_id) VALUES (:id, :version, 'node-two', 'host-test')",
        {"id": uuid4(), "version": version_id},
    )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE storage_nodes SET health = 'degraded' WHERE id = 'node-one'",
        "UPDATE document_versions SET status = 'unknown' WHERE version_number = 2",
        "UPDATE document_replicas SET status = 'healthy' WHERE node_id = 'node-one'",
    ],
)
def test_state_and_healthy_replica_constraints(database_engine, statement) -> None:
    expect_integrity_error(database_engine, statement, {})
