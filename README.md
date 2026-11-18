# CodeLens CI

**Repository-aware AI code review for GitHub pull requests, built on retrieval-based reasoning.**

Most AI reviewers send only the diff to a language model. CodeLens CI indexes the
whole repository, retrieves the existing code that a change interacts with
(callers, callees, similar logic) using **hybrid BM25 + dense retrieval**, and
reviews the change with that context in a **LangGraph** pipeline. Findings are then
validated against the actual diff, de-duplicated, risk-scored with a documented
formula, stored in PostgreSQL, and posted to the PR as a review with inline comments.

Everything runs locally on free infrastructure. The only external APIs are the
**Gemini API free tier** and **GitHub**.

## Features

| Area | What it does |
|---|---|
| Webhook | HMAC-SHA256 signature verification, immediate hand-off to Celery, `503` when the queue is down |
| Async processing | Celery + Redis, late acks, retries on GitHub rate limits and transient failures |
| Idempotency | Each PR commit (`pr_id`, `head_sha`) is reviewed once, however many deliveries or retries arrive |
| Diff parsing | Exact new-file line numbers, commentable lines, renames, deletions, binaries, lockfiles, large diffs |
| Chunking | Tree-sitter functions/methods/classes; oversized units split on statement boundaries **without losing code** |
| Embeddings | `gemini-embedding-001`, 1536-d, L2-normalized, `RETRIEVAL_DOCUMENT` / `CODE_RETRIEVAL_QUERY` |
| Vector store | Qdrant, one collection per repository, deterministic ids, incremental re-indexing, failure-safe pruning |
| Retrieval | BM25 (code-aware tokenizer) + dense, Reciprocal Rank Fusion, dedup, repository isolation |
| Review | LangGraph: changes → changed units → context → bugs → (conditional) security → quality → validate → dedup → severity → risk → render |
| Security | 12 deterministic rules on added lines **plus** an LLM security pass for security-sensitive changes |
| Validation | Evidence must exist in the file; confirmed vs possible findings; inline comments only on valid diff lines |
| Risk | Transparent formula over severity, confidence, security impact, file sensitivity and change size |
| Persistence | Repositories, PRs, reviews, findings, retrieval logs (PostgreSQL + Alembic) |
| GitHub | GitHub App JWT → installation tokens (PAT fallback for demos), review summary + inline comments |
| Evaluation | Precision@k, Recall@k, MRR, finding precision/recall, clean-case FP rate, latency |
| Ops | Docker Compose stack, JSON logs, optional LangSmith tracing |

## Architecture

```
GitHub PR ──webhook──▶ FastAPI (/webhook, HMAC) ──▶ Redis ──▶ Celery worker
                                                               │
         ┌─────────────────────────────────────────────────────┘
         ▼
  get PR + files (GitHub API) ─▶ diff parser
  index base commit (tarball ─▶ Tree-sitter chunks ─▶ Gemini embeddings ─▶ Qdrant)   [incremental]
  changed file contents ─▶ LangGraph review pipeline ◀── HybridRetriever (BM25 + Qdrant, RRF)
         │
         ▼
  PostgreSQL (review, findings, retrieval logs) ─▶ GitHub review (summary + inline comments)
```

Details: [ARCHITECTURE.md](ARCHITECTURE.md). Status, test results and limitations: [PROJECT_STATUS.md](PROJECT_STATUS.md).

## Tech stack

Python 3.11, FastAPI, Uvicorn, Pydantic, PostgreSQL 16, SQLAlchemy 2, Alembic, Celery 5, Redis 7,
Tree-sitter, Google Gemini (`google-genai`), Qdrant, LangGraph, httpx, PyJWT, pytest, Docker Compose.

## Zero-cost setup and privacy

* **Gemini:** create a key at <https://aistudio.google.com/apikey> **without enabling billing**.
  Free-tier quotas apply. When a quota is exhausted the system raises a clear error, keeps
  all finished work, and never switches to a paid service. Indexing is incremental, so a
  re-run only embeds what is missing.
* **Privacy:** Google may use content sent through the free tier to improve its products.
  **Only index and review public or non-confidential repositories.**
* PostgreSQL, Redis and Qdrant run locally in Docker.

## Quick start (Docker Compose)

```bash
cp .env.example .env
```

Edit `.env` and set at least `GITHUB_WEBHOOK_SECRET` and `GEMINI_API_KEY`. Then either
put your GitHub App key at `./github_app.pem` with `GITHUB_APP_ID` set, or set
`GITHUB_TOKEN` and create an empty key file so the bind mount works:

```bash
touch github_app.pem
```

```bash
docker compose up --build
```

```bash
curl http://localhost:8000/health
```

This starts `postgres`, `redis`, `qdrant`, a one-shot `migrate` job (`alembic upgrade head`),
the `api` on port 8000, and a Celery `worker`.

## Local development (without Docker for the app)

```bash
python -m venv venv
```

```bash
source venv/bin/activate
```

On Windows use `venv\Scripts\activate` instead.

```bash
pip install -r requirements.txt
```

```bash
docker compose up -d postgres redis qdrant
```

```bash
alembic upgrade head
```

```bash
uvicorn app.main:app --reload --port 8000
```

In a second terminal (`-P solo` is required on Windows):

```bash
celery -A app.tasks.celery_app:celery_app worker --loglevel=info -P solo
```

## Connecting GitHub

1. Create a GitHub App (Settings → Developer settings → GitHub Apps).
   * Webhook URL: your public URL + `/webhook`. Locally, use a free tunnel such as
     <https://smee.io> to forward to `http://localhost:8000/webhook`.
   * Webhook secret: the same value as `GITHUB_WEBHOOK_SECRET`.
   * Permissions: **Pull requests: Read & write**, **Contents: Read**, **Metadata: Read**.
   * Events: **Pull request**.
2. Generate a private key, save it as `github_app.pem`, and set `GITHUB_APP_ID`.
3. Install the App on a **public test repository** and open a PR.

For a quick demo without an App, set `GITHUB_TOKEN` (a fine-grained PAT with the same
permissions) and use the CLI below.

## CLI

```bash
python -m app.cli index-local benchmarks/sample_repo --github-id 1
```

```bash
python -m app.cli retrieve --github-id 1 "def login(db, email, password)"
```

```bash
python -m app.cli index OWNER/REPO
```

```bash
python -m app.cli review OWNER/REPO 12 --no-post
```

`review` runs exactly what the worker runs, synchronously. `--no-post` stores the review
without commenting on GitHub. Inspect stored results with
`GET /reviews/{id}` or `GET /repos/{github_id}/pulls/{number}/reviews`.

## Tests

```bash
docker compose up -d postgres
```

```bash
psql postgresql://postgres:postgres@localhost:5432/postgres -c "CREATE DATABASE codelens_test;"
```

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/codelens_test alembic upgrade head
```

```bash
pytest -q
```

The suite needs no API keys and no network. Gemini and GitHub clients are tested
through their real SDK and HTTP code against mock transports. DB tests use a
dedicated `codelens_test` database.

## Benchmarks

```bash
python -m app.evaluation.run retrieval --modes bm25,dense,hybrid
```

```bash
python -m app.evaluation.run review --mode llm
```

Reports are written to `benchmarks/results/`. Modes that need Gemini are reported as
*skipped* when no key is configured. See PROJECT_STATUS.md for the measured results.

## License / status

Final-year B.Tech project. See [PROJECT_STATUS.md](PROJECT_STATUS.md) for what has
been verified and what is still pending.
