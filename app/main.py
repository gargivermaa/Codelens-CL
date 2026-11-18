"""FastAPI entrypoint. Routes stay thin: webhook verification + enqueue, and
read-only access to stored reviews. All heavy work runs in the Celery worker.

If the React dashboard has been built (frontend/dist), it is served at "/".
"""
import base64
import hmac
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import dashboard, health, reviews, webhook
from app.config import settings
from app.logging_config import configure_logging

configure_logging()

app = FastAPI(title="CodeLens CI", version="1.1.0",
              description="Repository-aware AI code review for GitHub pull requests.")

# Paths that must stay open when the dashboard is password-protected: GitHub
# webhooks are authenticated by their HMAC signature, /health by nothing.
_OPEN_PATHS = ("/webhook", "/health")


@app.middleware("http")
async def dashboard_password(request: Request, call_next):
    """Optional HTTP Basic auth for everything except webhooks and health checks.

    Enabled by DASHBOARD_PASSWORD; meant for public demos through a tunnel.
    """
    if not settings.DASHBOARD_PASSWORD or request.url.path in _OPEN_PATHS:
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            user, _, password = base64.b64decode(header[6:]).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            user = password = ""
        if hmac.compare_digest(user, settings.DASHBOARD_USER) and \
                hmac.compare_digest(password, settings.DASHBOARD_PASSWORD):
            return await call_next(request)
    return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="CodeLens CI"'})


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
