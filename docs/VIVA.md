# Viva preparation: questions and accurate answers

## Problem and design

**What problem does CodeLens CI solve?**
Automated reviewers usually judge a pull request from its diff alone. Many defects
only show in relation to existing code: a caller that relies on a removed check, or a
convention the change violates. CodeLens retrieves the relevant existing code and
reviews the change with it, then validates every finding against the actual diff.

**Why a webhook + queue instead of reviewing inside the request?**
GitHub expects a fast response, and a review takes seconds to minutes (indexing plus
LLM calls). FastAPI only verifies the signature and enqueues the job. Celery does the
work, with retries and late acknowledgement, so a crashed worker re-delivers the
job instead of losing it.

**How do you prevent duplicate reviews from duplicate webhooks?**
A unique constraint on `reviews(pr_id, head_sha)`, plus
`INSERT … ON CONFLICT DO NOTHING` and `SELECT … FOR UPDATE`. A commit has exactly
one review row. A completed review is skipped; one running in another worker
(< 30 minutes old) is skipped. If analysis finished but posting failed, the review
is stored as `analyzed` and a retry only re-posts. The LLM is not called again, and
no second GitHub review is created.

## Code understanding and retrieval

**Why Tree-sitter?**
It parses code into a syntax tree, error-tolerantly. That gives whole functions and
classes with exact line numbers, instead of arbitrary text windows that cut a
function in half. Large units are split on statement boundaries with no code lost,
because the embedding model accepts at most 2048 tokens. We bound inputs at 1800
bytes, since a token covers at least one byte.

**Why embeddings?**
They let us find code by *meaning*: "check the user owns this order" matches
`get_order` without sharing words. We use `gemini-embedding-001` at 1536 dimensions,
L2-normalized. Documents are embedded with `RETRIEVAL_DOCUMENT` and queries with
`CODE_RETRIEVAL_QUERY`.

**Why Qdrant?**
It's an open-source vector database that runs locally for free. It does fast
cosine search, stores metadata payloads, supports one collection per repository
(isolation), and handles upserts and deletes for incremental re-indexing.

**Why BM25 + dense retrieval? What does RRF do?**
BM25 is excellent at exact identifiers (`calculate_discount`), while dense search
handles paraphrase. Their scores are on different scales. Reciprocal Rank Fusion
uses only ranks: score = Σ w / (k + rank), with k = 60 and weights (1, 1) by
default. Documents ranked high by both win; documents found by only one can still
appear.

**Honest result:** on our code-identifier queries, BM25 alone scored highest (v1). We
added natural-language queries and a validation-only parameter sweep, and we report
what the holdout shows rather than assuming hybrid wins.

**Why index the base commit and not the PR head?**
The context should be the code that *already exists*. The PR's own change comes
from the diff. Indexing is incremental: a chunk whose payload hasn't changed keeps
its vector. On `psf/requests`, a later base needed 71 new embeddings out of 341.

## Review pipeline

**Why LangGraph?**
The review is a stateful multi-step workflow with a conditional branch. Each node is
small and testable, the state is explicit, and per-node timings come free.

**The 11 steps:** understand changes → map added lines to changed functions →
retrieve context → bug pass → (security LLM pass + rules, or rules only) → quality
pass → validate → deduplicate → assign severity → score risk → render the review.

**How are findings validated?**
The model must quote evidence. A finding is dropped if its file isn't in the diff or
its evidence doesn't exist in the file (hallucination). Line numbers are corrected
from the evidence. A finding is **confirmed** only if the evidence is on added lines
and confidence ≥ 0.7; otherwise it is **possible**. Possible findings appear in the
summary, never as inline comments. Inline comments go only on lines GitHub accepts
(inside diff hunks).

**How is risk scored?**
Each finding contributes severity weight × confidence × (1 if confirmed, else 0.5)
× 1.5 if security × 1.25 if in a sensitive file. Change-size and sensitive-surface
points are added, and score = 1 − exp(−total / 10), giving 0..1. Labels are set by
thresholds, with floors (a confirmed critical finding is at least "high"). The full
breakdown is stored and shown in the dashboard.

**What about prompt injection in PR text or code?**
All PR text, diffs and code are wrapped in `<untrusted>` blocks, and the system
prompt says never to follow instructions inside them. The output is schema-validated
and must cite evidence that exists in the diff, so injected text can't produce an
unverifiable finding or change our instructions.

## Failure handling

**What happens when Gemini fails?**
* **Per-minute 429:** we wait the delay the API returns and retry.
* **Daily quota:** stop with a clear error.
* **Malformed or empty output:** one repair retry, then the pass is recorded as failed.
* **Unknown model (404):** the review stops loudly.

In every case the review states which pass failed, and **no finding is invented**.
The rules still run. If embeddings fail, retrieval falls back to BM25 (reported) or
the review runs without context (reported). The existing index is never corrupted:
deletion happens only after all new vectors are stored, and progress is kept per batch.

**Why only the free tier? Privacy?**
It's a ₹0 project. Google may use free-tier inputs to improve its products, so we
only review public repositories.

## Evaluation

**How do you know it works?**
Through labelled benchmarks with dev/validation/holdout splits, three
configurations (rules / LLM only / LLM + context) on identical cases, and strict
scoring: right file, ±2 lines and the right category. On top of that, real PRs from
`psf/requests`. Raw outputs, dataset and prompt hashes are recorded for every run.

**Known limitations?**
* Python only.
* Synthetic single-file benchmark cases.
* A subtle real regression (requests #7433) was missed.
* IDOR was mislabelled `bug` before the routing fix.
* Free-tier rate limits make large repositories slow to index the first time.
* The dashboard has no user accounts (optional password for public demos).
* Self-reported LLM confidence.
