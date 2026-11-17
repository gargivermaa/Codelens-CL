import logging

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from app.models.database import SessionLocal
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.tasks.celery_app import celery_app

logger = logging.getLogger("codelens.tasks")


@celery_app.task(
    name="review_task.process_pr_review",
    bind=True,
    max_retries=3,
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
    """
    Day 5: idempotently record the repository and pull request, and mark the
    PR as processing. Later days extend this body (chunking, retrieval,
    review, comment posting); the signature stays the same.
    """
    logger.info("Task received: repo=%s pr=#%d", repo_full_name, pr_number)
    db = SessionLocal()
    try:
        # Repository is identified by GitHub's immutable numeric id, not by
        # name (repos can be renamed or transferred). A rename simply updates
        # full_name on the existing row.
        repo_stmt = pg_insert(Repository).values(
            github_id=repo_github_id,
            full_name=repo_full_name,
            installation_id=installation_id,
        )
        repo_stmt = repo_stmt.on_conflict_do_update(
            index_elements=[Repository.github_id],
            set_={
                "full_name": repo_stmt.excluded.full_name,
                # Preserve the stored installation_id when the incoming value is None.
                "installation_id": func.coalesce(
                    repo_stmt.excluded.installation_id, Repository.installation_id
                ),
            },
        ).returning(Repository.id)
        repo_id = db.execute(repo_stmt).scalar_one()
        logger.info("Repository upserted: repo_id=%d github_id=%d", repo_id, repo_github_id)

        # Atomic INSERT ... ON CONFLICT: concurrent webhooks for the same PR
        # (e.g. opened + synchronize) cannot race into duplicate rows, because
        # the DB-level unique constraint arbitrates.
        pr_stmt = pg_insert(PullRequest).values(
            repo_id=repo_id,
            pr_number=pr_number,
            title=title,
            author=author,
            status="processing",
        )
        pr_stmt = pr_stmt.on_conflict_do_update(
            constraint="uq_pull_requests_repo_pr",
            set_={
                "title": func.coalesce(pr_stmt.excluded.title, PullRequest.title),
                "author": func.coalesce(pr_stmt.excluded.author, PullRequest.author),
                "status": "processing",  # a new push re-opens the review
            },
        ).returning(PullRequest.id)
        pr_id = db.execute(pr_stmt).scalar_one()

        db.commit()
        logger.info("PR row ready: pr_id=%d pr=#%d status=processing", pr_id, pr_number)
        return {"status": "ok", "repo_id": repo_id, "pr_id": pr_id}

    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("DB error in review task: %s", exc)
        raise self.retry(exc=exc)
    finally:
        db.close()