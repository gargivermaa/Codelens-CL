
# CodeLens CI

Repository-aware AI code review system built using retrieval-based reasoning.

## Project Overview

CodeLens CI analyzes GitHub pull requests and provides context-aware code review using code embeddings and retrieval.

## Implemented Modules

### Day 1 – Backend Foundation
- FastAPI application skeleton
- `/health` endpoint
- `/webhook` endpoint
- HMAC-SHA256 GitHub webhook signature verification

### Day 7 – Semantic Code Chunking
- Python AST-based Tree-sitter parsing
- Function, class and method extraction
- Semantic metadata and source line tracking
- Repository traversal and file exclusions

### Day 8 – Embeddings and Vector Indexing
- Google Gemini `gemini-embedding-001`
- 1536-dimensional normalized embeddings
- `RETRIEVAL_DOCUMENT` task type
- UTF-8-safe splitting for oversized embedding inputs
- Qdrant vector storage with COSINE similarity
- Deterministic UUID-based point IDs
- Metadata storage for every code chunk
- Re-indexing and stale-point cleanup
- Failure-safe indexing workflow

## Technology Stack

- Python
- FastAPI
- Tree-sitter
- Google Gemini API
- Qdrant
- Pytest

## Setup

Create and activate a virtual environment:

```bash
python -m venv venv
```

Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure environment variables in `.env`:

- `GEMINI_API_KEY`
- `QDRANT_URL`
- `GITHUB_WEBHOOK_SECRET`

Start Qdrant at the configured URL. The default URL is `http://localhost:6333`.

## Run the Backend

```bash
uvicorn app.main:app --reload --port 8000
```

## Index a Repository

```bash
python -m app.core.embedder . --github-id YOUR_GITHUB_REPOSITORY_ID
```

Replace the placeholder with the repository's numeric GitHub ID.

## Run Tests

```bash
pytest -q
```

## Verification

Latest local integration verification:
- 42 automated tests passing
- Gemini API embedding request successful
- 78 chunks indexed in Qdrant
- 1536-dimensional vectors verified
- All 7 metadata fields verified

## Upcoming Modules

- Day 9 – Full repository indexing
- Day 10 – Hybrid retrieval (BM25 + dense search)
- Subsequent stages – LangGraph review pipeline, risk scoring, GitHub PR comments and evaluation.
