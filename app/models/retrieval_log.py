from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class RetrievalLog(Base):
    """
    Exists from Day 3 per Change 2/3 of the locked architecture.
    Nothing writes to this table until Day 10 when retrieve_context() is built.
    Sitting empty now is intentional — the schema is decided once so the
    locked metric definitions never change mid-build.

    Locked metric definitions (Change 2/3):
      - retrieval_latency_ms: time for the Qdrant query only, not the full pipeline
      - scores: comma-separated cosine similarity scores from Qdrant, one per chunk
      - token_cost: embedding tokens used to embed the query (not generation tokens)
    """

    __tablename__ = "retrieval_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Nullable — Day 10 manual test calls won't have a real PR row yet.
    # Once Day 10+ retrieval is wired into the Celery pipeline, this is always set.
    pr_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    # The query text sent to Qdrant — usually the text of a changed function
    query_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Comma-separated Qdrant point UUIDs of the returned chunks
    retrieved_chunk_ids: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Locked metric: raw cosine similarity scores, comma-separated, same order
    # as retrieved_chunk_ids. Stored as text to avoid a separate scores table.
    scores: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Locked metric: Qdrant query latency only in milliseconds
    retrieval_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Locked metric: tokens used to embed the query string
    token_cost: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )