"""SQLAlchemy 2.x models for the PostgreSQL institutional catalog."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base


timestamp = DateTime(timezone=True)


class School(Base):
    __tablename__ = "schools"

    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(timestamp, server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_email_lower", func.lower(text("email")), unique=True),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    full_name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(timestamp, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(timestamp, server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(timestamp)


class SchoolMembership(Base):
    __tablename__ = "school_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "school_id", name="uq_membership_user_school"),
        CheckConstraint(
            "role IN ('member', 'contributor', 'school_admin')",
            name="ck_membership_role",
        ),
        CheckConstraint(
            "affiliation IS NULL OR affiliation IN ('student', 'professor', 'researcher', 'staff')",
            name="ck_membership_affiliation",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    school_id: Mapped[str] = mapped_column(ForeignKey("schools.id"))
    role: Mapped[str] = mapped_column(String(30))
    affiliation: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(timestamp, server_default=func.now())

    user: Mapped[User] = relationship()
    school: Mapped[School] = relationship()


class DocumentType(Base):
    __tablename__ = "document_types"

    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    requires_subject: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(timestamp, server_default=func.now())


document_subjects = Table(
    "document_subjects",
    Base.metadata,
    Column(
        "document_id",
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("documents.id"),
        primary_key=True,
    ),
    Column(
        "subject_id",
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("subjects.id"),
        primary_key=True,
    ),
    Index("ix_document_subjects_subject_id", "subject_id"),
)


class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (
        UniqueConstraint("school_id", "code", name="uq_subject_school_code"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    school_id: Mapped[str] = mapped_column(ForeignKey("schools.id"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(timestamp, server_default=func.now())

    school: Mapped[School] = relationship()
    documents: Mapped[list["Document"]] = relationship(
        secondary=document_subjects,
        back_populates="subjects",
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "visibility IN ('school', 'institution')", name="ck_document_visibility"
        ),
        CheckConstraint(
            "(deleted_at IS NULL AND deleted_by IS NULL) OR "
            "(deleted_at IS NOT NULL AND deleted_by IS NOT NULL)",
            name="ck_document_soft_delete",
        ),
        ForeignKeyConstraint(
            ["current_version_id", "id"],
            ["document_versions.id", "document_versions.document_id"],
            name="fk_document_current_version",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    school_id: Mapped[str] = mapped_column(ForeignKey("schools.id"))
    document_type_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_types.id"), index=True
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    visibility: Mapped[str] = mapped_column(String(20), server_default=text("'school'"))
    current_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    created_at: Mapped[datetime] = mapped_column(timestamp, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(timestamp, server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(timestamp)
    deleted_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))

    school: Mapped[School] = relationship()
    document_type: Mapped[DocumentType | None] = relationship()
    subjects: Mapped[list[Subject]] = relationship(
        secondary=document_subjects,
        back_populates="documents",
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "version_number", name="uq_version_document_number"
        ),
        UniqueConstraint("id", "document_id", name="uq_version_id_document"),
        CheckConstraint("version_number > 0", name="ck_version_number_positive"),
        CheckConstraint("size_bytes >= 0", name="ck_version_size_nonnegative"),
        CheckConstraint(
            "target_replicas > 0", name="ck_version_target_replicas_positive"
        ),
        CheckConstraint("sha256 ~ '^[0-9A-Fa-f]{64}$'", name="ck_version_sha256"),
        CheckConstraint(
            "status IN ('pending', 'available', 'failed')", name="ck_version_status"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"))
    version_number: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending'"))
    target_replicas: Mapped[int] = mapped_column(SmallInteger, server_default=text("3"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(timestamp, server_default=func.now())
    committed_at: Mapped[datetime | None] = mapped_column(timestamp)
    change_description: Mapped[str | None] = mapped_column(Text)


class PhysicalHost(Base):
    __tablename__ = "physical_hosts"

    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(100), unique=True)
    ip_address: Mapped[str | None] = mapped_column(INET)
    device_type: Mapped[str] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(timestamp, server_default=func.now())


class StorageNode(Base):
    __tablename__ = "storage_nodes"
    __table_args__ = (
        UniqueConstraint("home_school_id", name="uq_storage_node_home_school"),
        UniqueConstraint("physical_host_id", "port", name="uq_storage_node_host_port"),
        UniqueConstraint("id", "physical_host_id", name="uq_storage_node_id_host"),
        CheckConstraint(
            "health IN ('up', 'down', 'unknown')", name="ck_storage_node_health"
        ),
        CheckConstraint("port BETWEEN 1 AND 65535", name="ck_storage_node_port"),
    )

    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    home_school_id: Mapped[str | None] = mapped_column(ForeignKey("schools.id"))
    physical_host_id: Mapped[str] = mapped_column(ForeignKey("physical_hosts.id"))
    port: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    health: Mapped[str] = mapped_column(String(20), server_default=text("'unknown'"))
    last_seen_at: Mapped[datetime | None] = mapped_column(timestamp)
    health_changed_at: Mapped[datetime] = mapped_column(
        timestamp, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(timestamp, server_default=func.now())


class DocumentReplica(Base):
    __tablename__ = "document_replicas"
    __table_args__ = (
        UniqueConstraint(
            "version_id", "physical_host_id", name="uq_replica_version_host"
        ),
        UniqueConstraint("version_id", "node_id", name="uq_replica_version_node"),
        ForeignKeyConstraint(
            ["node_id", "physical_host_id"],
            ["storage_nodes.id", "storage_nodes.physical_host_id"],
            name="fk_replica_storage_node_host",
        ),
        CheckConstraint(
            "status IN ('pending', 'healthy', 'corrupted', 'missing', 'failed')",
            name="ck_replica_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_replica_attempts_nonnegative"),
        CheckConstraint(
            "status <> 'healthy' OR stored_at IS NOT NULL",
            name="ck_replica_healthy_stored",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id"))
    node_id: Mapped[str] = mapped_column(String(30))
    physical_host_id: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(timestamp, server_default=func.now())
    stored_at: Mapped[datetime | None] = mapped_column(timestamp)
    verified_at: Mapped[datetime | None] = mapped_column(timestamp)
    repair_after: Mapped[datetime | None] = mapped_column(timestamp)


class ReconciliationJob(Base):
    __tablename__ = "reconciliation_jobs"
    __table_args__ = (
        UniqueConstraint("version_id", name="uq_reconciliation_job_version"),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'retry_wait', 'completed', 'failed', 'cancelled')",
            name="ck_reconciliation_job_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_reconciliation_job_attempts"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id"))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(timestamp)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(timestamp, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(timestamp, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(timestamp)


class ReconciliationAttempt(Base):
    __tablename__ = "reconciliation_attempts"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "attempt_number", name="uq_reconciliation_attempt_number"
        ),
        CheckConstraint("attempt_number > 0", name="ck_reconciliation_attempt_positive"),
        CheckConstraint(
            "outcome IN ('in_progress', 'completed', 'retry', 'failed', 'cancelled')",
            name="ck_reconciliation_attempt_outcome",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    job_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_jobs.id"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(timestamp, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(timestamp)
    outcome: Mapped[str] = mapped_column(
        String(20), server_default=text("'in_progress'")
    )
    destination_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("storage_nodes.id")
    )
    replica_snapshot: Mapped[dict] = mapped_column(JSONB)
    detail: Mapped[str | None] = mapped_column(Text)
