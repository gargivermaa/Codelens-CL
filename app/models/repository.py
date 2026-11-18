from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # GitHub's own numeric ID for the repo — unique, stable, never changes
    # even if the repo is renamed. Also names the Qdrant collection (repo_<id>).
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)

    # "owner/repo-name" — human-readable, used in logs and GitHub API calls
    full_name: Mapped[str] = mapped_column(String, nullable=False)

    # GitHub App installation that grants us access to this repository.
    installation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # When / at which commit the repository was last indexed into Qdrant.
    # The worker re-indexes (incrementally) only when indexed_sha != PR base sha.
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    indexed_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    indexed_chunks: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
