"""initial schema: tools + tool_stats

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-16
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tools",
        sa.Column("id", sa.String(length=255), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.String(length=50), nullable=False, server_default="1.0.0"),
        sa.Column("author", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=100), nullable=False, server_default="general"),
        sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("spec_json", sa.Text(), nullable=False),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_tools_name", "tools", ["name"])
    op.create_index("ix_tools_category", "tools", ["category"])

    op.create_table(
        "tool_stats",
        sa.Column("tool_id", sa.String(length=255), primary_key=True, nullable=False),
        sa.Column("execution_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_duration_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=20), nullable=False, server_default="never"),
        sa.Column("favorite_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("tool_stats")
    op.drop_index("ix_tools_category", table_name="tools")
    op.drop_index("ix_tools_name", table_name="tools")
    op.drop_table("tools")
