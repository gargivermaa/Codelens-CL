# CodeLens CI

**Repository-aware AI code review for GitHub pull requests, built on retrieval-based reasoning.**

Most AI reviewers send only the diff to a language model. CodeLens CI indexes the
whole repository, retrieves the existing code a change interacts with using
**hybrid BM25 + dense retrieval**, and reviews the change in a **LangGraph**
pipeline. Findings are validated against the real diff, de-duplicated and
risk-scored with a documented formula. They are stored in PostgreSQL, posted to the
pull request as a review with inline comments, and shown in a **React dashboard**.

Everything runs on your laptop on free infrastructure. The only external services
are the **Gemini API free tier** and **GitHub**.

![Review detail](docs/screenshots/review-findings.png)

## Contents

[Architecture](#architecture) · [Features](#features) · [Tech stack](#tech-stack) ·
[Folder structure](#folder-structure) · [Run with Docker](#run-with-docker-recommended) ·
[Local development](#local-development-without-docker-for-the-app) · [Environment variables](#environment-variables) ·
[GitHub App setup](#github-app-setup) · [Demo](#demo-a-real-pull-request-end-to-end) ·
[Dashboard](#dashboard) · [API](#api) · [Tests](#tests) · [Evaluation](#evaluation) ·
[Design decisions](#design-decisions) · [Limitations](#known-limitations) · [Pushing this branch](#pushing-this-branch)

## Architecture

```
 GitHub PR opened / pushed
        │  webhook (HMAC-SHA256 signed)
        ▼
 FastAPI /webhook ──verify──▶ Redis queue ──▶ Celery worker
                                               │
   ┌───────────────────────────────────────────┘
   ▼
 GitHub API (App installation token): PR metadata, changed files, file contents
   │
   ├─▶ Index base commit: tarball ─▶ Tree-sitter chunks ─▶ Gemini embeddings ─▶ Qdrant   (incremental)
   │
   ▼
 LangGraph review pipeline
   understand changes → changed units → hybrid retrieval (BM25 + Qdrant, RRF)
   → bug pass → [security pass if security-relevant | rules only] → quality pass
   → validate against diff → de-duplicate → severity → risk score → render
   │
   ▼
 PostgreSQL (repos, PRs, reviews, findings, diffs, retrieval logs)
   ├─▶ GitHub: review summary + inline comments on the changed lines
   └─▶ React dashboard (served by FastAPI at http://localhost:8000)
```

Details: [ARCHITECTURE.md](ARCHITECTURE.md). Current verification status: [PROJECT_STATUS.md](PROJECT_STATUS.md).

## Features

| Area | What it does |
|---|---|
| Webhook | HMAC-SHA256 verification over the raw body; `pull_request` (opened, synchronize, reopened, ready_for_review), `installation`, `ping`; `503` when the queue is down so GitHub shows the failed delivery |
| Async processing | Celery + Redis, late acknowledgement, retries on GitHub rate limits, 5xx and DB errors |
| Idempotency | One review per `(PR, head commit)`, however many duplicate deliveries or retries arrive; an analyzed-but-unposted review is only re-posted, never re-analyzed |
| Diff parsing | Exact new-file line numbers, commentable lines, renames, deletions, binaries, lockfiles, large diffs |
| Chunking | Tree-sitter functions/methods/classes; oversized units split on statement boundaries without losing code; tests, vendored, generated and backup files excluded |
| Embeddings | `gemini-embedding-001`, 1536-d, L2-normalized, `RETRIEVAL_DOCUMENT` / `CODE_RETRIEVAL_QUERY` |
| Vector store | Qdrant, one collection per repository, deterministic ids, incremental re-indexing, failure-safe pruning |
| Retrieval | BM25 with a code-aware tokenizer + dense search, Reciprocal Rank Fusion, explicit fallback to BM25 |
| Review | LangGraph, structured JSON output validated by Pydantic, prompt-injection-resistant prompts |
| Security | 12 deterministic rules on added lines, plus an LLM security pass when a change touches security-relevant code (including *removed* checks) |
| Validation | Evidence must exist in the file; confirmed vs possible findings; inline comments only on lines GitHub accepts |
| Risk | `1 − exp(−(findings + size + surface)/10)`, stored with its full breakdown |
| Dashboard | Overview, repositories, pull requests, review detail, code diff with findings on their lines, analytics and benchmark results |
| Evaluation | Precision@k, Recall@k, MRR, finding precision and recall, clean-PR false-positive rate, latency |

## Tech stack

**Backend:** Python 3.11, FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL 16, Celery 5, Redis 7.
**AI / retrieval:** Tree-sitter, Google Gemini (`google-genai`), Qdrant, LangGraph.
**GitHub:** GitHub App (PyJWT RS256 → installation tokens), REST API via httpx.
**Frontend:** React 18, Vite, Tailwind CSS, Recharts, lucide-react.
**Ops:** Docker Compose, pytest, JSON logging, optional LangSmith tracing.

## Folder structure

```
app/
  api/            webhook.py (GitHub events), dashboard.py (/api/*), reviews.py, health.py
  core/           diff_parser.py, chunker.py, embedder.py, bm25.py, retriever.py
  review/         graph.py (LangGraph), llm.py, prompts.py, schemas.py, security_rules.py, risk.py
  github/         auth.py (App JWT, installation tokens), client.py (REST, review posting)
  services/       review_service.py (orchestration), deps.py
  tasks/          celery_app.py, review_task.py
  models/         SQLAlchemy models
  evaluation/     metrics.py, run.py (benchmarks)
  cli.py          index / retrieve / review / models commands
alembic/          migrations
frontend/         React dashboard (Vite + Tailwind)
benchmarks/       sample_repo/, retrieval_benchmark.json, review_benchmark.json, results/
scripts/          create_demo_prs.py, seed_local_reviews.py, send_test_webhook.py
tests/            157 tests (no network, no API keys needed)
docs/screenshots/ dashboard screenshots
```

## Zero cost and privacy

* **Gemini:** create a key at <https://aistudio.google.com/apikey> and **do not
  enable billing**. Without billing, a model outside the free tier can only fail
  with `429`; it cannot charge you. Quota errors are reported clearly, finished work
  is kept, and a re-run continues.
* **Privacy:** Google may use free-tier inputs to improve its products. **Only
  index and review public or non-confidential repositories**, such as `codelens-demo`.
* PostgreSQL, Redis and Qdrant run locally in Docker.

## Run with Docker (recommended)

Requirements: Docker Desktop (running). All commands are for Windows PowerShell, run
from the `codelens-ci` folder.

1. **Configure.**
   ```powershell
   copy .env.example .env
   ```
   ```powershell
   notepad .env
   ```
   Set at least `GITHUB_WEBHOOK_SECRET` and `GEMINI_API_KEY`. Leave the
   `DATABASE_URL`/`REDIS_URL`/`QDRANT_URL` defaults: Compose overrides them inside
   the containers.

2. **Start everything.**
   ```powershell
   docker compose up --build -d
   ```
   This starts postgres, redis, qdrant, a one-shot `migrate` job, `api` (port 8000)
   and `worker`.

3. **Check.**
   ```powershell
   docker compose ps
   ```
   `api` should be `healthy` and `migrate` should have exited with code 0. Open
   **http://localhost:8000** for the dashboard and **http://localhost:8000/docs**
   for the API.

4. **Logs and stopping.**
   ```powershell
   docker compose logs -f worker
   ```
   ```powershell
   docker compose down
   ```
   Data stays in the `pg_data` and `qdrant_data` volumes. `docker compose down -v`
   deletes it.

To receive GitHub webhooks, start the stack with the tunnel profile
(see [GitHub App setup](#github-app-setup)):

```powershell
docker compose --profile tunnel up --build -d
```

## Local development (without Docker for the app)

Requirements: Python **3.11**, Docker Desktop (for the databases), and Node.js 20+
only if you want to change the dashboard.

```powershell
py -3.11 -m venv venv
```

```powershell
venv\Scripts\activate
```

```powershell
pip install -r requirements.txt
```

```powershell
docker compose up -d postgres redis qdrant
```

```powershell
alembic upgrade head
```

Build the dashboard once (or after frontend changes):

```powershell
cd frontend; npm install; npm run build; cd ..
```

Terminal 1, the API and dashboard:

```powershell
uvicorn app.main:app --reload --port 8000
```

Terminal 2, the worker (`-P solo` is required on Windows):

```powershell
celery -A app.tasks.celery_app:celery_app worker --loglevel=info -P solo
```

For live frontend editing, run `npm run dev` in `frontend/` and open
http://localhost:5173. It proxies `/api` to port 8000.

To try the dashboard without GitHub, run the real review service on the benchmark
PRs (read from local files, never posted):

```powershell
python scripts/seed_local_reviews.py
```

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `GITHUB_WEBHOOK_SECRET` | yes | Shared secret for webhook signatures (same value as in the GitHub App) |
| `GEMINI_API_KEY` | for AI | Gemini free-tier key (embeddings + review model) |
| `GEMINI_REVIEW_MODEL` | no | Default `gemini-3.5-flash-lite`. `python -m app.cli models` lists models your key can use |
| `GITHUB_APP_ID` | for the App | GitHub App ID |
| `GITHUB_PRIVATE_KEY_PATH` | for the App | Default `./secrets/github_app.pem` |
| `GITHUB_TOKEN` | optional | Fine-grained token for CLI demos without an App |
| `GITHUB_POST_REVIEWS` | no | `false` = store reviews without posting to GitHub |
| `SMEE_URL` | for the tunnel | Your channel from https://smee.io/new |
| `DATABASE_URL`, `REDIS_URL`, `QDRANT_URL` | local runs | Defaults in `.env.example` match `docker-compose.yml` |
| `REVIEW_TOP_K`, `REVIEW_MAX_FILES`, `REVIEW_MAX_PATCH_LINES`, `REVIEW_MAX_INLINE_COMMENTS` | no | Review limits |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY` | no | Optional tracing (free developer tier) |
| `LOG_FORMAT` | no | `text` or `json` |

Secrets live only in `.env` and `secrets/`, which are both git-ignored and docker-ignored. Never commit them.

## GitHub App setup

1. **Webhook tunnel.** Open <https://smee.io/new> and copy the channel URL into
   `.env` as `SMEE_URL=...`.
2. **Create the App.** On GitHub: Settings → Developer settings → GitHub Apps →
   *New GitHub App*.
   * Homepage URL: anything (for example your repository URL).
   * **Webhook URL:** your smee channel URL.
   * **Webhook secret:** the same value as `GITHUB_WEBHOOK_SECRET` in `.env`.
   * Repository permissions: **Pull requests: Read and write**, **Contents: Read-only**
     (Metadata: Read-only is added automatically).
   * Subscribe to events: **Pull request**.
   * Where can it be installed: *Only on this account*.
3. **Private key.** On the App page: *Generate a private key*. Save the downloaded
   file as `secrets\github_app.pem` inside the project. Copy the **App ID** into
   `.env` as `GITHUB_APP_ID=...`.
4. **Install** the App (App page → *Install App*) on **only** your public
   `codelens-demo` repository.
5. **Start with the tunnel.**
   ```powershell
   docker compose --profile tunnel up --build -d
   ```
   Then, in the App's settings → *Advanced* → *Recent Deliveries*, you should see
   the `installation` event with a green tick. The repository appears on the
   dashboard's **Repositories** page.

## Demo: a real pull request end to end

1. **Create the demo repository.** On GitHub create a **public** repository named
   `codelens-demo` with a README, and clone it next to `codelens-ci`:
   ```powershell
   cd ..; git clone https://github.com/<you>/codelens-demo.git; cd codelens-ci
   ```
2. **Create one branch per seeded issue** (SQL injection, IDOR, path traversal,
   command injection, hardcoded credential, two logic bugs, weak token, three clean
   changes), push them, and open the PRs:
   ```powershell
   python scripts/create_demo_prs.py ..\codelens-demo --push
   ```
   Then open each `demo/*` branch on GitHub → *Compare & pull request*. If you have
   the GitHub CLI, add `--open` to do this automatically.
3. **Watch it work.** Each PR triggers the webhook, and the worker reviews it. Follow
   along with `docker compose logs -f worker`.
4. **On GitHub:** the PR gets a *CodeLens CI review* summary with a risk score, plus
   inline comments on the vulnerable lines.
5. **On the dashboard** (http://localhost:8000): the PR appears under *Pull
   requests*. Open it to see findings, the code diff with each finding on its line,
   and the risk breakdown.

Without an App you can still review a PR with a token. Set `GITHUB_TOKEN` in `.env`, then:

```powershell
python -m app.cli review <you>/codelens-demo 1 --no-post
```

Remove `--no-post` to post the review.

## Dashboard

| Overview | Pull requests |
|---|---|
| ![Overview](docs/screenshots/overview.png) | ![Pull requests](docs/screenshots/pull-requests.png) |
| **Code diff with findings** | **Risk breakdown and pipeline run** |
| ![Diff](docs/screenshots/review-diff.png) | ![Risk](docs/screenshots/review-risk.png) |
| **Repositories** | **Analytics** |
| ![Repositories](docs/screenshots/repositories.png) | ![Analytics](docs/screenshots/analytics.png) |
| **Evaluation (benchmarks)** | |
| ![Evaluation](docs/screenshots/evaluation.png) | |

The screenshots show real persisted reviews from `scripts/seed_local_reviews.py` and
real benchmark runs. They were taken in rules-only mode, because the build
environment had no Gemini key or GitHub access. On your machine the same screens
show your GitHub PRs, LLM findings and LLM benchmark runs.

## API

Interactive docs: http://localhost:8000/docs

| Method & path | Purpose |
|---|---|
| `POST /webhook` | GitHub events (signature required) |
| `GET /health` | Liveness |
| `GET /api/overview` | Counts, risk distribution, severity totals, recent PRs |
| `GET /api/repositories` · `/api/repositories/{github_id}` | Repositories, index state, PR history |
| `GET /api/pulls?repo=&risk=&status=` · `/api/pulls/{id}` | PRs with latest review; review history per PR |
| `GET /api/reviews/{id}` | Full review: findings, stored diffs, risk breakdown, LLM calls, node timings, notes |
| `GET /api/analytics?days=30` | History, categories, sources, per-repo stats, latency |
| `GET /api/benchmarks` · `/api/benchmarks/runs/{run_id}` | Latest measured report per configuration/split; full run with per-case results |
| `GET /reviews/{id}`, `/repos/{id}/pulls/{n}/reviews`, `/findings/stats` | Original JSON API (kept) |

CLI: `python -m app.cli index-local PATH --github-id N`, `index OWNER/REPO`,
`retrieve --github-id N "query"`, `review OWNER/REPO PR [--no-post]`, `models`.

## Tests

```powershell
docker compose up -d postgres redis
```

```powershell
docker compose exec postgres psql -U postgres -c "CREATE DATABASE codelens_test;"
```

```powershell
$env:DATABASE_URL="postgresql://postgres:postgres@localhost:5432/codelens_test"; alembic upgrade head; Remove-Item Env:DATABASE_URL
```

```powershell
pytest -q
```

Expected: **168 passed**. No API keys or network are needed. The Gemini and GitHub
clients are exercised through their real SDK and HTTP code against mock transports;
database tests use the separate `codelens_test` database.

## Evaluation

Full methodology, metric definitions and results: **[docs/EVALUATION.md](docs/EVALUATION.md)**.

* **Review benchmark v2:** 51 labelled PRs (38 seeded defects across 13 types, plus 13
  clean PRs), split into dev / validation / holdout.
* **Retrieval benchmark v2:** 40 labelled queries, same splits.
* **Three configurations** on identical cases: `rules`, `llm` (no context) and
  `llm_context` (CodeLens).
* **Strict scoring:** right file, ±2 lines *and* the right category.

```powershell
python -m app.evaluation.run review --config all --split holdout
```

```powershell
python -m app.evaluation.run retrieval --split holdout
```

Results appear on the dashboard's **Evaluation** page, with case inspection, and in
`benchmarks/results/` as JSON plus CSV.

Measured so far:

| Run | Precision | Recall | Clean-PR FP |
|---|---|---|---|
| Rules only, v2 holdout (28 PRs) | 1.00 | 0.24 (5/21) | 0% |
| LLM + context, v1 (11 PRs, `gemini-3.5-flash-lite`) | 0.875 | 0.875 (7/8) | 0% |

| Retrieval | P@1 | MRR |
|---|---|---|
| BM25, v2 holdout | 0.933 | 0.967 |
| BM25 / Dense / Hybrid, v1 | 0.933 / 0.733 / 0.867 | 0.967 / 0.867 / 0.933 |

On real `psf/requests` PRs, a correct fix (#7502) got no findings, which is correct.
A subtle regression (#7433) was missed. Known issues are listed in
[docs/EVALUATION.md §6](docs/EVALUATION.md#6-known-issues-and-observations).

## Documentation

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, data flow, database, retrieval, LangGraph, security |
| [docs/DIAGRAMS.md](docs/DIAGRAMS.md) | Architecture, data-flow, sequence and ER diagrams (Mermaid) |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Benchmark design, metrics, results |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | ₹0 deployment: local Docker + Cloudflare Quick Tunnel + smee |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | 8–10 minute demo with fallbacks |
| [docs/VIVA.md](docs/VIVA.md) | Viva questions and answers |
| [docs/TECHNICAL_REPORT.md](docs/TECHNICAL_REPORT.md) | Project report |
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | What is verified, what is pending |

## Design decisions

* **Hybrid retrieval with RRF.** BM25 is strong on exact identifiers, while dense
  search handles paraphrase. RRF fuses their ranks without calibrating score scales.
* **Index the base commit, review the head.** Context is the *existing* code; the
  PR's own changes come from the diff.
* **Conditional security pass** (locked design decision): one general review, plus a
  security-focused LLM pass only for security-relevant changes. This saves free-tier
  quota.
* **Grounding over trust.** A finding must quote code that exists in the file.
  Only confirmed findings on changed lines become inline comments.
* **Idempotent per commit.** A `(pr_id, head_sha)` unique constraint plus row
  locking makes duplicate webhooks and Celery retries safe.
* **Lossless chunking.** Large units are split on statement boundaries; nothing is
  truncated.
* **Free tier only.** There is no paid fallback, and quota exhaustion is reported.

## Known limitations

Python is the only reviewed language. The dashboard has no user accounts; set
`DASHBOARD_PASSWORD` before exposing it publicly (see docs/DEPLOYMENT.md). Benchmarks are small. Free-tier rate limits make large PRs slow. Full list:
[PROJECT_STATUS.md](PROJECT_STATUS.md#6-known-limitations).

## Pushing this branch

The development session that wrote this branch had read-only GitHub access. To
publish it from your machine, using your original clone of `Codelens-CL`:

```powershell
git fetch C:\path\to\codelens-ci-claude.bundle claude/full-project-implementation:claude/full-project-implementation
```

```powershell
git push -u origin claude/full-project-implementation
```

Then open a pull request from that branch into `main` on GitHub. `main` is never
pushed directly.
