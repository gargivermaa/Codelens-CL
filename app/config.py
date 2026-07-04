"""
Centralized configuration. All env vars are declared here from Day 1,
even ones not used until later days (DB, Redis, Qdrant, OpenAI, GitHub App),
so later-day code only needs to import `settings` — never re-touch this file
to add a missing var.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # --- Day 1-2: required now ---
    GITHUB_WEBHOOK_SECRET: str

    # --- Day 15+: GitHub App ---
    GITHUB_APP_ID: str = ""
    GITHUB_PRIVATE_KEY_PATH: str = "./github_app.pem"

    # --- Day 8-9: OpenAI ---
    OPENAI_API_KEY: str = ""

    # --- Day 3-4: PostgreSQL ---
    DATABASE_URL: str = ""

    # --- Day 5-6: Redis / Celery ---
    REDIS_URL: str = ""

    # --- Day 8-9: Qdrant ---
    QDRANT_URL: str = ""

    # --- Week 4 Should Have: LangSmith ---
    LANGSMITH_API_KEY: str = ""


settings = Settings()