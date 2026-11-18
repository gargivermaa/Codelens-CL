"""
Embedder/indexer tests. No network: OpenAI is replaced by a deterministic fake
and Qdrant runs in qdrant-client's in-memory mode.
"""
import json
import httpx2
from google import genai
from google.genai import types
import random
from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient

from app.config import settings
from app.core import embedder
from app.core.chunker import chunk_source
from app.core.embedder import (
    EMBEDDING_DIMENSIONS,
    _make_batches,
    _point_id,
    collection_name,
    embed_texts,
    # get_gemini_client,
    get_gemini_client,
    index_repository,
)

SAMPLE = b"def a():\n    return 1\n\ndef b():\n    return 2\n\nclass C:\n    def m(self):\n        pass\n"


def _vec(text: str) -> list[float]:
    rng = random.Random(text)
    return [rng.random() + 0.01 for _ in range(EMBEDDING_DIMENSIONS)]


# class FakeGemini:
#     def __init__(self):
#         self.calls: list[list[str]] = []
#         self.embeddings = self

#     def create(self, model, input):
#         self.calls.append(list(input))
#         data = [SimpleNamespace(index=i, embedding=_vec(t)) for i, t in enumerate(input)]
#         data.reverse()  # the API does not guarantee list order; embed_texts must use `index`
#         return SimpleNamespace(data=data, usage=SimpleNamespace(total_tokens=sum(len(t) for t in input)))


class FakeGemini:
    def __init__(self, include_statistics=True):
        self.calls: list[list[str]] = []
        self.configs = []
        self.include_statistics = include_statistics
        self.models = self

    def embed_content(self, model, contents, config):
        batch = [contents] if isinstance(contents, str) else list(contents)
        self.calls.append(batch)
        self.configs.append(config)

        embeddings = []
        for text in batch:
            statistics = (
                SimpleNamespace(token_count=len(text))
                if self.include_statistics
                else None
            )
            embeddings.append(
                SimpleNamespace(
                    values=_vec(text),
                    statistics=statistics,
                )
            )

        return SimpleNamespace(embeddings=embeddings)


class ExplodingGemini:
    def __init__(self):
        self.models = self

    def embed_content(self, model, contents, config):
        raise RuntimeError("Gemini is down")


# class ExplodingOpenAI:
#     def __init__(self):
#         self.embeddings = self

#     def create(self, model, input):
#         raise RuntimeError("Gemini is down")


@pytest.fixture
def qdrant():
    return QdrantClient(":memory:")


def test_make_batches_respects_limits_and_keeps_order(monkeypatch):
    monkeypatch.setattr(embedder, "MAX_BATCH_ITEMS", 3)
    monkeypatch.setattr(embedder, "MAX_BATCH_BYTES", 10)
    texts = ["aaaa", "bbbb", "cc", "dddddd", "e", "f", "g", "h"]
    batches = _make_batches(texts)
    assert [t for batch in batches for t in batch] == texts
    assert all(len(b) <= 3 for b in batches)
    assert all(
    sum(len(t.encode("utf-8")) for t in b) <= 10
    for b in batches
)


def test_embed_texts_returns_vectors_in_input_order_and_sums_tokens(monkeypatch):
    monkeypatch.setattr(embedder, "MAX_BATCH_ITEMS", 2)
    fake = FakeGemini()
    texts = ["one", "two", "three", "four", "five"]
    result = embed_texts(texts, fake)
    assert len(fake.calls) == 3  # 2 + 2 + 1
    # assert result.vectors == [_vec(t) for t in texts]
    assert result.vectors == [
    embedder._normalize_vector(_vec(t)) for t in texts
]
    assert result.total_tokens == sum(len(t) for t in texts)


def test_embed_texts_with_no_input_makes_no_api_call():
    fake = FakeGemini()
    assert embed_texts([], fake).vectors == []
    assert fake.calls == []


