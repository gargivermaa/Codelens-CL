"""FastAPI entrypoint. Routes stay thin: webhook verification + enqueue, and
read-only access to stored reviews. All heavy work runs in the Celery worker.

If the React dashboard has been built (frontend/dist), it is served at "/".
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import dashboard, health, reviews, webhook
from app.logging_config import configure_logging

configure_logging()

app = FastAPI(title="CodeLens CI", version="1.1.0",
              description="Repository-aware AI code review for GitHub pull requests.")

app.include_router(health.router)
app.include_router(webhook.router)
app.include_router(reviews.router)
app.include_router(dashboard.router)

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
_API_PREFIXES = ("api/", "webhook", "health", "reviews/", "repos/", "findings/", "docs", "redoc", "openapi.json")


@app.get("/api/info")
async def info():
    return {"service": "CodeLens CI", "status": "running", "dashboard": FRONTEND_DIST.is_dir()}


if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith(_API_PREFIXES):
            raise HTTPException(status_code=404)
        candidate = (FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_file() and FRONTEND_DIST in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")   # client-side routes
else:
    @app.get("/")
    async def root():
        return {"service": "CodeLens CI", "status": "running",
                "dashboard": "not built (cd frontend && npm install && npm run build)"}
