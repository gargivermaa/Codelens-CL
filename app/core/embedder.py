"""
Gemini embeddings + Qdrant indexing.

index_repository() turns code chunks (from app.core.chunker) into real Gemini
embedding vectors and stores them, with their metadata, in one Qdrant
collection per repository. Retrieval lives in app/core/retriever.py.

Design notes
  * Model: gemini-embedding-001 at 1536 dimensions (Matryoshka truncation of the
    native 3072). Truncated outputs are not unit length, so every vector is
    L2-normalized here; Qdrant uses COSINE distance.
  * Task types: chunks are embedded as RETRIEVAL_DOCUMENT, search queries as
    CODE_RETRIEVAL_QUERY (settings.EMBEDDING_QUERY_TASK_TYPE), the asymmetric
    pair Google documents for code retrieval.
  * Input limit: 2048 tokens per input. The chunker guarantees every chunk's
    embed_text fits MAX_INPUT_BYTES (tokens <= bytes), so documents are never
    truncated; an oversized document raises instead of silently losing code.
    Only search queries (derived from diffs) may be shortened.
  * Collection name uses the repository's immutable GitHub id (repo_<id>).
  * Point ids are deterministic (uuid5 of file/type/name/line[/part]).
  * Incremental: a point whose stored payload equals the new chunk's payload is
    not re-embedded, which saves free-tier quota on every re-index.
  * Failure safety: embed first, upsert into the live collection, and only then
    prune points that no longer exist. A failure never deletes existing data,
    and re-running converges to the exact new state.
  * Token usage: Gemini's embed response does not always report token counts.
    When it does not, usage is None - never estimated or invented.
"""
import argparse
import logging
import math
import time
import uuid
from dataclasses import dataclass

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointIdsList, PointStruct, VectorParams

from app.config import settings
from app.core.chunker import MAX_EMBED_BYTES, CodeChunk, chunk_repository

logger = logging.getLogger("codelens.embedder")

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 1536
DOCUMENT_TASK_TYPE = "RETRIEVAL_DOCUMENT"

MAX_INPUT_BYTES = MAX_EMBED_BYTES
MAX_BATCH_ITEMS = 100        # batchEmbedContents accepts at most 100 requests
MAX_BATCH_BYTES = 100_000

UPSERT_BATCH_SIZE = 100
PRUNE_BATCH_SIZE = 500
SCROLL_PAGE_SIZE = 1000

_POINT_ID_NAMESPACE = uuid.UUID("6f1c2f0e-5d0b-4a56-9d52-3c1a7b9e4d10")

# Status codes the SDK retries with exponential backoff (rate limit + transient).
_RETRY_STATUS_CODES = [408, 429, 500, 502, 503, 504]


class EmbeddingError(RuntimeError):
    """The embedding provider failed after retries."""


class EmbeddingQuotaError(EmbeddingError):
    """Free-tier quota / rate limit exhausted. Resume later; never switch to paid."""


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    total_tokens: int | None


@dataclass(frozen=True)
class IndexResult:
    collection: str
    chunks_indexed: int
    total_tokens: int | None
    elapsed_s: float
    embedded: int = 0     # chunks sent to Gemini in this run
    reused: int = 0       # unchanged chunks whose stored vector was kept
    pruned: int = 0       # stale points deleted


def get_gemini_client():
    """Create the real Gemini API client with bounded retry/backoff."""
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set (add it to .env)")
    return genai.Client(
        api_key=settings.GEMINI_API_KEY,
        http_options=types.HttpOptions(
            timeout=60_000,
            retry_options=types.HttpRetryOptions(
                attempts=settings.GEMINI_MAX_ATTEMPTS,
                initial_delay=2.0,
                max_delay=60.0,
                http_status_codes=_RETRY_STATUS_CODES,
            ),
        ),
    )


def get_qdrant_client() -> QdrantClient:
    if settings.QDRANT_PATH:
        # Embedded local mode (no server). Single-process only.
        return QdrantClient(path=settings.QDRANT_PATH)
    return QdrantClient(url=settings.QDRANT_URL or "http://localhost:6333")


def collection_name(repo_github_id: int) -> str:
    return f"repo_{repo_github_id}"


def _point_id(chunk: CodeChunk) -> str:
    key = f"{chunk.file}:{chunk.type}:{chunk.qualified_name}:{chunk.start_line}"
    if chunk.part_count > 1:
        # Unsplit chunks keep their Day 8 ids; parts get a unique, stable suffix.
        key += f"#part{chunk.part}"
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, key))


def point_id(chunk: CodeChunk) -> str:
    """Public alias: the Qdrant point id of a chunk."""
    return _point_id(chunk)


def _truncate_to_bytes(text: str) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= MAX_INPUT_BYTES:
        return text
    return raw[:MAX_INPUT_BYTES].decode("utf-8", errors="ignore")


