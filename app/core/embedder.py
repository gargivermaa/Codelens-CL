
"""
Gemini embeddings + Qdrant indexing.

index_repository() turns code chunks (from app.core.chunker) into real
Gemini embedding vectors and stores them with metadata in Qdrant.

Retrieval is added on Day 10 in app/core/retriever.py.
"""
import argparse
import logging
import math

import time
import uuid
from dataclasses import dataclass

from google import genai
from google.genai import types
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointIdsList,
    PointStruct,
    VectorParams,
)

from app.config import settings
from app.core.chunker import CodeChunk, chunk_repository

logger = logging.getLogger("codelens.embedder")

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 1536

# Gemini embedding-001 has a 2048-token input limit.
# A conservative byte limit avoids requiring a tokenizer.
MAX_INPUT_BYTES = 1800
MAX_BATCH_ITEMS = 100
MAX_BATCH_BYTES = 100_000

UPSERT_BATCH_SIZE = 100
PRUNE_BATCH_SIZE = 500
SCROLL_PAGE_SIZE = 1000

_POINT_ID_NAMESPACE = uuid.UUID(
    "6f1c2f0e-5d0b-4a56-9d52-3c1a7b9e4d10"
)


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


def get_gemini_client():
    """Create the real Gemini API client."""
    if not settings.GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set (add it to .env)"
        )

    return genai.Client(api_key=settings.GEMINI_API_KEY)


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(
        url=settings.QDRANT_URL or "http://localhost:6333"
    )


def collection_name(repo_github_id: int) -> str:
    return f"repo_{repo_github_id}"


def _point_id(chunk: CodeChunk) -> str:
    key = (
        f"{chunk.file}:{chunk.type}:"
        f"{chunk.qualified_name}:{chunk.start_line}"
    )
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, key))


def _split_text_by_bytes(text: str) -> list[str]:
    """Split text into UTF-8-safe pieces without losing any content."""
    if not text:
        return [text]

    pieces = []
    current = []
    current_bytes = 0

    for char in text:
        char_bytes = len(char.encode("utf-8"))

        if current and current_bytes + char_bytes > MAX_INPUT_BYTES:
            pieces.append("".join(current))
            current = []
            current_bytes = 0

        current.append(char)
        current_bytes += char_bytes

    if current:
        pieces.append("".join(current))

    return pieces


def _make_batches(texts: list[str]) -> list[list[str]]:
    """Split inputs into bounded batches, preserving order."""
    batches: list[list[str]] = []
    current: list[str] = []
    current_bytes = 0

    for text in texts:
        size = len(text.encode("utf-8"))

        if current and (
            len(current) >= MAX_BATCH_ITEMS
            or current_bytes + size > MAX_BATCH_BYTES
        ):
            batches.append(current)
            current = []
            current_bytes = 0

        current.append(text)
        current_bytes += size

    if current:
        batches.append(current)

    return batches


def _normalize_vector(values: list[float]) -> list[float]:
    """L2-normalize a Gemini embedding vector."""
    vector = [float(value) for value in values]

    if len(vector) != EMBEDDING_DIMENSIONS:
        raise RuntimeError(
            f"Expected {EMBEDDING_DIMENSIONS} dimensions, "
            f"received {len(vector)}"
        )

    norm = math.sqrt(sum(value * value for value in vector))

    if norm == 0:
        raise RuntimeError("Gemini returned a zero embedding vector")

    return [value / norm for value in vector]



def embed_texts(
    texts: list[str],
    client=None,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> EmbeddingResult:
    """
    Embed texts using Gemini.

    Long inputs are split into UTF-8-safe pieces.
    Their vectors are combined into one normalized vector per original input.
    """
    if not texts:
        return EmbeddingResult(vectors=[], total_tokens=0)

    # Split long inputs without losing any content.
    pieces_per_text = [
        _split_text_by_bytes(text) for text in texts
    ]
    flat_inputs = [
        piece
        for pieces in pieces_per_text
        for piece in pieces
    ]

    client = client or get_gemini_client()

    piece_vectors: list[list[float]] = []
    total_tokens = 0
    usage_available = True

    for batch in _make_batches(flat_inputs):
        response = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=batch,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=EMBEDDING_DIMENSIONS,
            ),
        )

        embeddings = response.embeddings or []

        if len(embeddings) != len(batch):
            raise RuntimeError(
                f"Embedding count mismatch: sent {len(batch)}, "
                f"got {len(embeddings)}"
            )

        for item in embeddings:
            piece_vectors.append(
                _normalize_vector(item.values)
            )

            statistics = getattr(item, "statistics", None)
            token_count = getattr(statistics, "token_count", None)

            if token_count is None:
                usage_available = False
            else:
                total_tokens += int(token_count)

    if len(piece_vectors) != len(flat_inputs):
        raise RuntimeError(
            f"Embedding count mismatch: sent {len(flat_inputs)}, "
            f"got {len(piece_vectors)}"
        )

    # Combine piece vectors into one vector per original input.
    vectors = []
    offset = 0

    for pieces in pieces_per_text:
        count = len(pieces)
        group_vectors = piece_vectors[offset:offset + count]
        offset += count

        
        # A single-piece input already has its final normalized vector.
        if count == 1:
            vectors.append(group_vectors[0])
            continue

        weights = [
            max(1, len(piece.encode("utf-8")))
            for piece in pieces
        ]
        total_weight = sum(weights)

        combined = [
            sum(
                vector[i] * weight
                for vector, weight in zip(group_vectors, weights)
            ) / total_weight
            for i in range(EMBEDDING_DIMENSIONS)
        ]

        vectors.append(_normalize_vector(combined))


    return EmbeddingResult(
        vectors=vectors,
        total_tokens=total_tokens if usage_available else None,
    )



