# CodeLens CI — Architecture

## 1. Components

| Layer | Module | Responsibility |
|---|---|---|
| API | `app/api/webhook.py` | Verify `X-Hub-Signature-256` over the raw body (constant-time compare), route PR events, enqueue, return immediately; `503` if Redis is down |
| API | `app/api/reviews.py` | Read-only access to stored reviews and findings |
| Queue | `app/tasks/celery_app.py`, `review_task.py` | Celery over Redis; late acks; retry policy |
| Orchestration | `app/services/review_service.py` | Claim → index → fetch → review → persist → post |
| GitHub | `app/github/auth.py`, `client.py` | App JWT, installation tokens, REST calls, safe archive extraction, review posting |
| Code understanding | `app/core/diff_parser.py`, `chunker.py` | Diff → exact line maps; source → semantic chunks |
| Embeddings / index | `app/core/embedder.py` | Gemini embeddings, Qdrant collections, incremental indexing |
| Retrieval | `app/core/bm25.py`, `retriever.py` | BM25 + dense search fused with RRF |
| Review | `app/review/graph.py` (+ `llm.py`, `prompts.py`, `schemas.py`, `security_rules.py`, `risk.py`) | LangGraph pipeline |
| Persistence | `app/models/*`, `alembic/` | PostgreSQL schema and migrations |
| Evaluation | `app/evaluation/*`, `benchmarks/` | Datasets, metrics, runner |

## 2. End-to-end data flow

1. **Webhook.** GitHub sends a `pull_request` event (`opened`, `synchronize`, `reopened`,
   `ready_for_review`). The signature is verified, then `process_pr_review.delay(repo_id,
   full_name, pr_number, installation_id, title, author)` is enqueued and the API answers
   in milliseconds.
2. **Claim.** The worker fetches the PR (head and base SHA), upserts `repositories` and
   `pull_requests`, and claims the `reviews` row for `(pr_id, head_sha)` with
   `INSERT … ON CONFLICT DO NOTHING` plus `SELECT … FOR UPDATE`. A completed review means
   skip. A running one younger than 30 minutes means skip. `analyzed` means post only.
3. **Index.** If `repositories.indexed_sha != base_sha`, the base commit tarball is
   downloaded. Only regular `.py` files with safe relative paths are extracted. They are
   chunked and indexed incrementally under a per-repository Redis lock.
4. **Diff.** The PR files API is parsed into `ChangedFile`s. Changed file contents are
   fetched at `head_sha`.
5. **Review.** The LangGraph pipeline (section 5) runs.
6. **Persist.** In one transaction: review fields, risk breakdown, findings and retrieval
   logs are written, and the status becomes `analyzed`.
7. **Post.** One GitHub review (`event=COMMENT`) is posted: the summary plus inline
   comments on confirmed findings. If GitHub rejects positions (`422`), the summary is
   re-posted with the comments embedded. The status becomes `completed`.

Review states: `pending → running → analyzed → completed`, or `failed`, which can be
claimed again on redelivery. Because `analyzed` is stored before posting, a retry after
a GitHub outage only re-posts. The free-tier LLM analysis is never repeated.

## 3. Database

* `repositories`: `github_id` (BigInteger, unique, also names the Qdrant collection),
  `full_name`, `installation_id`, `indexed_sha`, `indexed_at`, `indexed_chunks`.
* `pull_requests`: unique `(repo_id, pr_number)`, `head_sha`, `status`.
* `reviews`: unique `(pr_id, head_sha)`, `status`, `error`, `attempts`, `risk_score`,
  `risk_label`, `risk_breakdown` (JSON), `issues_found`, `files_reviewed`, `model`,
  `retrieval_mode`, `summary`, `raw_output` (JSON), `total_tokens`, `total_cost_usd`,
  `total_latency_ms`, `github_review_id`, timestamps.
* `findings`: unique `(review_id, fingerprint)`, title, description, category, severity,
  confidence, status, source, file, lines, `comment_line`, evidence, explanation, fix,
  context refs, `posted_inline`.
* `retrieval_logs`: per query: strategy, chunk ids, dense/BM25/fused scores, dense
  latency, BM25 latency, degradation reason, review id.

* `review_files`: one row per changed file of a reviewed commit (status, +/-, patch,
  skip reason). Powers the dashboard's diff viewer.
* `repositories.default_branch` and `private` come from the PR payload or the
  `installation` webhook.

Migrations: `6c55a024860c` (initial), `948c7ae88c69` (unique PR, BigInteger id),
`b7d2e4f19a30` (review pipeline), `c41e8a7d5b62` (dashboard: stored diffs). All are additive. `alembic check` reports no drift.

