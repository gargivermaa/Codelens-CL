from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class RetrievalLog(Base):
    """
    One row per retrieval call (evaluation data, collected from day one).

    Locked metric definitions:
      - retrieval_latency_ms: dense Qdrant query latency (incl. query embedding);
        the BM25 side is logged separately in bm25_latency_ms
      - scores: dense cosine similarity per returned chunk (empty when a chunk
        came from BM25 only), same order as retrieved_chunk_ids
      - token_cost: embedding tokens for the query if Gemini reported them, else NULL
    """

    __tablename__ = "retrieval_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Nullable: manual / CLI retrieval calls have no PR or review.
    pr_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reviews.id", ondelete="SET NULL"), nullable=True, index=True
    )
    repo_github_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    query_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy: Mapped[str | None] = mapped_column(String, nullable=True)  # hybrid|dense|bm25

    # Comma-separated Qdrant point UUIDs of the returned chunks
    retrieved_chunk_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    scores: Mapped[str | None] = mapped_column(Text, nullable=True)
    bm25_scores: Mapped[str | None] = mapped_column(Text, nullable=True)
    fused_scores: Mapped[str | None] = mapped_column(Text, nullable=True)

    retrieval_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bm25_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_cost: Mapped[int | None] = mapped_column(Integer, nullable=True)
    degraded_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
