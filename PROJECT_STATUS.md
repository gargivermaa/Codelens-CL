# CodeLens CI — Project Status

Branch: `claude/full-project-implementation` (based on `d6a1998`). Last updated: 2026-11-18.

Status keys:
* ✅ **Verified**: implemented and run for real.
* 🧪 **Not integration-tested**: implemented and tested with mock HTTP or fakes only.
* ⏳ **Pending owner action**: needs the owner's laptop, keys or GitHub account.

## 0. Acceptance criteria

| # | Criterion | Status | Evidence / what is left |
|---|---|---|---|
| 1 | Existing and new tests pass | ✅ | 168 passed (this environment); 157 on the owner's laptop before the latest additions |
| 2 | IDOR benchmark re-run after the fix | ⏳ | r03 is in the v2 **dev** split: `python -m app.evaluation.run review --config llm_context --split dev` |
| 3 | Reproducible raw outputs | ✅ | every run stores run id, dataset and prompt hashes, model, generation config, raw model output, tokens, latency, failures |
| 4 | Rules vs LLM-only vs LLM+context | ✅ framework / ⏳ LLM numbers | rules measured on holdout; `--config all --split holdout` on the laptop gives the LLM numbers |
| 5 | Honest retrieval evaluation | ✅ framework / ⏳ dense numbers | 40 queries, NDCG@5, validation-only sweep; BM25 measured; dense/hybrid need the key |
| 6 | All dashboard screens with real data | ✅ | 7 screens incl. Evaluation; verified with persisted reviews and real benchmark files; no console errors; no overflow at 390 px |
| 7 | Docker stack on the Windows laptop | ✅ | owner's laptop: all services up, api healthy, dashboard served |
| 8 | Real GitHub PR triggers an automatic review | ⏳ | needs the GitHub App installed on `codelens-demo` (README → GitHub App setup) |
| 9 | Inline comments on GitHub | ⏳ | same step as 8 |
| 10 | Dashboard shows the same review | ✅ for CLI-triggered real PRs (psf/requests #7502, #7433) / ⏳ for webhook-triggered | |
| 11 | ₹0 deployment or local alternative | ✅ | docs/DEPLOYMENT.md: local Docker + Cloudflare Quick Tunnel + smee; dashboard password |
| 12 | README and architecture docs | ✅ | README, ARCHITECTURE, docs/* |
| 13 | Slides and viva preparation | ✅ viva, demo script, report, diagrams / ⏳ slides | the slides are built after the LLM holdout run, so they show final measured numbers |
| 14 | No secrets exposed | ✅ | `.env` and `secrets/` are git- and docker-ignored; nothing logged; zip contains no secrets |
| 15 | Reproducible demo from the instructions | ⏳ | confirmed once steps 8–9 run on the laptop |

## 1. Component status

| Area | Status | Evidence |
|---|---|---|
| FastAPI, webhook HMAC, ping/installation events, dashboard password | ✅ | tests; live uvicorn + Celery runs |
| PostgreSQL schema (4 additive migrations) | ✅ | up/down/up; `alembic check` clean; owner's laptop migrated |
| Redis + Celery | ✅ | live worker; Docker on the owner's laptop |
| Tree-sitter chunking (lossless splitting) | ✅ | tests; 342 chunks of psf/requests |
| Gemini embeddings + Qdrant (incremental, free-tier pacing) | ✅ | real runs: 342 chunks; incremental re-index 71 embedded / 270 reused |
| Hybrid retrieval | ✅ | real reviews used "hybrid"; benchmarks |
| LangGraph pipeline with Gemini | ✅ | real runs, 0 LLM errors (benchmark v1, psf/requests) |
| Persistence + dashboard API | ✅ | real-PR reviews stored and shown |
| React dashboard | ✅ | 7 screens; production build |
| GitHub App auth + posting reviews | 🧪 | mock-transport tests only; ⏳ real run |
| Evaluation v2 framework | ✅ | rules + BM25 measured on holdout; LLM configs ⏳ |
| LangSmith tracing | 🧪 | env switch only |

## 2. Test results

`pytest -q`: **168 passed** (Python 3.13 and Python 3.11 in this environment;
the owner's laptop passed 157 before the evaluation v2 additions).

| File | Passed |
|---|---|
| test_chunker.py | 33 |
| test_dashboard_api.py | 5 |
| test_db.py | 5 |
| test_diff_parser.py | 5 |
| test_embedder.py | 38 |
| test_evaluation.py | 10 |
| test_github.py | 14 |
| test_retriever.py | 12 |
| test_review.py | 18 |
| test_review_service.py | 9 |
| test_tasks_and_api.py | 12 |
| test_webhook.py | 7 |

## 3. Problems found and fixed (audit log)

| Problem | Fix |
|---|---|
| `requirements.txt` not installable; pydantic pin conflicted with google-genai | fixed pins |
| Oversized chunks silently truncated before embedding | lossless syntax-aware splitting |
| `docker-compose.yml` did not publish Postgres/Redis ports to the host | ports added |
| A retired review model (404) produced silent rules-only reviews | `LLMConfigurationError` stops the review |
| `gemini-2.5-flash` unavailable to new keys | default `gemini-3.5-flash-lite` (verified on owner's key) |
| Missing `GEMINI_API_KEY` failed the whole review during indexing | degrades to "no repository context" (noted in the review) |
| Dashboard route `/reviews/:id` collided with the JSON API `/reviews/{id}` | dashboard uses `/review/:id` |
| Docker created a *directory* when `github_app.pem` was missing | key now lives in `./secrets/` (mounted read-only) |
| Security pass skipped when a PR *removed* an authorization check (IDOR r03) | removed lines count toward security routing |
| Duplicate `readme.md`/`README.md` broke Windows checkouts | merged |

## 4. Measured benchmark results

All numbers are real runs; the JSON reports are in `benchmarks/results/`. The
corpus is the 23-chunk `benchmarks/sample_repo`, so it is small and absolute values
will not transfer to large repositories.

**Retrieval** (15 labelled queries, k = 5; real `gemini-embedding-001`, 1536-d,
measured on the owner's laptop, 2026-11-18):

| mode | P@1 | P@3 | P@5 | R@5 | MRR | median latency |
|---|---|---|---|---|---|---|
| bm25 | 0.933 | 0.578 | 0.373 | 0.978 | 0.967 | 0.19 ms |
| dense | 0.733 | 0.489 | 0.333 | 0.878 | 0.867 | 1180 ms |
| hybrid (RRF) | 0.867 | 0.556 | 0.360 | 0.944 | 0.933 | 1150 ms |

Reading the results: the queries are changed code that shares exact identifiers with
its targets, and the corpus is tiny, which favours BM25. Dense retrieval matches by
meaning but is weaker on exact names. Hybrid beats dense on every metric and stays
close to BM25, which is why it is the default. Hybrid latency is almost entirely the
Gemini query-embedding call. P@5 is capped near 0.4 because most queries have only
1–2 relevant chunks.

**Review** (8 seeded defects, 3 clean PRs; review model `gemini-3.5-flash-lite`,
free tier, 0 failed LLM calls; measured on the owner's laptop, 2026-11-18):

| configuration | precision | recall | clean-PR FP rate | median time / PR |
|---|---|---|---|---|
| rules only (no LLM) | 1.00 | 0.375 (3/8) | 0.00 | 3.6 ms |
| LLM + hybrid context, confirmed findings | **0.875** (7/8) | **0.875** (7/8) | **0.00** | 6.3 s |
| LLM + hybrid context, incl. possible | 0.778 (7/9) | 0.875 | 0.00 | |

Per case: weak token, SQL injection, discount bug, path traversal, command
injection, hardcoded password and tax bug were all found and confirmed. The three
clean PRs got no findings. Two cases need explanation, and the labels were
**not** changed after seeing the results:
* **r03 (IDOR) counts as missed.** The model reported "Authorization check removed
  from get_order" on the right line, but as category `bug` with `possible`
  status, and the protocol requires a security-category match.
* **r04's extra finding counts as the one false positive.** "Discount calculation
  missing percentage cap" is arguably a real issue: the seeded change also removed
  the `MAX_DISCOUNT_PERCENT` cap, but the label only lists the `// 10` bug.

Latency: mean 8.1 s, p95 20.0 s per PR (3 LLM calls plus retrieval, with the
client-side free-tier throttle of 1 request per second).

Model note: `gemini-2.5-flash` returned 404 ("no longer available to new users").
`gemini-3.8-flash` was available but returned 503 (overloaded) during testing.
`gemini-3.5-flash-lite` worked and is now the default. With no billing account
linked, a model outside the free tier can only fail with 429; it cannot incur charges.

**IDOR (r03) root cause, found after the run.** The security-focused LLM pass only
runs when a change looks security-relevant. That check looked at the file path and
the **added** lines. In r03 the added line (`if order is None:`) is harmless; the
authorization check is on the **removed** line. So the security pass never ran and
only the bug pass saw the issue, which explains the `bug` label. Since commit
"fix(review): route removed security checks to the security pass", removed lines are
also considered (test: `test_removing_an_authorization_check_routes_to_the_security_pass`).
**The LLM benchmark has not been re-run since this fix**, so the numbers above are
from before it. Re-run `python -m app.evaluation.run review --mode llm` and update
this table; the label for r03 must not be changed.

## 5. What the owner must do to finish

1. GitHub App + webhook + `codelens-demo`: follow README → *GitHub App setup* and
   *Demo*. This is the first real test of posting reviews and inline comments.
2. `docker compose up --build` on the laptop (README → *Run with Docker*).
3. Re-run `python -m app.evaluation.run review --mode llm` after the routing fix and
   update section 4.
4. Push the branch (README → *Pushing this branch*). This session had read-only
   GitHub access.

## 6. Known limitations

* Review and chunking support Python only; other files are listed as skipped.
* Indexing downloads the base commit's tarball whenever the base SHA changes
  (embedding is incremental; download and chunking are not).
* BM25 is rebuilt in memory per review from Qdrant payloads (fine up to tens of
  thousands of chunks).
* Free-tier rate limits: about 3 LLM calls per PR plus one embedding call per changed
  unit (max 12). Overload (503) and quota (429) errors are retried, then reported.
* LLM confidence is self-reported; validation grounds the evidence but cannot prove a
  finding correct, which is why "possible" findings are never posted inline.
* Benchmarks are small and hand-labelled; they show the method, not production accuracy.
* The dashboard has no login. Run it on localhost only; do not expose port 8000 publicly
  without adding authentication.