def test_missing_api_key_fails_fast(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        get_gemini_client()


def test_point_id_is_stable_and_distinguishes_chunks():
    a, b, c = chunk_source(SAMPLE, "x.py")[:3]
    assert _point_id(a) == _point_id(chunk_source(SAMPLE, "x.py")[0])
    assert len({_point_id(a), _point_id(b), _point_id(c)}) == 3


def test_index_repository_stores_every_chunk_with_metadata(qdrant):
    chunks = chunk_source(SAMPLE, "pkg/x.py")
    result = index_repository(1296269, chunks, FakeGemini(), qdrant)

    assert result.collection == "repo_1296269" == collection_name(1296269)
    assert result.chunks_indexed == len(chunks) == 4  # a, b, C, C.m
    assert qdrant.count("repo_1296269", exact=True).count == 4

    points, _ = qdrant.scroll("repo_1296269", limit=100, with_payload=True)
    payloads = {p.payload["qualified_name"]: p.payload for p in points}
    assert set(payloads) == {"a", "b", "C", "C.m"}
    assert payloads["C.m"]["type"] == "method"
    assert payloads["C.m"]["file"] == "pkg/x.py"
    assert payloads["a"]["start_line"] == 1 and "return 1" in payloads["a"]["text"]


def test_reindexing_replaces_instead_of_duplicating(qdrant):
    chunks = chunk_source(SAMPLE, "x.py")
    index_repository(7, chunks, FakeGemini(), qdrant)
    index_repository(7, chunks, FakeGemini(), qdrant)
    assert qdrant.count("repo_7", exact=True).count == len(chunks)

    index_repository(7, chunks[:1], FakeGemini(), qdrant)  # code was deleted
    assert qdrant.count("repo_7", exact=True).count == 1


def test_repositories_get_separate_collections(qdrant):
    chunks = chunk_source(SAMPLE, "x.py")
    index_repository(1, chunks, FakeGemini(), qdrant)
    index_repository(2, chunks[:2], FakeGemini(), qdrant)
    assert qdrant.count("repo_1", exact=True).count == len(chunks)
    assert qdrant.count("repo_2", exact=True).count == 2


def test_gemini_failure_leaves_existing_index_intact(qdrant):
    chunks = chunk_source(SAMPLE, "x.py")
    index_repository(9, chunks, FakeGemini(), qdrant)
    with pytest.raises(RuntimeError, match="Gemini is down"):
        index_repository(9, chunks[:1], ExplodingGemini(), qdrant)
    assert qdrant.count("repo_9", exact=True).count == len(chunks)


def test_indexing_nothing_creates_empty_collection(qdrant):
    fake = FakeGemini()
    result = index_repository(5, [], fake, qdrant)
    assert result.chunks_indexed == 0 and fake.calls == []
    assert qdrant.count("repo_5", exact=True).count == 0

SAMPLE_V2 = b"def a():\n    return 100\n\ndef z():\n    return 3\n"


def _snapshot(qdrant, name):
    points, _ = qdrant.scroll(name, limit=1000, with_payload=True, with_vectors=True)
    return {str(p.id): (p.payload, tuple(p.vector)) for p in points}


def _fail_upserts_after(qdrant, monkeypatch, succeed):
    real_upsert = qdrant.upsert
    calls = {"n": 0}

    def flaky_upsert(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] > succeed:
            raise RuntimeError("Qdrant upsert failed")
        return real_upsert(*args, **kwargs)

    monkeypatch.setattr(qdrant, "upsert", flaky_upsert)


def test_qdrant_upsert_failure_leaves_previous_index_intact(qdrant, monkeypatch):
    index_repository(3, chunk_source(SAMPLE, "x.py"), FakeGemini(), qdrant)
    before = _snapshot(qdrant, "repo_3")

    _fail_upserts_after(qdrant, monkeypatch, succeed=0)
    with pytest.raises(RuntimeError, match="Qdrant upsert failed"):
        index_repository(3, chunk_source(SAMPLE_V2, "x.py"), FakeGemini(), qdrant)

    assert _snapshot(qdrant, "repo_3") == before  # same ids, payloads and vectors


def test_midway_upsert_failure_never_loses_old_chunks_and_rerun_converges(qdrant, monkeypatch):
    old_chunks = chunk_source(SAMPLE, "x.py")      # a, b, C, C.m
    new_chunks = chunk_source(SAMPLE_V2, "x.py")   # a (changed), z
    index_repository(4, old_chunks, FakeGemini(), qdrant)
    old_ids = {_point_id(c) for c in old_chunks}

    monkeypatch.setattr(embedder, "UPSERT_BATCH_SIZE", 1)
    _fail_upserts_after(qdrant, monkeypatch, succeed=1)  # 'a' is stored, 'z' fails
    with pytest.raises(RuntimeError, match="Qdrant upsert failed"):
        index_repository(4, new_chunks, FakeGemini(), qdrant)

    # Nothing was pruned: every previously indexed chunk is still searchable.
    assert old_ids <= set(_snapshot(qdrant, "repo_4"))

    monkeypatch.undo()  # Qdrant is healthy again; a retry must finish the job
    index_repository(4, new_chunks, FakeGemini(), qdrant)
    assert set(_snapshot(qdrant, "repo_4")) == {_point_id(c) for c in new_chunks}

def test_point_id_golden_value_guards_against_accidental_id_changes():
    # Changing the key format or namespace would orphan every existing index.
    assert _point_id(chunk_source(SAMPLE, "x.py")[0]) == "0c938445-c3a6-59bf-b30c-cf452de0ce3f"


def test_oversized_input_is_truncated_to_the_byte_limit_before_embedding():
    fake = FakeGemini()
    embed_texts(["x" * (embedder.MAX_INPUT_BYTES + 5000), "small"], fake)
    sent = fake.calls[0]
    assert len(sent[0].encode("utf-8")) == embedder.MAX_INPUT_BYTES
    assert sent[1] == "small"




def test_worst_case_batches_stay_under_the_api_limits():
    # tokens <= bytes, so byte budgets bound the token budgets (API: 8192 per input, ~300k per request).
    assert embedder.MAX_INPUT_BYTES < 8192 and embedder.MAX_BATCH_BYTES < 300_000
    batches = _make_batches(["x" * embedder.MAX_INPUT_BYTES] * 200)
    assert sum(len(b) for b in batches) == 200
    for batch in batches:
        assert len(batch) <= embedder.MAX_BATCH_ITEMS
        assert sum(len(t.encode("utf-8")) for t in batch) <= embedder.MAX_BATCH_BYTES



    
   


def test_prune_failure_midway_then_rerun_converges_to_the_exact_new_state(qdrant, monkeypatch):
    old_chunks = chunk_source(SAMPLE, "x.py")      # a, b, C, C.m
    new_chunks = chunk_source(SAMPLE_V2, "x.py")   # a (changed), z  -> b, C, C.m are stale
    new_ids = {_point_id(c) for c in new_chunks}
    index_repository(6, old_chunks, FakeGemini(), qdrant)

    monkeypatch.setattr(embedder, "PRUNE_BATCH_SIZE", 1)  # 3 stale points -> 3 delete calls
    real_delete = qdrant.delete
    calls = {"n": 0}

    def flaky_delete(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # first batch succeeds, second fails
            raise RuntimeError("Qdrant delete failed")
        return real_delete(*args, **kwargs)

    monkeypatch.setattr(qdrant, "delete", flaky_delete)
    with pytest.raises(RuntimeError, match="Qdrant delete failed"):
        index_repository(6, new_chunks, FakeGemini(), qdrant)

    after_failure = set(_snapshot(qdrant, "repo_6"))
    assert new_ids <= after_failure                     # every new chunk is already stored
    assert 0 < len(after_failure - new_ids) < 3         # pruning got partway, not all the way

    monkeypatch.undo()
    index_repository(6, new_chunks, FakeGemini(), qdrant)
    assert set(_snapshot(qdrant, "repo_6")) == new_ids


def test_existing_collection_is_reused_with_exact_payload_and_emptied_for_empty_repo(qdrant, monkeypatch):
    chunks = chunk_source(SAMPLE, "x.py")
    index_repository(8, chunks, FakeGemini(), qdrant)
    first_ids = set(_snapshot(qdrant, "repo_8"))

    created = []
    real_create = qdrant.create_collection
    monkeypatch.setattr(qdrant, "create_collection",
                        lambda *a, **kw: created.append(1) or real_create(*a, **kw))
    index_repository(8, chunks, FakeGemini(), qdrant)
    assert created == []                                 # reused, not recreated
    assert set(_snapshot(qdrant, "repo_8")) == first_ids  # same deterministic ids on re-index

    payload = next(iter(_snapshot(qdrant, "repo_8").values()))[0]
    assert set(payload) == {"text", "file", "name", "qualified_name", "type", "start_line", "end_line"}

    index_repository(8, [], FakeGemini(), qdrant)        # repository now has no Python code
    assert qdrant.count("repo_8", exact=True).count == 0


def test_gemini_uses_document_task_and_1536_dimensions():
    fake = FakeGemini()
    embed_texts(["def hello(): pass"], fake)

    config = fake.configs[0]
    assert config.task_type == "RETRIEVAL_DOCUMENT"
    assert config.output_dimensionality == 1536


def test_gemini_without_statistics_does_not_invent_token_usage():
    fake = FakeGemini(include_statistics=False)
    result = embed_texts(["def hello(): pass"], fake)

    assert len(result.vectors) == 1
    assert result.total_tokens is None


def test_gemini_sdk_roundtrip_with_http_mock():
    """Test actual Gemini SDK serialization and response parsing."""

    def handler(request):
        body = json.loads(request.content)

        assert request.url.path.endswith(":batchEmbedContents")
        assert len(body["requests"]) == 2

        requests = body["requests"]

        texts = [
            item["content"]["parts"][0]["text"]
            for item in requests
        ]

        for item in requests:
            config = item.get("embedContentConfig", item)
            assert config["taskType"] == "RETRIEVAL_DOCUMENT"
            assert config["outputDimensionality"] == 1536

        return httpx2.Response(
            200,
            json={
                "embeddings": [
                    {"values": _vec(text)}
                    for text in texts
                ]
            },
        )

    transport = httpx2.MockTransport(handler)

    client = genai.Client(
        api_key="test-key",
        http_options=types.HttpOptions(
            client_args={"transport": transport}
        ),
    )

    try:
        result = embed_texts(["alpha", "beta"], client)

        assert len(result.vectors) == 2
        assert all(
            len(vector) == EMBEDDING_DIMENSIONS
            for vector in result.vectors
        )

        assert result.vectors[0] == pytest.approx(
            embedder._normalize_vector(_vec("alpha"))
        )
    finally:
        client.close()

