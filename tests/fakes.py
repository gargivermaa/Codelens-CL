"""Test doubles shared across test modules (never used by application code)."""
import hashlib
from types import SimpleNamespace

from app.core.bm25 import tokenize
from app.core.embedder import EMBEDDING_DIMENSIONS


def hashed_vector(text: str) -> list[float]:
    """Deterministic bag-of-tokens vector: texts sharing identifiers point the same way.

    Stands in for a semantic embedding in tests; it is NOT a model.
    """
    vec = [0.0] * EMBEDDING_DIMENSIONS
    for token in tokenize(text) or ["empty"]:
        digest = hashlib.sha256(token.encode()).digest()
        vec[int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS] += 1.0
    vec[0] += 0.01  # never all-zero
    return vec


class HashingGemini:
    """Fake Gemini client returning hashed_vector() embeddings."""

    def __init__(self, fail_with: Exception | None = None):
        self.models = self
        self.calls: list[list[str]] = []
        self.configs = []
        self.fail_with = fail_with

    def embed_content(self, model, contents, config):
        if self.fail_with:
            raise self.fail_with
        batch = [contents] if isinstance(contents, str) else list(contents)
        self.calls.append(batch)
        self.configs.append(config)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=hashed_vector(t), statistics=None)
                                           for t in batch])
