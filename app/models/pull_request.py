from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (
        # One row per (repo, PR number): repeated webhooks update, never duplicate.
        UniqueConstraint("repo_id", "pr_number", name="uq_pull_requests_repo_pr"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # FK to repositories.id — if the repo row is deleted, all its PRs go with it
    repo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )

    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    author: Mapped[str | None] = mapped_column(String, nullable=True)

    # Latest head commit seen for this PR.
    head_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # State machine: pending → processing → completed | failed
    # (status of the latest review attempt; per-commit detail lives in reviews).
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
