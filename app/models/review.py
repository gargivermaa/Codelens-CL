from datetime import datetime

from sqlalchemy import (BigInteger, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.database import Base

# Review lifecycle (one row per PR head commit):
#   pending  -> running -> analyzed -> completed
#                       \-> failed            (can be claimed again on redelivery)
# "analyzed" = findings stored but not yet posted to GitHub; a retry only posts,
# it never re-runs (and re-pays for) the LLM analysis.
REVIEW_STATUSES = ("pending", "running", "analyzed", "completed", "failed")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        # Idempotency: a PR commit is reviewed at most once, however many
        # webhook deliveries arrive for it. (NULL head_sha rows are legacy.)
        UniqueConstraint("pr_id", "head_sha", name="uq_reviews_pr_head_sha"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    pr_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )

    head_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    base_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending",
                                        server_default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # 0.0–1.0 score + label (low/medium/high/critical) from app/review/risk.py.
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_label: Mapped[str | None] = mapped_column(String, nullable=True)
    # JSON: the inputs and per-factor contributions behind risk_score.
    risk_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)

    issues_found: Mapped[int | None] = mapped_column(Integer, nullable=True)
    files_reviewed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # total_latency_ms = task start -> review posted (or stored, if posting is off).
    total_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Sum of reported LLM + embedding tokens. NULL when the provider did not
    # report usage — never estimated.
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Free tier only: 0.0. Kept so a cost regression would be visible.
    total_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    model: Mapped[str | None] = mapped_column(String, nullable=True)
    retrieval_mode: Mapped[str | None] = mapped_column(String, nullable=True)

    # Markdown summary posted to GitHub, and the full structured pipeline output.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    # GitHub ids are > 2^31: BigInteger.
    github_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    github_review_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    findings: Mapped[list["Finding"]] = relationship(  # noqa: F821
        back_populates="review", cascade="all, delete-orphan", passive_deletes=True,
        order_by="Finding.id",
    )
