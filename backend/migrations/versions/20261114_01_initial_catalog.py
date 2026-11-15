"""Create the initial eight-table PostgreSQL catalog."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261114_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> tuple[sa.Column, ...]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def upgrade() -> None:
    op.create_table(
        "schools",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("code", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(150), nullable=False, unique=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        *timestamps(),
    )
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("full_name", sa.String(150), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        *timestamps(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True
    )
    op.create_table(
        "physical_hosts",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("hostname", sa.String(100), nullable=False, unique=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("device_type", sa.String(30), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        *timestamps(),
    )
    op.create_table(
        "school_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "school_id", sa.String(30), sa.ForeignKey("schools.id"), nullable=False
        ),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("affiliation", sa.String(30), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("user_id", "school_id", name="uq_membership_user_school"),
        sa.CheckConstraint(
            "role IN ('member', 'contributor', 'school_admin')",
            name="ck_membership_role",
        ),
        sa.CheckConstraint(
            "affiliation IS NULL OR affiliation IN ('student', 'professor', 'researcher', 'staff')",
            name="ck_membership_affiliation",
        ),
    )
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "school_id", sa.String(30), sa.ForeignKey("schools.id"), nullable=False
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "visibility",
            sa.String(20),
            server_default=sa.text("'school'"),
            nullable=False,
        ),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        *timestamps(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "deleted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "visibility IN ('school', 'institution')", name="ck_document_visibility"
        ),
        sa.CheckConstraint(
            "(deleted_at IS NULL AND deleted_by IS NULL) OR "
            "(deleted_at IS NOT NULL AND deleted_by IS NOT NULL)",
            name="ck_document_soft_delete",
        ),
    )
    op.create_table(
        "storage_nodes",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column(
            "home_school_id", sa.String(30), sa.ForeignKey("schools.id"), nullable=True
        ),
        sa.Column(
            "physical_host_id",
            sa.String(30),
            sa.ForeignKey("physical_hosts.id"),
            nullable=False,
        ),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "health", sa.String(20), server_default=sa.text("'unknown'"), nullable=False
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("home_school_id", name="uq_storage_node_home_school"),
        sa.UniqueConstraint(
            "physical_host_id", "port", name="uq_storage_node_host_port"
        ),
        sa.UniqueConstraint("id", "physical_host_id", name="uq_storage_node_id_host"),
        sa.CheckConstraint(
            "health IN ('up', 'down', 'unknown')", name="ck_storage_node_health"
        ),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name="ck_storage_node_port"),
    )
    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column(
            "status", sa.String(20), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column(
            "target_replicas",
            sa.SmallInteger(),
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        *timestamps(),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_description", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "document_id", "version_number", name="uq_version_document_number"
        ),
        sa.UniqueConstraint("id", "document_id", name="uq_version_id_document"),
        sa.CheckConstraint("version_number > 0", name="ck_version_number_positive"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_version_size_nonnegative"),
        sa.CheckConstraint(
            "target_replicas > 0", name="ck_version_target_replicas_positive"
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9A-Fa-f]{64}$'", name="ck_version_sha256"),
        sa.CheckConstraint(
            "status IN ('pending', 'available', 'failed')", name="ck_version_status"
        ),
    )
    op.create_foreign_key(
        "fk_document_current_version",
        "documents",
        "document_versions",
        ["current_version_id", "id"],
        ["id", "document_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "document_replicas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(30), nullable=False),
        sa.Column("physical_host_id", sa.String(30), nullable=False),
        sa.Column(
            "status", sa.String(20), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        *timestamps(),
        sa.Column("stored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "version_id", "physical_host_id", name="uq_replica_version_host"
        ),
        sa.UniqueConstraint("version_id", "node_id", name="uq_replica_version_node"),
        sa.ForeignKeyConstraint(
            ["node_id", "physical_host_id"],
            ["storage_nodes.id", "storage_nodes.physical_host_id"],
            name="fk_replica_storage_node_host",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'healthy', 'corrupted', 'missing', 'failed')",
            name="ck_replica_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_replica_attempts_nonnegative"),
        sa.CheckConstraint(
            "status <> 'healthy' OR stored_at IS NOT NULL",
            name="ck_replica_healthy_stored",
        ),
    )


def downgrade() -> None:
    op.drop_table("document_replicas")
    op.drop_constraint("fk_document_current_version", "documents", type_="foreignkey")
    op.drop_table("document_versions")
    op.drop_table("storage_nodes")
    op.drop_table("documents")
    op.drop_table("school_memberships")
    op.drop_table("physical_hosts")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")
    op.drop_table("schools")
