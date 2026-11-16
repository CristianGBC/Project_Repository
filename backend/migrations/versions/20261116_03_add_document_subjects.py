"""Add subjects and many-to-many document associations."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261116_03"
down_revision: str | None = "20261116_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_types",
        sa.Column(
            "requires_subject",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE document_types SET requires_subject = true "
        "WHERE id IN ('study_material', 'syllabus')"
    )
    op.create_table(
        "subjects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "school_id",
            sa.String(30),
            sa.ForeignKey("schools.id"),
            nullable=False,
        ),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("school_id", "code", name="uq_subject_school_code"),
    )
    op.create_index("ix_subjects_school_id", "subjects", ["school_id"])
    op.create_table(
        "document_subjects",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id"),
            primary_key=True,
        ),
        sa.Column(
            "subject_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("subjects.id"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_document_subjects_subject_id",
        "document_subjects",
        ["subject_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_subjects_subject_id",
        table_name="document_subjects",
    )
    op.drop_table("document_subjects")
    op.drop_index("ix_subjects_school_id", table_name="subjects")
    op.drop_table("subjects")
    op.drop_column("document_types", "requires_subject")
