"""review pipeline schema: review lifecycle, findings, retrieval log details

Revision ID: b7d2e4f19a30
Revises: 948c7ae88c69
Create Date: 2026-11-18

Additive only: no existing column is dropped or renamed. Integer -> BigInteger
widenings are safe in place on PostgreSQL.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2e4f19a30"
down_revision: Union[str, None] = "948c7ae88c69"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # repositories
    op.alter_column("repositories", "installation_id", existing_type=sa.Integer(),
                    type_=sa.BigInteger(), existing_nullable=True)
    op.add_column("repositories", sa.Column("indexed_sha", sa.String(40), nullable=True))
    op.add_column("repositories", sa.Column("indexed_chunks", sa.Integer(), nullable=True))

    # pull_requests
    op.add_column("pull_requests", sa.Column("head_sha", sa.String(40), nullable=True))

    # reviews
    op.add_column("reviews", sa.Column("head_sha", sa.String(40), nullable=True))
    op.add_column("reviews", sa.Column("base_sha", sa.String(40), nullable=True))
    op.add_column("reviews", sa.Column("status", sa.String(), nullable=False, server_default="pending"))
    op.add_column("reviews", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("reviews", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("reviews", sa.Column("risk_breakdown", sa.Text(), nullable=True))
    op.add_column("reviews", sa.Column("files_reviewed", sa.Integer(), nullable=True))
    op.add_column("reviews", sa.Column("model", sa.String(), nullable=True))
    op.add_column("reviews", sa.Column("retrieval_mode", sa.String(), nullable=True))
    op.add_column("reviews", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("reviews", sa.Column("github_review_id", sa.BigInteger(), nullable=True))
    op.add_column("reviews", sa.Column("started_at", sa.DateTime(), nullable=True))
    op.add_column("reviews", sa.Column("completed_at", sa.DateTime(), nullable=True))
    op.alter_column("reviews", "github_comment_id", existing_type=sa.Integer(),
                    type_=sa.BigInteger(), existing_nullable=True)
    op.create_unique_constraint("uq_reviews_pr_head_sha", "reviews", ["pr_id", "head_sha"])

    # findings
    op.create_table(
        "findings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("review_id", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("comment_line", sa.Integer(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("suggested_fix", sa.Text(), nullable=True),
        sa.Column("context_refs", sa.Text(), nullable=True),
        sa.Column("posted_inline", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_id", "fingerprint", name="uq_findings_review_fingerprint"),
    )
    op.create_index("ix_findings_review_id", "findings", ["review_id"])

    # retrieval_logs
    op.add_column("retrieval_logs", sa.Column("review_id", sa.Integer(), nullable=True))
    op.add_column("retrieval_logs", sa.Column("repo_github_id", sa.BigInteger(), nullable=True))
    op.add_column("retrieval_logs", sa.Column("strategy", sa.String(), nullable=True))
    op.add_column("retrieval_logs", sa.Column("bm25_scores", sa.Text(), nullable=True))
    op.add_column("retrieval_logs", sa.Column("fused_scores", sa.Text(), nullable=True))
    op.add_column("retrieval_logs", sa.Column("bm25_latency_ms", sa.Integer(), nullable=True))
    op.add_column("retrieval_logs", sa.Column("degraded_reason", sa.Text(), nullable=True))
    op.create_foreign_key("fk_retrieval_logs_review_id", "retrieval_logs", "reviews",
                          ["review_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_retrieval_logs_review_id", "retrieval_logs", ["review_id"])


def downgrade() -> None:
    op.drop_index("ix_retrieval_logs_review_id", table_name="retrieval_logs")
    op.drop_constraint("fk_retrieval_logs_review_id", "retrieval_logs", type_="foreignkey")
    for column in ("degraded_reason", "bm25_latency_ms", "fused_scores", "bm25_scores",
                   "strategy", "repo_github_id", "review_id"):
        op.drop_column("retrieval_logs", column)

    op.drop_index("ix_findings_review_id", table_name="findings")
    op.drop_table("findings")

    op.drop_constraint("uq_reviews_pr_head_sha", "reviews", type_="unique")
    op.alter_column("reviews", "github_comment_id", existing_type=sa.BigInteger(),
                    type_=sa.Integer(), existing_nullable=True)
    for column in ("completed_at", "started_at", "github_review_id", "summary", "retrieval_mode",
                   "model", "files_reviewed", "risk_breakdown", "attempts", "error", "status",
                   "base_sha", "head_sha"):
        op.drop_column("reviews", column)

    op.drop_column("pull_requests", "head_sha")

    op.drop_column("repositories", "indexed_chunks")
    op.drop_column("repositories", "indexed_sha")
    op.alter_column("repositories", "installation_id", existing_type=sa.BigInteger(),
                    type_=sa.Integer(), existing_nullable=True)
