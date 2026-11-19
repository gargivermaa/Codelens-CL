"""
Centralized configuration, loaded from environment variables / .env.

Only GITHUB_WEBHOOK_SECRET is required to boot the API. Everything else has a
safe default so individual components (tests, the chunker CLI, ...) can run
without the full stack. Secrets are never logged.
"""
import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- GitHub webhook ---
    GITHUB_WEBHOOK_SECRET: str

    # --- GitHub App (preferred) ---
    GITHUB_APP_ID: str = ""
    GITHUB_PRIVATE_KEY_PATH: str = "./secrets/github_app.pem"
    # --- GitHub token (optional fallback for local demos: a free fine-grained PAT) ---
    GITHUB_TOKEN: str = ""
    GITHUB_API_URL: str = "https://api.github.com"
    # When false, reviews are computed and stored but not posted to GitHub.
    GITHUB_POST_REVIEWS: bool = True

    # --- PostgreSQL ---
    DATABASE_URL: str = ""

    # --- Redis / Celery ---
    REDIS_URL: str = ""

    # --- Qdrant ---
    QDRANT_URL: str = ""
    # If set, use Qdrant's embedded local mode at this path instead of a server.
    QDRANT_PATH: str = ""

    # --- Gemini (free tier only; never configure billing) ---
    GEMINI_API_KEY: str = ""
    EMBEDDING_QUERY_TASK_TYPE: str = "CODE_RETRIEVAL_QUERY"
    # Review LLM. Must be a model that is free of charge on the free tier.
    GEMINI_REVIEW_MODEL: str = "gemini-3.5-flash-lite"
    GEMINI_MAX_ATTEMPTS: int = 5
    # Spacing between Gemini requests from this process (free-tier RPM limits).
    GEMINI_MIN_REQUEST_INTERVAL_S: float = 1.0
    # Free tier: embedding inputs per minute (every text in a batch counts; the
    # limit observed on the free tier is 100). Stay below it.
    GEMINI_EMBED_INPUTS_PER_MINUTE: int = 90
    # How often to wait-and-retry a per-minute 429 (daily quotas are never retried).
    GEMINI_RATE_LIMIT_RETRIES: int = 3

    # --- Review pipeline ---
    REVIEW_TOP_K: int = 5                 # retrieved context chunks per changed unit
    REVIEW_MAX_FILES: int = 30            # changed files considered per PR
    REVIEW_MAX_PATCH_LINES: int = 2000    # larger diffs are reviewed partially (reported)
    REVIEW_MAX_INLINE_COMMENTS: int = 15

    # --- Dashboard protection for public demos (HTTP Basic auth; empty = off) ---
    DASHBOARD_USER: str = "codelens"
    DASHBOARD_PASSWORD: str = ""

    # --- LangSmith tracing (optional, free developer tier) ---
    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "codelens-ci"


settings = Settings()

# LangGraph/LangSmith read tracing settings from os.environ, which .env values
# are not exported to; export them only when tracing is explicitly enabled.
if settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY:
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", settings.LANGSMITH_API_KEY)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.LANGSMITH_PROJECT)