def _existing_point_ids(
    qdrant: QdrantClient,
    name: str,
) -> list[str]:
    ids: list[str] = []
    offset = None

    while True:
        points, offset = qdrant.scroll(
            collection_name=name,
            limit=SCROLL_PAGE_SIZE,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )

        ids.extend(str(point.id) for point in points)

        if offset is None:
            return ids


def _prune_stale_points(
    qdrant: QdrantClient,
    name: str,
    keep_ids: set[str],
) -> int:
    """Delete points that no longer exist in the repository."""
    stale = [
        point_id
        for point_id in _existing_point_ids(qdrant, name)
        if point_id not in keep_ids
    ]

    for start in range(0, len(stale), PRUNE_BATCH_SIZE):
        qdrant.delete(
            collection_name=name,
            points_selector=PointIdsList(
                points=stale[start:start + PRUNE_BATCH_SIZE]
            ),
            wait=True,
        )

    return len(stale)


def index_repository(
    repo_github_id: int,
    chunks: list[CodeChunk],
    gemini_client=None,
    qdrant: QdrantClient | None = None,
) -> IndexResult:
    """
    Full repository re-index.

    1. Generate all embeddings.
    2. Create collection if missing.
    3. Upsert new chunks.
    4. Prune stale points only after successful upserts.
    """
    started = time.perf_counter()
    name = collection_name(repo_github_id)

    # An API failure must happen before Qdrant is modified.
    embedding = embed_texts(
        [chunk.text for chunk in chunks],
        client=gemini_client,
        task_type="RETRIEVAL_DOCUMENT",
    )

    qdrant = qdrant or get_qdrant_client()

    if not qdrant.collection_exists(name):
        qdrant.create_collection(
            collection_name=name,
            vectors_config=VectorParams(
                size=EMBEDDING_DIMENSIONS,
                distance=Distance.COSINE,
            ),
        )

    if not chunks:
        logger.warning(
            "No chunks to index for repo %s; collection will be emptied",
            repo_github_id,
        )

    points = [
        PointStruct(
            id=_point_id(chunk),
            vector=vector,
            payload=chunk.to_dict(),
        )
        for chunk, vector in zip(chunks, embedding.vectors)
    ]

    for start in range(0, len(points), UPSERT_BATCH_SIZE):
        qdrant.upsert(
            collection_name=name,
            points=points[start:start + UPSERT_BATCH_SIZE],
            wait=True,
        )

    pruned = _prune_stale_points(
        qdrant,
        name,
        {str(point.id) for point in points},
    )

    elapsed = time.perf_counter() - started
    token_display = (
    str(embedding.total_tokens)
    if embedding.total_tokens is not None
    else "unavailable"
)

    logger.info(
    "Indexed %d chunks into %s "
    "(%d pruned, %s embedding tokens, %.1fs)",
    len(chunks),
    name,
    pruned,
    token_display,
    elapsed,
)

    return IndexResult(
        collection=name,
        chunks_indexed=len(chunks),
        total_tokens=embedding.total_tokens,
        elapsed_s=elapsed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chunk, embed and index a Python repository into Qdrant."
    )

    parser.add_argument("repo_path")
    parser.add_argument("--github-id", type=int, required=True)

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )

    chunks = chunk_repository(args.repo_path)
    result = index_repository(args.github_id, chunks)

    print(
        f"Indexed {result.chunks_indexed} chunks into "
        f"'{result.collection}' | "
        f"{result.total_tokens if result.total_tokens is not None else 'usage unavailable'} embedding tokens | "
        f"{result.elapsed_s:.1f}s"
    )


if __name__ == "__main__":
    main()
