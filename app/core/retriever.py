"""
Hybrid retrieval: Gemini dense vectors (Qdrant) + BM25 keywords, fused with
Reciprocal Rank Fusion (RRF).

Why RRF: dense cosine scores (0..1) and BM25 scores (unbounded) are on
different scales, so adding them needs fragile weight tuning. RRF uses only
each list's ranks: score(d) = sum over lists of 1 / (RRF_K + rank(d)).
A chunk that both retrievers rank highly wins; a chunk found by only one
retriever can still surface.

Isolation: one retriever instance reads exactly one repository collection
(repo_<github_id>), so context never crosses repositories.

Degradation is explicit: if the dense side fails (e.g. free-tier quota), the
result is BM25-only and says so (`degraded_reason`); nothing is faked.
"""
import argparse
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from qdrant_client import QdrantClient

from app.core.bm25 import BM25Index, tokenize
from app.core.embedder import EmbeddingError, collection_name, embed_query, get_qdrant_client

logger = logging.getLogger("codelens.retriever")

RRF_K = 60
MODES = ("hybrid", "dense", "bm25")


class RepositoryNotIndexedError(RuntimeError):
    pass


@dataclass
class RetrievedChunk:
    point_id: str
    payload: dict
    score: float                    # fused RRF score (or the single retriever's score)
    dense_score: float | None = None
    dense_rank: int | None = None
    bm25_score: float | None = None
    bm25_rank: int | None = None

    @property
    def location(self) -> str:
        p = self.payload
        return f"{p.get('file')}:{p.get('start_line')}-{p.get('end_line')}"


@dataclass
class RetrievalResult:
    query: str
    mode: str
    chunks: list[RetrievedChunk]
    dense_latency_ms: int | None = None
    bm25_latency_ms: int | None = None
    total_latency_ms: int = 0
    degraded_reason: str | None = None
    candidates: dict = field(default_factory=dict)


def bm25_document(payload: dict) -> list[str]:
    """Tokens BM25 indexes for a chunk. File path and qualified name are
    included so a query mentioning "auth/session.py" or "SessionStore" matches."""
    return tokenize(f"{payload.get('file', '')} {payload.get('qualified_name', '')} {payload.get('text', '')}")


def _unit_key(payload: dict) -> tuple:
    return payload.get("file"), payload.get("qualified_name"), payload.get("type")


