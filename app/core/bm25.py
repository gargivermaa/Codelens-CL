"""
BM25 (Okapi) keyword index with a code-aware tokenizer.

Dense embeddings capture meaning ("validates a token") but are weak at exact
identifiers ("calculate_discount", "MAX_RETRIES"). BM25 is the opposite. The
hybrid retriever combines both.

Tokenizer: identifiers are kept whole AND split on snake_case / camelCase, so
"getUserById" matches queries for "get_user_by_id", "user" or "byid".
Python keywords and very common names are dropped as stopwords.
"""
import keyword
import math
import re
from collections import Counter

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")

STOPWORDS = frozenset(keyword.kwlist) | frozenset({
    "self", "cls", "none", "true", "false", "args", "kwargs", "the", "a", "an", "of", "to",
    "is", "in", "it", "and", "or", "for", "on", "be", "this", "that", "with",
})


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for identifier in _IDENTIFIER.findall(text):
        lowered = identifier.lower()
        pieces = [p.lower() for part in identifier.split("_") for p in _CAMEL.findall(part)]
        if lowered not in STOPWORDS and len(lowered) > 1:
            tokens.append(lowered)
        if len(pieces) > 1:
            tokens.extend(p for p in pieces if p not in STOPWORDS and len(p) > 1)
    return tokens


class BM25Index:
    """Okapi BM25 over a fixed list of tokenized documents."""

    def __init__(self, documents: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_freqs = [Counter(doc) for doc in documents]
        self.doc_lens = [len(doc) for doc in documents]
        self.avg_len = (sum(self.doc_lens) / len(documents)) if documents else 0.0
        df: Counter = Counter()
        for freqs in self.doc_freqs:
            df.update(freqs.keys())
        n = len(documents)
        # BM25+ style idf floor at 0 avoids negative weights for very common terms.
        self.idf = {term: max(0.0, math.log((n - f + 0.5) / (f + 0.5) + 1.0)) for term, f in df.items()}

    def __len__(self) -> int:
        return len(self.doc_freqs)

    def scores(self, query_tokens: list[str]) -> list[float]:
        terms = set(query_tokens)
        results = []
        for freqs, length in zip(self.doc_freqs, self.doc_lens):
            score = 0.0
            norm = self.k1 * (1 - self.b + self.b * (length / self.avg_len if self.avg_len else 0))
            for term in terms:
                tf = freqs.get(term)
                if tf:
                    score += self.idf.get(term, 0.0) * tf * (self.k1 + 1) / (tf + norm)
            results.append(score)
        return results
