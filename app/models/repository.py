from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # GitHub's own numeric ID for the repo — unique, stable, never changes
    # even if the repo is renamed. Used to de-duplicate installations.
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)

    # "owner/repo-name" — human-readable, used in logs and GitHub API calls
    full_name: Mapped[str] = mapped_column(String, nullable=False)

    # Populated on Day 15+ when GitHub App installation flow is built.
    # Nullable now because we may insert repo rows before the App is registered.
    installation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Set when the repo's codebase is first indexed into Qdrant (Day 8-9).
    # Null means "not yet indexed" — the worker checks this before deciding
    # whether to re-chunk the whole repo or skip straight to retrieval.
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )