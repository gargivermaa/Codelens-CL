"""Builds real service dependencies from settings (used by the worker and CLI)."""
import logging

from app.config import settings
from app.core.embedder import get_gemini_client, get_qdrant_client
from app.github.client import GitHubClient
from app.review.llm import GeminiReviewLLM
from app.services.review_service import ReviewDeps

logger = logging.getLogger("codelens.deps")


def build_deps(installation_id: int | None) -> ReviewDeps:
    gemini_client = llm = None
    if settings.GEMINI_API_KEY:
        gemini_client = get_gemini_client()
        llm = GeminiReviewLLM(client=gemini_client)
    else:
        logger.warning("GEMINI_API_KEY not set: reviews will contain rule-based findings only")
    redis = None
    if settings.REDIS_URL:
        import redis as redis_lib
        redis = redis_lib.Redis.from_url(settings.REDIS_URL)
    return ReviewDeps(github=GitHubClient.for_installation(installation_id), qdrant=get_qdrant_client(),
                      llm=llm, gemini_client=gemini_client, redis=redis)