## 4. Retrieval

**Chunking.** Tree-sitter parses Python into functions, methods and classes, with
decorators included and nested functions kept inside their parent. If a unit's text
exceeds `MAX_EMBED_BYTES = 1800`, it is split into *parts* that end only before a
statement or clause boundary. Every source line belongs to exactly one part. A large
class is split only over its own lines, because its methods are separate chunks. A
single over-long line is cut into byte windows on UTF-8 character boundaries.
**No source is ever truncated or dropped.**

**Why 1800 bytes.** `gemini-embedding-001` accepts 2048 input tokens. A token covers at
least one byte, so N bytes are at most N tokens. A byte budget therefore guarantees the
limit without a tokenizer. Parts get a short header (`# file :: function name (part i/n)`),
and 200 bytes are reserved for it.

**Identity.** Point id = `uuid5(file:type:qualified_name:start_line[#partN])`. Unsplit
chunks keep their Day 8 ids and payloads.

**Exclusions.** Tests, migrations, vendored and `third_party` code, virtualenvs,
generated files (`_pb2.py`, "DO NOT EDIT" headers), backup copies (`*_backup.py`,
`*_old.py`, `* copy.py`, `*(1).py`, `backup/`), and files over 1 MB.

**Indexing.** Indexing is incremental: a point whose stored payload equals the new
chunk's payload keeps its vector. The order is embed → create collection if missing →
upsert → prune stale points. A failure before pruning never deletes data, and re-running
converges.

**Hybrid search.** Each query is a changed unit (`file qualified_name` + code).
* Dense: `CODE_RETRIEVAL_QUERY` embedding → Qdrant cosine top-N.
* BM25: Okapi BM25 over `file + qualified name + text`. The tokenizer keeps identifiers
  and also splits snake_case and camelCase.
* Fusion: `RRF(d) = Σ 1/(60 + rank_i(d))`. Rank-based fusion avoids calibrating
  incomparable score scales.
* Post-processing: exclude the changed unit's own previous version, deduplicate, and
  collapse multiple parts of one function.
* If dense search fails (for example, quota), the result is BM25-only with
  `degraded_reason` set. The degradation appears in the review notes and in
  `retrieval_logs`.

*Alternatives considered:* dense-only (weak on exact identifiers), BM25-only (weak on
paraphrase), weighted score sums (need per-repository tuning), and a cross-encoder
re-ranker (extra model or API calls, which conflicts with the zero-cost constraint).

## 5. LangGraph review pipeline

```
understand_changes → analyze_changed_code → retrieve_context → detect_bugs
   → route_security ─┬─ security_analysis   (LLM pass + rules)   if sensitive files or a rule hit
                     └─ security_rules_only (rules)              otherwise
   → quality_analysis → validate_findings → deduplicate_findings
   → assign_severity → score_risk → generate_review
```

* **understand_changes:** stats, skipped files with reasons, and security-sensitive files
  (the path, an added line or a **removed** line matches auth, token, owner, role, payment,
  sql, upload, shell, ...). Removed lines count because deleting an authorization check
  is a security change even when the added lines look harmless.
* **analyze_changed_code:** maps added lines to their innermost function or method from
  the head version. Module-level additions become `<module>` units.
* **retrieve_context:** hybrid retrieval for up to 12 units. Context chunks get labels
  `C1…Cn`.
* **detect_bugs / security / quality:** one Gemini call each, with structured JSON output
  validated by Pydantic and one repair retry. A failed pass is recorded in `errors`. The
  review continues and says what is missing. Nothing is invented.
* **validate_findings:** drops findings whose file is not in the diff or whose evidence
  is not in the file. It corrects line numbers from verified evidence. A finding is
  **confirmed** only if its evidence is on added lines, it overlaps the change and its
  confidence is at least 0.7. Otherwise it is **possible**: listed in the summary, never
  posted inline. Unknown context labels are removed.
* **deduplicate_findings:** same file, lines within 3, and the same category, a similar
  title (Jaccard ≥ 0.5) or identical evidence, keeping the strongest. Sources are merged
  (`llm:security+rule:SEC002`).
* **assign_severity:** possible findings are capped at medium. Confidence below 0.5 is
  capped at low. A confirmed security finding is never `info`.
* **score_risk:** see section 6.
* **generate_review:** Markdown summary (risk, issue table, possible concerns, notes,
  skipped files) and inline comments.

