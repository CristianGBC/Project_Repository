"""Add durable timing fields required by automatic replica repair."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261123_04"
down_revision: str | None = "20261116_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "storage_nodes",
        sa.Column(
            "health_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "document_replicas",
        sa.Column("repair_after", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_replicas", "repair_after")
    op.drop_column("storage_nodes", "health_changed_at")
