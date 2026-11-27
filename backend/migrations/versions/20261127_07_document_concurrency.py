"""Persist metadata revisions and publication cancellations."""

from alembic import op
import sqlalchemy as sa

revision = "20261127_07"
down_revision = "20261124_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("metadata_revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("document_versions", sa.Column("publication_cancelled", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("document_versions", "publication_cancelled")
    op.drop_column("documents", "metadata_revision")
