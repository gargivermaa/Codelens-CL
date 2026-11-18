# CodeLens CI: Repository-Aware AI Code Review with Retrieval-Based Reasoning

*Technical report, final-year B.Tech (CSE, AI) project. Team: Ashutosh Choudhary,
Gargi Verma, Vaibhav Agarwal.*

## Abstract

CodeLens CI is a GitHub App that reviews pull requests using the repository's own
code as context.
* **Indexing:** the base commit is parsed with Tree-sitter into functions and classes,
  embedded with Gemini, and stored in Qdrant.
* **Retrieval:** for every changed function, related existing code is retrieved with
  hybrid BM25 + dense retrieval fused by Reciprocal Rank Fusion.
* **Review:** an 11-step LangGraph pipeline asks a language model for bugs,
  security issues and quality problems in structured JSON. Every finding is
  validated against the actual diff, de-duplicated, assigned a severity and turned
  into a transparent risk score.
* **Output:** results are stored in PostgreSQL, posted to GitHub as a review with
  inline comments, and shown in a React dashboard.

We evaluate on a labelled benchmark of 51 pull requests with dev/validation/holdout
splits, comparing deterministic rules, an LLM without context, and the LLM with
repository context. We also report on real pull requests from `psf/requests`. The
whole system runs at zero cost on free tiers and local infrastructure.

## 1. Problem

Code review is a bottleneck, and automated reviewers typically see only the diff.
Defects often live in the relationship between a change and existing code: a
removed authorization check, a caller's contract, a project convention. Reviewing
the diff in isolation misses them, and plain LLM reviewers also produce
unverifiable or hallucinated comments.

## 2. Objectives

1. Retrieve the existing code that is relevant to each change.
2. Produce grounded findings: right file, right line, quoted evidence.
3. Integrate into the developer workflow (GitHub reviews) reliably and idempotently.
4. Measure accuracy honestly, including the contribution of repository context.
5. Zero cost.

## 3. System design

See `ARCHITECTURE.md` and `docs/DIAGRAMS.md`. Key components:

| Component | Choice | Reason |
|---|---|---|
| API | FastAPI | Async, typed; webhook HMAC verification over raw bytes |
| Async work | Celery + Redis | Retries, late acknowledgement, a fast webhook response |
| Parsing | Tree-sitter | Error-tolerant syntax trees, exact line ranges |
| Embeddings | gemini-embedding-001 (1536-d) | Free tier; asymmetric document/query task types |
| Vector store | Qdrant | Local, free, per-repository collections, payload storage |
| Lexical retrieval | BM25 (code-aware tokenizer) | Exact identifiers |
| Fusion | Reciprocal Rank Fusion | Combines incomparable score scales by rank |
| Orchestration | LangGraph | Explicit state, a conditional security branch, testable nodes |
| Review model | Gemini Flash-Lite (free tier) | Structured JSON output |
| Persistence | PostgreSQL + Alembic | Idempotency constraints, review history |
| UI | React + Vite + Tailwind | Dashboard and evaluation views |

## 4. Implementation highlights

* **Lossless chunking.** Units above the embedding budget are split on statement
  boundaries; no source line is dropped (tested).
* **Incremental, failure-safe indexing.** Deterministic point ids; unchanged chunks
  keep their vectors. The order is embed → upsert → prune, with progress kept per
  batch. Free-tier pacing came from a real failure: the quota counts each input of a
  batch.
* **Grounded findings.** Evidence must exist in the file; confirmed vs possible;
  inline comments only on lines inside diff hunks; line numbers corrected from evidence.
* **Security routing.** A security LLM pass runs only for security-relevant
  changes, which includes *removed* lines. This was added after a removed
  authorization check (IDOR) was mislabelled.
* **Idempotency.** One review per (PR, head commit). A post failure is retried
  without re-running the LLM.
* **Prompt-injection resistance.** Untrusted content is delimited; the output is
  schema-validated and grounded.
* **Risk score.** `1 − exp(−(findings + size + surface) / 10)`, with documented
  weights and floors.

## 5. Evaluation

The methodology and current numbers are in `docs/EVALUATION.md`. Summary of what is
measured so far:

* **Rules only, holdout (28 PRs):** precision 1.00, recall 0.24, 0% false positives
  on clean PRs. Precise, but they catch only well-known API misuse.
* **LLM only (no repository context), holdout:** precision 0.889, recall 0.762,
  F1 0.821, 0% clean-PR false positives, category accuracy 0.941.
* **LLM + context, v1 (11 PRs, before the routing fix):** precision 0.875, recall
  0.875, 0% clean-PR false positives with `gemini-3.5-flash-lite`.
* **Retrieval:** BM25 P@1 0.933 / MRR 0.967 on the v1 queries. Dense 0.733 / 0.867.
  Hybrid 0.867 / 0.933.
* **Real-world:**
  * requests #7502 (a correct fix): no findings, the correct outcome.
  * requests #7433 (a subtle regression): missed.

The decisive comparison, `llm` vs `llm_context` on the v2 holdout split, is run with
`python -m app.evaluation.run review --config all --split holdout`. Its results are
added to `docs/EVALUATION.md` as measured.

## 6. Limitations

* Python only.
* The benchmark is synthetic and single-file on a small app.
* The sample size is small.
* LLM confidence is self-reported.
* Free-tier rate limits.
* The dashboard has no multi-user accounts.
* There is no free always-on hosting (see `docs/DEPLOYMENT.md`).

## 7. Future work

* Multi-language support (JavaScript/TypeScript Tree-sitter grammars).
* Call-graph-aware retrieval (callers/callees) on top of BM25 and dense retrieval.
* Real-world labelled datasets mined from bug-fix commits.
* Learning from accepted and dismissed comments.
* A GitHub check-run status.

## 8. Reproducibility

```powershell
docker compose up --build -d
```

```powershell
pytest -q
```

```powershell
python -m app.evaluation.run review --config all --split holdout
```

```powershell
python -m app.evaluation.run retrieval --split holdout
```

Every benchmark run records the run id, dataset hash, prompt hash, model and
generation config, raw model output, tokens, latency and API failures.
