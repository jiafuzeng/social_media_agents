"""initial identity schema

Revision ID: 001_identity
Revises:
Create Date: 2026-08-17
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "001_identity"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("username", sa.String(), nullable=False),
            sa.Column("password_salt", sa.String(), nullable=False),
            sa.Column("password_hash", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
            sa.PrimaryKeyConstraint("user_id"),
            sa.UniqueConstraint("username"),
        )
    if "tokens" not in tables:
        op.create_table(
            "tokens",
            sa.Column("token_hash", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(
                ["user_id"], ["users.user_id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("token_hash"),
        )
        op.create_index("idx_tokens_user", "tokens", ["user_id"], unique=False)
    if "sessions" not in tables:
        op.create_table(
            "sessions",
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("last_scenario", sa.String(), nullable=True),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
            sa.Column("last_active_at", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(
                ["user_id"], ["users.user_id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("session_id"),
        )
        op.create_index(
            "idx_sessions_user",
            "sessions",
            ["user_id", "last_active_at"],
            unique=False,
        )
    if "session_turns" not in tables:
        op.create_table(
            "session_turns",
            sa.Column("turn_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("text", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=True),
            sa.Column("extra_json", sa.String(), nullable=True),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(
                ["session_id"],
                ["sessions.session_id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("turn_id"),
        )
        op.create_index(
            "idx_session_turns_session",
            "session_turns",
            ["session_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "session_turns" in tables:
        op.drop_index("idx_session_turns_session", table_name="session_turns")
        op.drop_table("session_turns")
    if "sessions" in tables:
        op.drop_index("idx_sessions_user", table_name="sessions")
        op.drop_table("sessions")
    if "tokens" in tables:
        op.drop_index("idx_tokens_user", table_name="tokens")
        op.drop_table("tokens")
    if "users" in tables:
        op.drop_table("users")
