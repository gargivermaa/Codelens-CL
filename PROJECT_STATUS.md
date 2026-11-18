# CodeLens CI — Project Status

Branch: `claude/full-project-implementation` (based on `d6a1998`). Last updated: 2026-11-18.

**Legend:**
* ✅ implemented and verified by running it
* 🧪 implemented and verified against mocks or fakes only; needs a real external service
* ⏳ not done

## 1. Milestones

| # | Milestone | Status | Evidence |
|---|---|---|---|
| 1 | FastAPI, config, webhook + HMAC | ✅ | `test_webhook.py`, live run (section 4) |
| 1 | PostgreSQL models + Alembic | ✅ | migrations up/down/up on PG 16, `alembic check` clean, `test_db.py` |
| 1 | Redis + Celery | ✅ | live worker consumed a webhook job (section 4); retry policy in `test_tasks_and_api.py` |
| 2 | Tree-sitter chunking, lossless splitting, exclusions | ✅ | `test_chunker.py` (33) |
| 3 | Gemini embeddings (batching, normalization, retry, quota errors, query task type) | 🧪 | real `google-genai` SDK against a mock HTTP transport. **The previous real run (54 chunks, `repo_1289349644`) predates this branch.** |
| 3 | Qdrant indexing: incremental, failure-safe, per-repo | ✅ | `test_embedder.py` (34) on qdrant-client local mode |
| 4 | Hybrid retrieval (BM25 + dense + RRF) | ✅ BM25 / 🧪 dense | `test_retriever.py` (12); BM25 benchmark measured |
| 5 | LangGraph review pipeline (11 nodes + conditional security) | 🧪 | `test_review.py` (16) with a scripted LLM; Gemini client tested through the real SDK with mock HTTP |
| 5 | Security rules | ✅ | measured on the benchmark (section 5) |
| 6 | Risk scoring | ✅ | documented formula, `test_review.py` |
| 6 | Persistence of reviews, findings, retrieval logs | ✅ | `test_review_service.py` against real PostgreSQL |
| 7 | GitHub App auth, PR fetching, review posting | 🧪 | `test_github.py` (14) with the real httpx client and mock transport |
| 8 | End-to-end orchestration | ✅ with fakes | `test_review_service.py`: real PG + real graph + in-memory Qdrant; GitHub and Gemini faked |
| 9 | Evaluation framework + datasets | ✅ | `test_evaluation.py`; BM25 and rules-only baselines measured |
| 9 | Observability (JSON logs, timings, retrieval logs, LangSmith switch) | ✅ / 🧪 LangSmith | |
| 10 | Dockerfile + Compose | 🧪 | `docker compose config` valid; images not built here (no Docker daemon) |
| 10 | README, ARCHITECTURE, this file | ✅ | |
| — | Real GitHub PR reviewed end-to-end | ⏳ | needs a GitHub App or token and a test repository (section 6) |
| — | Dense, hybrid and LLM benchmark numbers | ⏳ | needs `GEMINI_API_KEY` (section 6) |

## 2. Test results (actual)

`pytest -q`: **150 passed**. This was run on Python 3.13 and again on Python 3.11 (a clean
install of `requirements.txt`). PostgreSQL 16 and Redis 7 ran locally. No API keys and
no network calls were used.

| File | Passed |
|---|---|
| test_chunker.py | 33 |
| test_embedder.py | 34 |
| test_retriever.py | 12 |
| test_review.py | 16 |
| test_github.py | 14 |
| test_tasks_and_api.py | 10 |
| test_review_service.py | 7 |
| test_evaluation.py | 7 |
| test_webhook.py | 7 |
| test_db.py | 5 |
| test_diff_parser.py | 5 |

Baseline on the original commit `d6a1998`: 41 passed, after the `requirements.txt` fix.

## 3. Changes on this branch

| Commit | Summary |
|---|---|
| `fix(deps)` | `requirements.txt` was not installable: bare `--- Added Day N ---` lines, and pydantic 2.10.4 conflicted with google-genai 3.5.0, which needs ≥ 2.12.5. Added langgraph, PyJWT and cryptography. |
| `feat(core)` Day 9 | Lossless syntax-aware splitting (fixes the "8 inputs truncated at 1800 bytes" loss); backup, vendored and generated exclusions; incremental indexing; retry and quota handling; `embed_query` with `CODE_RETRIEVAL_QUERY`; removed the duplicate `OPENAI_API_KEY`, stale OpenAI comments and the unused `openai` dependency. |
| `feat(retrieval)` Day 10 | BM25 + dense + RRF hybrid retriever. |
| `feat:` schema/diff/GitHub | Migration `b7d2e4f19a30`, diff parser, GitHub App client. |
| `feat(review)` | LangGraph pipeline, validation, dedup, severity, risk, rules. |
| `feat:` wiring | Review service, Celery task, read API, CLI, JSON logging. |
| `feat(eval)` | Benchmarks, metrics, measured baselines. |
| `docs/docker` | Dockerfile, Compose, `.env.example`, README, ARCHITECTURE, this file. Removed `readme.md`: it duplicated `README.md`, and two names differing only in case break checkouts on Windows. Its content is merged into `README.md`. |

