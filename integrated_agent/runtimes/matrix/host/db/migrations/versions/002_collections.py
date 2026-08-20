"""collections and collection items

Revision ID: 002_collections
Revises: 001_identity
Create Date: 2026-08-17
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "002_collections"
down_revision: Union[str, Sequence[str], None] = "001_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "collections" not in tables:
        op.create_table(
            "collections",
            sa.Column("collection_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.String(length=255), nullable=False),
            sa.Column("updated_at", sa.String(length=255), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("collection_id"),
            sa.UniqueConstraint("user_id", "name", name="uq_collections_user_name"),
        )
        op.create_index(
            "idx_collections_user",
            "collections",
            ["user_id", "created_at"],
            unique=False,
        )
    if "collection_items" not in tables:
        op.create_table(
            "collection_items",
            sa.Column("item_id", sa.String(length=64), nullable=False),
            sa.Column("collection_id", sa.String(length=64), nullable=False),
            sa.Column("parent_item_id", sa.String(length=64), nullable=True),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("extra_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.String(length=255), nullable=False),
            sa.ForeignKeyConstraint(
                ["collection_id"],
                ["collections.collection_id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["parent_item_id"],
                ["collection_items.item_id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("item_id"),
        )
        op.create_index(
            "idx_collection_items_folder",
            "collection_items",
            ["collection_id", "parent_item_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "collection_items" in tables:
        op.drop_index("idx_collection_items_folder", table_name="collection_items")
        op.drop_table("collection_items")
    if "collections" in tables:
        op.drop_index("idx_collections_user", table_name="collections")
        op.drop_table("collections")
