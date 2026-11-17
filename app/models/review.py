from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    pr_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )

    # 0.0–1.0 score + human-readable label (low/medium/high/critical).
    # Calculated by the risk scorer (Day 13-14). Null until that day.
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_label: Mapped[str | None] = mapped_column(String, nullable=True)

    issues_found: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Locked metric definition (Change 2/3):
    # total_latency_ms = webhook receipt timestamp → GitHub comment posted timestamp.
    # Sub-latencies stored separately in retrieval_logs, never summed here.
    total_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Locked metric definition (Change 2/3):
    # Sum of embedding + generation token counts across the full review pipeline.
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Full structured LLM output stored as JSON string.
    # Keeping the raw output means we can re-parse it later with a better
    # formatter without re-running the expensive LLM call.
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ID of the comment we posted on the GitHub PR (Day 17-18).
    # Lets us edit the comment on PR updates instead of posting duplicates.
    github_comment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
