"""Add user authentication, multi-role memberships, ownership and audit."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261124_06"
down_revision: str | None = "20261123_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("first_name", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("last_name", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("global_role", sa.String(30), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "token_version", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_user_global_role", "users", "global_role IS NULL OR global_role = 'super_admin'"
    )
    op.create_check_constraint(
        "ck_user_token_version", "users", "token_version >= 0"
    )

    op.drop_constraint(
        "uq_membership_user_school", "school_memberships", type_="unique"
    )
    op.drop_constraint("ck_membership_role", "school_memberships", type_="check")
    op.drop_constraint(
        "ck_membership_affiliation", "school_memberships", type_="check"
    )
    op.execute(
        """
        UPDATE school_memberships
        SET role = CASE
            WHEN role = 'school_admin' THEN 'school_admin'
            WHEN affiliation = 'professor' OR role = 'contributor' THEN 'professor'
            ELSE 'student'
        END
        """
    )
    op.add_column(
        "school_memberships",
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_membership_role",
        "school_memberships",
        "role IN ('student', 'professor', 'school_admin')",
    )
    op.create_unique_constraint(
        "uq_membership_user_school_role",
        "school_memberships",
        ["user_id", "school_id", "role"],
    )

    op.add_column(
        "documents",
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])

    op.create_table(
        "security_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'denied', 'failed')",
            name="ck_security_audit_outcome",
        ),
    )
    op.create_index(
        "ix_security_audit_actor_created",
        "security_audit_events",
        ["actor_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_security_audit_actor_created", table_name="security_audit_events")
    op.drop_table("security_audit_events")
    op.drop_index("ix_documents_owner_id", table_name="documents")
    op.drop_column("documents", "owner_id")
    op.drop_constraint(
        "uq_membership_user_school_role", "school_memberships", type_="unique"
    )
    op.drop_constraint("ck_membership_role", "school_memberships", type_="check")
    op.drop_column("school_memberships", "is_active")
    op.execute(
        """
        UPDATE school_memberships
        SET affiliation = CASE role
            WHEN 'professor' THEN 'professor'
            WHEN 'student' THEN 'student'
            ELSE 'staff'
        END,
        role = CASE role
            WHEN 'professor' THEN 'contributor'
            WHEN 'school_admin' THEN 'school_admin'
            ELSE 'member'
        END
        """
    )
    op.create_check_constraint(
        "ck_membership_role",
        "school_memberships",
        "role IN ('member', 'contributor', 'school_admin')",
    )
    op.create_check_constraint(
        "ck_membership_affiliation",
        "school_memberships",
        "affiliation IS NULL OR affiliation IN ('student', 'professor', 'researcher', 'staff')",
    )
    op.create_unique_constraint(
        "uq_membership_user_school", "school_memberships", ["user_id", "school_id"]
    )
    op.drop_constraint("ck_user_token_version", "users", type_="check")
    op.drop_constraint("ck_user_global_role", "users", type_="check")
    op.drop_column("users", "token_version")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "global_role")
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")
