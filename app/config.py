"""
Centralized configuration, loaded from environment variables / .env.

Only GITHUB_WEBHOOK_SECRET is required to boot the API. Everything else has a
safe default so individual components (tests, the chunker CLI, ...) can run
without the full stack. Secrets are never logged.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- GitHub webhook ---
    GITHUB_WEBHOOK_SECRET: str

    # --- GitHub App (preferred) ---
    GITHUB_APP_ID: str = ""
    GITHUB_PRIVATE_KEY_PATH: str = "./github_app.pem"
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
    GEMINI_REVIEW_MODEL: str = "gemini-2.5-flash"
    GEMINI_MAX_ATTEMPTS: int = 5
    # Spacing between Gemini requests from this process (free-tier RPM limits).
    GEMINI_MIN_REQUEST_INTERVAL_S: float = 1.0

    # --- Review pipeline ---
    REVIEW_TOP_K: int = 5                 # retrieved context chunks per changed unit
    REVIEW_MAX_FILES: int = 30            # changed files considered per PR
    REVIEW_MAX_PATCH_LINES: int = 2000    # larger diffs are reviewed partially (reported)
    REVIEW_MAX_INLINE_COMMENTS: int = 15

    # --- LangSmith tracing (optional, free developer tier) ---
    LANGSMITH_API_KEY: str = ""


settings = Settings()
