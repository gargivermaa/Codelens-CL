"""FastAPI entrypoint. Routes stay thin: webhook verification + enqueue, and
read-only access to stored reviews. All heavy work runs in the Celery worker."""
from fastapi import FastAPI

from app.api import health, reviews, webhook
from app.logging_config import configure_logging

configure_logging()

app = FastAPI(title="CodeLens CI", version="1.0.0",
              description="Repository-aware AI code review for GitHub pull requests.")

app.include_router(health.router)
app.include_router(webhook.router)
app.include_router(reviews.router)


@app.get("/")
async def root():
    return {"service": "CodeLens CI", "status": "running"}
