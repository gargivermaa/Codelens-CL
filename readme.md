# CodeLens CI

Repository-aware code review system built on retrieval-based reasoning.

## Day 1 status
- FastAPI app skeleton
- `/health` endpoint
- `/webhook` endpoint with HMAC-SHA256 GitHub signature verification

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in GITHUB_WEBHOOK_SECRET
uvicorn app.main:app --reload --port 8000
```

## Test

```bash
curl http://localhost:8000/health

# Unsigned request -> should return 401
curl -X POST http://localhost:8000/webhook -d '{}'
```