"""Add durable upload-reconciliation jobs and attempt audit."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261123_05"
down_revision: str | None = "20261123_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(20), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("version_id", name="uq_reconciliation_job_version"),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'retry_wait', 'completed', 'failed', 'cancelled')",
            name="ck_reconciliation_job_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_reconciliation_job_attempts"),
    )
    op.create_table(
        "reconciliation_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reconciliation_jobs.id"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "outcome",
            sa.String(20),
            server_default=sa.text("'in_progress'"),
            nullable=False,
        ),
        sa.Column(
            "destination_node_id",
            sa.String(30),
            sa.ForeignKey("storage_nodes.id"),
            nullable=True,
        ),
        sa.Column("replica_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "job_id", "attempt_number", name="uq_reconciliation_attempt_number"
        ),
        sa.CheckConstraint(
            "attempt_number > 0", name="ck_reconciliation_attempt_positive"
        ),
        sa.CheckConstraint(
            "outcome IN ('in_progress', 'completed', 'retry', 'failed', 'cancelled')",
            name="ck_reconciliation_attempt_outcome",
        ),
    )


def downgrade() -> None:
    op.drop_table("reconciliation_attempts")
    op.drop_table("reconciliation_jobs")
