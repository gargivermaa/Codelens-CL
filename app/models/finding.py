from datetime import datetime

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.database import Base


class Finding(Base):
    """One validated review finding (see app/review/schemas.py ReviewFinding)."""

    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("review_id", "fingerprint", name="uq_findings_review_fingerprint"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Stable hash of file + category + lines + title: dedup key within a review.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)     # bug|security|quality|...
    severity: Mapped[str] = mapped_column(String, nullable=False)     # critical..info
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # "confirmed" = evidence verified in the changed lines; "possible" = a concern
    # that could not be fully verified (never posted as an inline comment).
    status: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)       # llm:bug, rule:SEC001, ...

    file_path: Mapped[str] = mapped_column(String, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    comment_line: Mapped[int | None] = mapped_column(Integer, nullable=True)

    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_refs: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON list
    posted_inline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                server_default="false")

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    review: Mapped["Review"] = relationship(back_populates="findings")  # noqa: F821
