"""
Celery entry point for PR reviews.

The webhook enqueues process_pr_review(...) with the same signature since
Day 5. The body now runs the full review (app/services/review_service.py).

Retry policy:
  GitHub rate limit       -> retry after GitHub's reset time
  GitHub 5xx / network,
  DB connection errors     -> exponential backoff (10s, 20s, 40s, ...), max 5 retries
  Everything else          -> fail; the review row stores the error
"""
import logging

from sqlalchemy.exc import OperationalError as DBOperationalError

from app.github.client import GitHubRateLimitError, GitHubTransientError
from app.models.database import SessionLocal
from app.services.deps import build_deps
from app.services.review_service import run_review
from app.tasks.celery_app import celery_app

logger = logging.getLogger("codelens.tasks")


@celery_app.task(
    name="review_task.process_pr_review",
    bind=True,
    max_retries=5,
    default_retry_delay=10,
)
def process_pr_review(
    self,
    repo_github_id: int,
    repo_full_name: str,
    pr_number: int,
    installation_id: int | None = None,
    title: str | None = None,
    author: str | None = None,
):
    logger.info("Task received: repo=%s pr=#%d attempt=%d", repo_full_name, pr_number, self.request.retries + 1)
    db = SessionLocal()
    try:
        return run_review(db, build_deps(installation_id), repo_github_id, repo_full_name, pr_number,
                          installation_id=installation_id)
    except GitHubRateLimitError as exc:
        logger.warning("GitHub rate limited; retrying in %ss", exc.retry_after)
        raise self.retry(exc=exc, countdown=exc.retry_after)
    except (GitHubTransientError, DBOperationalError) as exc:
        countdown = 10 * 2 ** self.request.retries
        logger.warning("Transient failure (%s); retrying in %ss", type(exc).__name__, countdown)
        raise self.retry(exc=exc, countdown=countdown)
    finally:
        db.close()
