"""dashboard schema: stored PR diffs, repository default branch

Revision ID: c41e8a7d5b62
Revises: b7d2e4f19a30
Create Date: 2026-11-18

Additive only.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c41e8a7d5b62"
down_revision: Union[str, None] = "b7d2e4f19a30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("repositories", sa.Column("default_branch", sa.String(), nullable=True))
    op.add_column("repositories", sa.Column("private", sa.Boolean(), nullable=True))
    op.create_table(
        "review_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("review_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("previous_path", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("additions", sa.Integer(), nullable=False),
        sa.Column("deletions", sa.Integer(), nullable=False),
        sa.Column("patch", sa.Text(), nullable=True),
        sa.Column("skip_reason", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_id", "path", name="uq_review_files_review_path"),
    )
    op.create_index("ix_review_files_review_id", "review_files", ["review_id"])


def downgrade() -> None:
    op.drop_index("ix_review_files_review_id", table_name="review_files")
    op.drop_table("review_files")
    op.drop_column("repositories", "private")
    op.drop_column("repositories", "default_branch")
