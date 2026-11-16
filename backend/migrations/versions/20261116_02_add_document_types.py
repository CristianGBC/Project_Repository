"""Add the document type catalog and optional document classification."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261116_02"
down_revision: str | None = "20261114_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_types",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("code", sa.String(30), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
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
    )
    op.add_column(
        "documents",
        sa.Column("document_type_id", sa.String(30), nullable=True),
    )
    op.create_foreign_key(
        "fk_documents_document_type",
        "documents",
        "document_types",
        ["document_type_id"],
        ["id"],
    )
    op.create_index(
        "ix_documents_document_type_id",
        "documents",
        ["document_type_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_document_type_id", table_name="documents")
    op.drop_constraint(
        "fk_documents_document_type",
        "documents",
        type_="foreignkey",
    )
    op.drop_column("documents", "document_type_id")
    op.drop_table("document_types")