def _make_batches(texts: list[str]) -> list[list[str]]:
    """Split inputs into bounded batches, preserving order."""
    batches: list[list[str]] = []
    current: list[str] = []
    current_bytes = 0
    for text in texts:
        size = len(text.encode("utf-8"))
        if current and (len(current) >= MAX_BATCH_ITEMS or current_bytes + size > MAX_BATCH_BYTES):
            batches.append(current)
            current, current_bytes = [], 0
        current.append(text)
        current_bytes += size
    if current:
        batches.append(current)
    return batches


def _normalize_vector(values: list[float] | None) -> list[float]:
    """Validate dimensions and L2-normalize a Gemini embedding vector."""
    if not values:
        raise EmbeddingError("Gemini returned an empty embedding")
    vector = [float(value) for value in values]
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise EmbeddingError(f"Expected {EMBEDDING_DIMENSIONS} dimensions, received {len(vector)}")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0 or math.isnan(norm):
        raise EmbeddingError("Gemini returned a zero/invalid embedding vector")
    return [value / norm for value in vector]


def _classify_api_error(exc: Exception) -> EmbeddingError:
    code = getattr(exc, "code", None)
    status = str(getattr(exc, "status", "") or "")
    if code == 429 or "RESOURCE_EXHAUSTED" in status:
        return EmbeddingQuotaError(
            "Gemini free-tier quota or rate limit exhausted for the embedding model. "
            "Wait for the quota window to reset and re-run; indexing is incremental, "
            f"so finished work is kept. Details: {exc}"
        )
    return EmbeddingError(f"Gemini embedding request failed: {exc}")


_last_request_at = 0.0


def _throttle() -> None:
    """Client-side spacing between requests to stay under free-tier RPM limits."""
    global _last_request_at
    gap = settings.GEMINI_MIN_REQUEST_INTERVAL_S
    wait = _last_request_at + gap - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def embed_texts(texts: list[str], client=None, task_type: str = DOCUMENT_TASK_TYPE,
                allow_truncation: bool = True) -> EmbeddingResult:
    """Embed texts with Gemini; vectors come back in the same order as `texts`.

    allow_truncation=False (used for indexing) raises instead of cutting an
    over-long input, so source code can never be silently lost.
    """
    if not texts:
        return EmbeddingResult(vectors=[], total_tokens=0)

    oversized = [i for i, t in enumerate(texts) if len(t.encode("utf-8")) > MAX_INPUT_BYTES]
    if oversized and not allow_truncation:
        raise ValueError(
            f"{len(oversized)} input(s) exceed {MAX_INPUT_BYTES} bytes; chunks must be split "
            "by the chunker before embedding (refusing to truncate source code)"
        )
    inputs = [_truncate_to_bytes(text) for text in texts]
    if oversized:
        logger.warning("%d input(s) exceeded %d bytes and were truncated", len(oversized), MAX_INPUT_BYTES)

    client = client or get_gemini_client()
    vectors: list[list[float]] = []
    total_tokens = 0
    usage_available = True

    for batch in _make_batches(inputs):
        _throttle()
        try:
            response = client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=batch,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=EMBEDDING_DIMENSIONS,
                ),
            )
        except genai_errors.APIError as exc:
            raise _classify_api_error(exc) from exc

        embeddings = getattr(response, "embeddings", None) or []
        if len(embeddings) != len(batch):
            raise EmbeddingError(f"Embedding count mismatch: sent {len(batch)}, got {len(embeddings)}")

        for item in embeddings:
            vectors.append(_normalize_vector(getattr(item, "values", None)))
            token_count = getattr(getattr(item, "statistics", None), "token_count", None)
            if token_count is None:
                usage_available = False
            else:
                total_tokens += int(token_count)

    if len(vectors) != len(texts):
        raise EmbeddingError(f"Embedding count mismatch: sent {len(texts)}, got {len(vectors)}")
    return EmbeddingResult(vectors=vectors, total_tokens=total_tokens if usage_available else None)


def embed_query(text: str, client=None) -> list[float]:
    """Embed one search query (asymmetric query task type, truncation allowed)."""
    return embed_texts([text], client=client, task_type=settings.EMBEDDING_QUERY_TASK_TYPE).vectors[0]


def _existing_points(qdrant: QdrantClient, name: str, with_payload: bool = True) -> dict[str, dict]:
    points_by_id: dict[str, dict] = {}
    offset = None
    while True:
        points, offset = qdrant.scroll(
            collection_name=name, limit=SCROLL_PAGE_SIZE, offset=offset,
            with_payload=with_payload, with_vectors=False,
        )
        for point in points:
            points_by_id[str(point.id)] = point.payload or {}
        if offset is None:
            return points_by_id


