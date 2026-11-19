"""Hybrid retrieval tests: in-memory Qdrant + deterministic fake embeddings."""
import pytest
from qdrant_client import QdrantClient

from app.core.bm25 import BM25Index, tokenize
from app.core.chunker import chunk_source
from app.core.embedder import EmbeddingQuotaError, index_repository
from app.core.retriever import HybridRetriever, RepositoryNotIndexedError
from tests.fakes import HashingGemini

AUTH = b'''
def hash_password(password, salt):
    return pbkdf2_hmac("sha256", password, salt, 100_000)


def verify_password(password, salt, expected_hash):
    return compare_digest(hash_password(password, salt), expected_hash)


class SessionStore:
    def create_session(self, user_id):
        token = token_urlsafe(32)
        self.redis.set(token, user_id, ex=3600)
        return token

    def revoke_session(self, token):
        self.redis.delete(token)
'''

BILLING = b'''
def calculate_discount(order_total, coupon_code):
    if coupon_code == "WELCOME10":
        return order_total * 0.10
    return 0


def apply_tax(amount, region):
    return amount * TAX_RATES[region]
'''


@pytest.fixture
def indexed():
    qdrant = QdrantClient(":memory:")
    chunks = chunk_source(AUTH, "app/auth.py") + chunk_source(BILLING, "app/billing.py")
    index_repository(100, chunks, HashingGemini(), qdrant)
    other = chunk_source(b"def calculate_discount(x):\n    return 'OTHER REPO'\n", "x.py")
    index_repository(200, other, HashingGemini(), qdrant)
    return qdrant


def _names(result):
    return [c.payload["qualified_name"] for c in result.chunks]


def test_tokenizer_splits_snake_and_camel_case_and_drops_keywords():
    tokens = tokenize("def getUserById(self, user_id): return None")
    assert {"getuserbyid", "get", "user", "by", "id", "user_id"} <= set(tokens)
    assert "def" not in tokens and "self" not in tokens and "return" not in tokens


def test_bm25_ranks_documents_with_rare_matching_terms_first():
    index = BM25Index([tokenize("apply tax amount region"), tokenize("calculate discount coupon"),
                       tokenize("tax tax tax rates")])
    scores = index.scores(tokenize("coupon discount"))
    assert scores.index(max(scores)) == 1 and scores[0] == 0


def test_bm25_mode_finds_identifier_match(indexed):
    result = HybridRetriever(100, indexed, HashingGemini()).search("calculate_discount(total, code)",
                                                                   top_k=3, mode="bm25")
    assert _names(result)[0] == "calculate_discount"
    assert result.chunks[0].bm25_rank == 1 and result.chunks[0].dense_rank is None


def test_dense_mode_uses_query_task_type_and_qdrant(indexed):
    gemini = HashingGemini()
    result = HybridRetriever(100, indexed, gemini).search("verify password hash compare", top_k=2, mode="dense")
    assert _names(result)[0] in {"verify_password", "hash_password"}
    assert gemini.configs[-1].task_type == "CODE_RETRIEVAL_QUERY"
    assert result.chunks[0].dense_score is not None


def test_hybrid_fuses_both_rank_lists_with_rrf(indexed):
    result = HybridRetriever(100, indexed, HashingGemini()).search(
        "token = token_urlsafe(32); self.redis.set(token, user_id)", top_k=3)
    top = result.chunks[0]
    assert top.payload["qualified_name"] == "SessionStore.create_session"
    assert top.dense_rank is not None and top.bm25_rank is not None
    assert top.score == pytest.approx(1 / (60 + top.dense_rank) + 1 / (60 + top.bm25_rank))
    assert result.mode == "hybrid" and result.dense_latency_ms is not None


def test_results_are_deduplicated_and_keep_metadata(indexed):
    result = HybridRetriever(100, indexed, HashingGemini()).search("password token session", top_k=10)
    ids = [c.point_id for c in result.chunks]
    assert len(ids) == len(set(ids))
    for c in result.chunks:
        assert {"file", "qualified_name", "start_line", "end_line", "text"} <= set(c.payload)


def test_exclude_filter_removes_changed_code(indexed):
    result = HybridRetriever(100, indexed, HashingGemini()).search(
        "calculate_discount coupon", top_k=5,
        exclude=lambda p: p["qualified_name"] == "calculate_discount")
    assert "calculate_discount" not in _names(result)


def test_repositories_are_isolated(indexed):
    result = HybridRetriever(200, indexed, HashingGemini()).search("calculate_discount", top_k=5)
    assert {c.payload["file"] for c in result.chunks} == {"x.py"}
    result = HybridRetriever(100, indexed, HashingGemini()).search("calculate_discount", top_k=5)
    assert "OTHER REPO" not in "".join(c.payload["text"] for c in result.chunks)


def test_unindexed_repository_raises(indexed):
    with pytest.raises(RepositoryNotIndexedError):
        HybridRetriever(999, indexed, HashingGemini()).search("anything")


def test_dense_failure_degrades_explicitly_to_bm25(indexed):
    failing = HashingGemini(fail_with=EmbeddingQuotaError("quota exhausted"))
    result = HybridRetriever(100, indexed, failing).search("calculate_discount", top_k=2)
    assert result.mode == "bm25" and "quota exhausted" in result.degraded_reason
    assert _names(result)[0] == "calculate_discount"


def test_dense_only_mode_does_not_hide_failures(indexed):
    failing = HashingGemini(fail_with=EmbeddingQuotaError("quota exhausted"))
    with pytest.raises(EmbeddingQuotaError):
        HybridRetriever(100, indexed, failing).search("x", mode="dense")


def test_split_parts_are_collapsed_to_one_hit_per_unit():
    qdrant = QdrantClient(":memory:")
    big = ("def big_parser(stream):\n" + "".join(f"    field_{i} = stream.read_field({i})\n"
                                               for i in range(300))).encode()
    parts = chunk_source(big, "p.py")
    assert len(parts) > 2
    index_repository(300, parts, HashingGemini(), qdrant)
    result = HybridRetriever(300, qdrant, HashingGemini()).search("stream read_field", top_k=5)
    assert len(result.chunks) == 1
    assert len(HybridRetriever(300, qdrant, HashingGemini()).search(
        "stream read_field", top_k=5, collapse_parts=False).chunks) == 5