**Behaviour changes to existing code, and why:**
* Two Day 7 and Day 8 tests asserted the old *truncation* behaviour that this assignment
  asks to eliminate. They now assert lossless splitting instead.
* `test_gemini_failure_leaves_existing_index_intact` now re-indexes *changed* code.
  Incremental indexing correctly makes no API call for unchanged code, so the old test
  could no longer trigger the failure.
* Chunks larger than 1800 bytes get new ids (`#partN`). They were previously stored
  truncated, so re-indexing replaces them. Ids of unsplit chunks are unchanged; the
  golden-value test still passes.
* `reviews.github_comment_id` and `repositories.installation_id` were widened to
  BigInteger, because GitHub ids exceed 2³¹.

## 4. Commands executed here (selection)

```
alembic upgrade head; alembic downgrade -1; alembic upgrade head; alembic check  -> no drift
pytest -q                                                                          -> 150 passed (3.13 and 3.11)
python -m app.evaluation.run retrieval        -> bm25 measured; dense/hybrid skipped (no key)
python -m app.evaluation.run review --mode rules
docker compose config -q                       -> valid
```

Live stack: uvicorn and a Celery worker (`-P solo`), with Redis and PostgreSQL.
`scripts/send_test_webhook.py` → `200 {"status":"queued"}`. The worker received the
task and called `GET /repos/octocat/hello-world/pulls/101`, which this sandbox's
network refused with `403`. It was correctly classified as permanent, so it was not
retried. The plumbing up to the GitHub call is verified; the GitHub part is not.

## 5. Measured benchmark results

All numbers come from `benchmarks/results/*.json`. The corpus is the 23-chunk
`benchmarks/sample_repo`, so it is small and absolute values will not transfer to
large repositories.

**Retrieval, BM25 only** (15 labelled queries, k = 5):

| mode | P@1 | P@3 | P@5 | R@5 | MRR | median latency |
|---|---|---|---|---|---|---|
| bm25 | 0.933 | 0.578 | 0.373 | 0.978 | 0.967 | 0.04 ms |
| dense | not run (needs `GEMINI_API_KEY`) | | | | | |
| hybrid | not run (needs `GEMINI_API_KEY`) | | | | | |

P@5 is bounded by the label count: most queries have 1–2 relevant chunks, so the
maximum P@5 is about 0.4.

**Review, rules only, no LLM** (8 seeded defects, 3 clean PRs): precision 1.00,
recall 0.375 (3 of 8: weak random token, `shell=True`, hardcoded password), clean-case
false-positive rate 0.00. The five defects that need reasoning (IDOR, path traversal,
SQL f-string through a wrapper, two arithmetic bugs) are for the LLM; `--mode llm` has
not been run yet.

## 6. Blockers and what the owner must run

These could not be verified in this environment. None of them are faked.

1. **Real Gemini calls.** The sandbox has no API key, and the docs site
   (ai.google.dev) was not reachable, so free-tier model availability could not be
   re-checked today. To verify, run:
   * `python -m app.cli index-local benchmarks/sample_repo --github-id 1`
   * `python -m app.evaluation.run retrieval`
   * `python -m app.evaluation.run review --mode llm`

   `GEMINI_REVIEW_MODEL` defaults to `gemini-2.5-flash`. Confirm it is listed as free
   of charge at <https://ai.google.dev/gemini-api/docs/pricing>, or switch to a listed
   free Flash or Flash-Lite model.
2. **Real GitHub.** Create a GitHub App (README → Connecting GitHub), install it on a
   *public* test repository, and point a smee.io tunnel at `/webhook`. Opening a PR is
   then the end-to-end test. A quicker check: `python -m app.cli review OWNER/REPO N
   --no-post` with `GITHUB_TOKEN`.
3. **Docker images.** Run `docker compose up --build` on your machine.
4. **Push.** This session's GitHub access was read-only, so the branch exists only in
   the bundle handed over with this work. See the handoff message for how to apply it.

## 7. Known limitations

* Review and chunking support Python only. Other files are skipped with a reason shown
  in the summary. Adding a language means adding its Tree-sitter grammar and node types
  in `chunker.py` and `REVIEWABLE_EXTENSIONS`.
* Indexing re-downloads the base commit's tarball when the base SHA changes. Embedding
  is incremental, but the download and chunking are not.
* The BM25 index is rebuilt in memory per review from Qdrant payloads. That is fine for
  repositories up to tens of thousands of chunks.
* Free-tier rate limits make very large PRs slow: 3 LLM calls per PR plus one embedding
  call per changed unit (at most 12).
* A model's confidence is self-reported. Validation grounds the evidence but cannot
  prove a finding is correct, which is why "possible" findings are never posted inline.
* Benchmarks are small and hand-labelled. They show the method, not production-scale
  accuracy.

## 8. Next steps

1. Get a free Gemini key and run the three verification commands in section 6.1. Paste
   the results into section 5.
2. Run a real PR on a public test repository via the GitHub App (section 6.2).
3. `docker compose up --build` on your machine.
4. Optional: a JavaScript/TypeScript grammar; a GitHub check run showing the risk label;
   more benchmark cases taken from real PRs.
