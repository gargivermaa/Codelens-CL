from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class ReviewFile(Base):
    """A file changed in the reviewed commit, with its diff (powers the diff viewer)."""

    __tablename__ = "review_files"
    __table_args__ = (UniqueConstraint("review_id", "path", name="uq_review_files_review_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    path: Mapped[str] = mapped_column(String, nullable=False)
    previous_path: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Unified diff as returned by GitHub (possibly cut at a hunk boundary).
    patch: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Why the file was not reviewed (None = reviewed).
    skip_reason: Mapped[str | None] = mapped_column(String, nullable=True)