**Locked decision kept:** security is not a separate always-on agent. It is one routing
decision. The extra LLM security pass runs only when the change is security-relevant,
which saves free-tier quota.

**Prompt-injection defence:** PR text, diffs and code are wrapped in `<untrusted>` blocks.
The system instruction states that instructions inside them must never be followed. All
output is schema-validated and grounded by `validate_findings`, so injected text cannot
create unverifiable findings or change the system prompt.

## 6. Risk score

```
contribution(f) = W[severity] × confidence × (1.0 confirmed | 0.5 possible)
                  × (1.5 if security) × (1.25 if sensitive file)
W = {critical: 10, high: 6, medium: 3, low: 1, info: 0}
raw   = Σ contribution + min(2, log2(1 + changed_lines/50)) + (1 if a sensitive file changed)
score = 1 − exp(−raw / 10)              labels: ≥.75 critical, ≥.50 high, ≥.25 medium, else low
floors: confirmed critical → ≥ high;  confirmed high security → ≥ medium
```

Saturating (0..1), monotonic in every input, and fully stored in `reviews.risk_breakdown`.

## 7. GitHub integration

* App authentication: an RS256 JWT (`iss` = App ID, valid for 9 minutes) is exchanged for
  an installation token via `POST /app/installations/{id}/access_tokens`. Tokens are
  cached per installation and refreshed 5 minutes before expiry. A PAT fallback exists
  for demos.
* Error classification: `403`/`429` rate limits retry at `x-ratelimit-reset` or
  `retry-after`. `5xx` and network errors retry with backoff. Other `4xx` errors fail the
  review with the error stored.
* Inline comments use `line` + `side=RIGHT` (new-file numbering) and are placed only on
  lines inside diff hunks.

## 8. Security decisions

* Webhook HMAC over raw bytes with `hmac.compare_digest`. Invalid or missing signatures
  return `401`.
* Untrusted repository content is never executed: archives are parsed in Python, links,
  devices and `..` paths are skipped, and size limits apply. The container runs as a
  non-root user.
* Secrets come only from the environment. HTTP client debug logging is suppressed so
  authorization headers are never logged. `.env` and `*.pem` are git- and docker-ignored.
* Repository isolation: one Qdrant collection per repository id, and a retriever instance
  is bound to one collection.
* Duplicate webhook deliveries are handled by the per-commit review claim.

## 9. Observability

* Logs: text, or JSON lines with `LOG_FORMAT=json`. They cover the task lifecycle,
  indexing stats (embedded, reused, pruned), retrieval degradation and review outcome.
* Per review: node timings, LLM calls (tokens, latency, attempts), dropped findings with
  reasons, and errors (`reviews.raw_output`).
* `retrieval_logs`: every query with scores and latencies (evaluation data).
* LangSmith: set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` (free developer tier).
  LangGraph emits traces automatically.

## 10. Dashboard

* React 18 + Vite + Tailwind in `frontend/`. `npm run build` writes `frontend/dist`,
  which FastAPI serves at `/`, with a client-side routing fallback. The Docker image
  builds it in a Node stage, so users do not need Node.
* Data comes only from `/api/*` (`app/api/dashboard.py`). These are read-only queries
  over PostgreSQL and `benchmarks/results/*.json`. The dashboard has no local state
  and no sample data.
* Screens: Overview, Repositories (+ detail), Pull requests (filters), PR review
  history, Review detail (findings / code diff with findings on their lines / risk
  and pipeline), Analytics (activity, categories, per-repo stats, benchmark results).
* Charts: fixed status palette (low → critical) always paired with text labels;
  categorical colors validated for color-vision deficiency on the dark surface;
  single-axis charts only.
* Dashboard paths (`/review/:id`) are kept distinct from the JSON API paths
  (`/reviews/{id}`), so a page reload never returns raw JSON.

## 11. Installation events

`installation` (created) and `installation_repositories` (added) webhooks enqueue
`register_repositories`, which upserts the repositories. A repository therefore
shows on the dashboard as soon as the App is installed. Indexing still happens on
its first pull request, at that PR's base commit.

## 12. Deployment layout (Docker Compose)

`postgres`, `redis`, `qdrant`, then `migrate` (runs `alembic upgrade head` and
exits), then `api` (FastAPI + dashboard, healthchecked) and `worker` (Celery).
An optional `tunnel` profile runs `smee-client` to forward GitHub webhooks from a
smee.io channel to `api:8000/webhook`. The private key is mounted read-only from
`./secrets`, and `benchmarks/results` is mounted so the Analytics page shows reports
generated on the host.