class HybridRetriever:
    """Retriever bound to one repository's collection."""

    def __init__(self, repo_github_id: int, qdrant: QdrantClient | None = None, gemini_client=None):
        self.repo_github_id = repo_github_id
        self.collection = collection_name(repo_github_id)
        self.qdrant = qdrant or get_qdrant_client()
        self.gemini_client = gemini_client
        self._ids: list[str] | None = None
        self._payloads: list[dict] = []
        self._bm25: BM25Index | None = None

    # ---- corpus -----------------------------------------------------------
    def _load_corpus(self) -> None:
        if self._bm25 is not None:
            return
        if not self.qdrant.collection_exists(self.collection):
            raise RepositoryNotIndexedError(f"Repository {self.repo_github_id} is not indexed "
                                            f"(collection {self.collection} missing)")
        ids, payloads, offset = [], [], None
        while True:
            points, offset = self.qdrant.scroll(self.collection, limit=1000, offset=offset,
                                                with_payload=True, with_vectors=False)
            for point in points:
                ids.append(str(point.id))
                payloads.append(point.payload or {})
            if offset is None:
                break
        docs = [bm25_document(p) for p in payloads]
        self._ids, self._payloads, self._bm25 = ids, payloads, BM25Index(docs)

    def corpus_size(self) -> int:
        self._load_corpus()
        return len(self._ids or [])

    # ---- individual retrievers --------------------------------------------
    def _dense(self, query: str, limit: int) -> list[tuple[str, float, dict]]:
        vector = embed_query(query, client=self.gemini_client)
        response = self.qdrant.query_points(self.collection, query=vector, limit=limit, with_payload=True)
        return [(str(p.id), float(p.score), p.payload or {}) for p in response.points]

    def _bm25_search(self, query: str, limit: int) -> list[tuple[str, float, dict]]:
        self._load_corpus()
        scores = self._bm25.scores(tokenize(query))
        order = sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: (-scores[i], self._ids[i]))
        return [(self._ids[i], scores[i], self._payloads[i]) for i in order[:limit]]

    # ---- public API -------------------------------------------------------
    def search(self, query: str, top_k: int = 5, mode: str = "hybrid",
               exclude: Callable[[dict], bool] | None = None,
               candidate_k: int | None = None, collapse_parts: bool = True) -> RetrievalResult:
        """Return up to top_k relevant chunks for `query`.

        exclude(payload) -> True drops a chunk (e.g. the changed code itself).
        collapse_parts keeps only the best-ranked part of a split unit, so the
        context is not filled with five slices of the same function.
        """
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        started = time.perf_counter()
        candidate_k = candidate_k or max(20, top_k * 4)
        self._load_corpus()

        dense_hits: list[tuple[str, float, dict]] = []
        bm25_hits: list[tuple[str, float, dict]] = []
        dense_ms = bm25_ms = None
        degraded = None
        used_mode = mode

        if mode in ("hybrid", "dense"):
            t0 = time.perf_counter()
            try:
                dense_hits = self._dense(query, candidate_k)
            except EmbeddingError as exc:
                if mode == "dense":
                    raise
                degraded = f"dense retrieval unavailable, BM25 only: {exc}"
                used_mode = "bm25"
                logger.warning("Hybrid retrieval degraded to BM25: %s", exc)
            dense_ms = int((time.perf_counter() - t0) * 1000)
        if mode in ("hybrid", "bm25"):
            t0 = time.perf_counter()
            bm25_hits = self._bm25_search(query, candidate_k)
            bm25_ms = int((time.perf_counter() - t0) * 1000)

        if exclude:
            dense_hits = [h for h in dense_hits if not exclude(h[2])]
            bm25_hits = [h for h in bm25_hits if not exclude(h[2])]

        merged: dict[str, RetrievedChunk] = {}
        for rank, (pid, score, payload) in enumerate(dense_hits, start=1):
            merged[pid] = RetrievedChunk(pid, payload, 0.0, dense_score=score, dense_rank=rank)
        for rank, (pid, score, payload) in enumerate(bm25_hits, start=1):
            chunk = merged.setdefault(pid, RetrievedChunk(pid, payload, 0.0))
            chunk.bm25_score, chunk.bm25_rank = score, rank

        for chunk in merged.values():
            if used_mode == "dense":
                chunk.score = chunk.dense_score or 0.0
            elif used_mode == "bm25":
                chunk.score = chunk.bm25_score or 0.0
            else:
                chunk.score = sum(1.0 / (RRF_K + r) for r in (chunk.dense_rank, chunk.bm25_rank) if r)

        ranked = sorted(merged.values(), key=lambda c: (-c.score, c.point_id))
        results: list[RetrievedChunk] = []
        seen_units: set[tuple] = set()
        for chunk in ranked:
            if collapse_parts and chunk.payload.get("split"):
                key = _unit_key(chunk.payload)
                if key in seen_units:
                    continue
                seen_units.add(key)
            results.append(chunk)
            if len(results) == top_k:
                break

        return RetrievalResult(
            query=query, mode=used_mode, chunks=results,
            dense_latency_ms=dense_ms, bm25_latency_ms=bm25_ms,
            total_latency_ms=int((time.perf_counter() - started) * 1000),
            degraded_reason=degraded,
            candidates={"dense": len(dense_hits), "bm25": len(bm25_hits)},
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Query an indexed repository.")
    parser.add_argument("query")
    parser.add_argument("--github-id", type=int, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--mode", choices=MODES, default="hybrid")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    result = HybridRetriever(args.github_id).search(args.query, top_k=args.top_k, mode=args.mode)
    print(f"mode={result.mode} dense={result.dense_latency_ms}ms bm25={result.bm25_latency_ms}ms"
          + (f" DEGRADED: {result.degraded_reason}" if result.degraded_reason else ""))
    for i, c in enumerate(result.chunks, 1):
        print(f"{i}. {c.score:.4f} {c.payload.get('type')} {c.payload.get('qualified_name')} {c.location}"
              f"  dense_rank={c.dense_rank} bm25_rank={c.bm25_rank}")


if __name__ == "__main__":
    main()