def _existing_point_ids(qdrant: QdrantClient, name: str) -> list[str]:
    return list(_existing_points(qdrant, name, with_payload=False))


def _prune_stale_points(qdrant: QdrantClient, name: str, keep_ids: set[str],
                        existing_ids: list[str] | None = None) -> int:
    """Delete points that no longer exist in the repository."""
    if existing_ids is None:
        existing_ids = _existing_point_ids(qdrant, name)
    stale = [pid for pid in existing_ids if pid not in keep_ids]
    for start in range(0, len(stale), PRUNE_BATCH_SIZE):
        qdrant.delete(
            collection_name=name,
            points_selector=PointIdsList(points=stale[start:start + PRUNE_BATCH_SIZE]),
            wait=True,
        )
    return len(stale)


def ensure_collection(qdrant: QdrantClient, name: str) -> None:
    if not qdrant.collection_exists(name):
        qdrant.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=EMBEDDING_DIMENSIONS, distance=Distance.COSINE),
        )
        return
    vectors = qdrant.get_collection(name).config.params.vectors
    size = getattr(vectors, "size", None)
    if size is not None and size != EMBEDDING_DIMENSIONS:
        raise RuntimeError(
            f"Collection {name} stores {size}-dim vectors but the model produces "
            f"{EMBEDDING_DIMENSIONS}; delete the collection and re-index"
        )


def index_repository(repo_github_id: int, chunks: list[CodeChunk], gemini_client=None,
                     qdrant: QdrantClient | None = None, progress=None) -> IndexResult:
    """(Re)index: make the repository's collection contain exactly `chunks`.

    Order matters for safety: (1) diff against stored payloads, (2) embed the
    new/changed chunks, (3) upsert, (4) prune stale points last.
    `progress`, if given, is called as progress(stage, done, total).
    """
    started = time.perf_counter()
    name = collection_name(repo_github_id)
    qdrant = qdrant or get_qdrant_client()

    # Duplicate ids would mean two chunks overwrite each other: fail loudly.
    ids = [_point_id(chunk) for chunk in chunks]
    if len(set(ids)) != len(ids):
        raise RuntimeError("Duplicate chunk ids produced by the chunker")

    existing = _existing_points(qdrant, name) if qdrant.collection_exists(name) else {}
    todo = [(pid, chunk) for pid, chunk in zip(ids, chunks) if existing.get(pid) != chunk.to_dict()]
    reused = len(chunks) - len(todo)
    if progress:
        progress("diff", reused, len(chunks))

    # An API failure must happen before Qdrant is modified.
    embedding = embed_texts([chunk.embed_text for _, chunk in todo], client=gemini_client,
                            task_type=DOCUMENT_TASK_TYPE, allow_truncation=False) if todo else \
        EmbeddingResult(vectors=[], total_tokens=0)

    ensure_collection(qdrant, name)
    if not chunks:
        logger.warning("No chunks to index for repo %s; collection will be emptied", repo_github_id)

    points = [
        PointStruct(id=pid, vector=vector, payload=chunk.to_dict())
        for (pid, chunk), vector in zip(todo, embedding.vectors)
    ]
    for start in range(0, len(points), UPSERT_BATCH_SIZE):
        qdrant.upsert(collection_name=name, points=points[start:start + UPSERT_BATCH_SIZE], wait=True)
        if progress:
            progress("upsert", min(start + UPSERT_BATCH_SIZE, len(points)), len(points))

    # Only reached when every new chunk is stored.
    pruned = _prune_stale_points(qdrant, name, set(ids), existing_ids=list(existing) or None)

    elapsed = time.perf_counter() - started
    tokens = "unavailable" if embedding.total_tokens is None else str(embedding.total_tokens)
    logger.info("Indexed %d chunks into %s (%d embedded, %d reused, %d pruned, %s embedding tokens, %.1fs)",
                len(chunks), name, len(todo), reused, pruned, tokens, elapsed)
    return IndexResult(collection=name, chunks_indexed=len(chunks), total_tokens=embedding.total_tokens,
                       elapsed_s=elapsed, embedded=len(todo), reused=reused, pruned=pruned)


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunk, embed and index a Python repository into Qdrant.")
    parser.add_argument("repo_path")
    parser.add_argument("--github-id", type=int, required=True)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    chunks = chunk_repository(args.repo_path)

    def progress(stage, done, total):
        logger.info("progress %s: %d/%d", stage, done, total)

    result = index_repository(args.github_id, chunks, progress=progress)
    tokens = result.total_tokens if result.total_tokens is not None else "usage unavailable"
    print(f"Indexed {result.chunks_indexed} chunks into '{result.collection}' | "
          f"embedded {result.embedded}, reused {result.reused}, pruned {result.pruned} | "
          f"{tokens} embedding tokens | {result.elapsed_s:.1f}s")


if __name__ == "__main__":
    main()
