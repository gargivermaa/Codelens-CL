"""
End-to-end review orchestration (called by the Celery task and the CLI).

    GitHub PR -> claim (pr, head_sha) -> fetch files -> parse diff
      -> ensure base commit is indexed (incremental, locked per repo)
      -> fetch changed file contents at head -> LangGraph pipeline
      -> persist review + findings + retrieval logs (one transaction)
      -> post review to GitHub -> completed

Idempotency: the (pr_id, head_sha) unique constraint plus row locking means a
commit is analyzed once however many webhook deliveries or retries arrive. A
review that was analyzed but not yet posted is only re-posted on retry; the
LLM analysis is never paid for twice.
"""
import contextlib
import json
import logging
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from qdrant_client import QdrantClient
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.core.chunker import chunk_repository
from app.core.diff_parser import parse_pull_request_files
from app.core.embedder import EmbeddingError, collection_name, index_repository
from app.core.retriever import HybridRetriever
from app.github.client import GitHubClient, InlineComment
from app.models.finding import Finding
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.retrieval_log import RetrievalLog
from app.models.review import Review
from app.models.review_file import ReviewFile
from app.review.graph import ReviewPipeline, render_inline_body, to_json_safe
from app.review.schemas import SEVERITY_RANK, ReviewFinding

logger = logging.getLogger("codelens.service")

STALE_RUNNING_AFTER = timedelta(minutes=30)


def _utcnow() -> datetime:
    # DB columns are naive UTC timestamps.
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class ReviewDeps:
    github: GitHubClient
    qdrant: QdrantClient
    llm: object | None = None          # GeminiReviewLLM (None -> rules-only review, reported)
    gemini_client: object | None = None
    redis: object | None = None        # redis.Redis for the per-repository index lock


# ---------------------------------------------------------------- persistence helpers

def upsert_repository_and_pr(db: Session, repo_github_id: int, repo_full_name: str, pr_number: int,
                             installation_id: int | None = None, title: str | None = None,
                             author: str | None = None, head_sha: str | None = None,
                             status: str = "processing", default_branch: str | None = None,
                             private: bool | None = None) -> tuple[int, int]:
    """Atomic INSERT ... ON CONFLICT for the repository and PR rows (Day 5 logic)."""
    repo_id = upsert_repository(db, repo_github_id, repo_full_name, installation_id, default_branch, private,
                                commit=False)

    pr_stmt = pg_insert(PullRequest).values(repo_id=repo_id, pr_number=pr_number, title=title, author=author,
                                            head_sha=head_sha, status=status)
    pr_stmt = pr_stmt.on_conflict_do_update(
        constraint="uq_pull_requests_repo_pr",
        set_={"title": func.coalesce(pr_stmt.excluded.title, PullRequest.title),
              "author": func.coalesce(pr_stmt.excluded.author, PullRequest.author),
              "head_sha": func.coalesce(pr_stmt.excluded.head_sha, PullRequest.head_sha),
              "status": status},
    ).returning(PullRequest.id)
    pr_id = db.execute(pr_stmt).scalar_one()
    db.commit()
    return repo_id, pr_id


def upsert_repository(db: Session, repo_github_id: int, repo_full_name: str,
                      installation_id: int | None = None, default_branch: str | None = None,
                      private: bool | None = None, commit: bool = True) -> int:
    """Insert or update a repository row; None values never overwrite stored ones."""
    stmt = pg_insert(Repository).values(github_id=repo_github_id, full_name=repo_full_name,
                                        installation_id=installation_id, default_branch=default_branch,
                                        private=private)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Repository.github_id],
        set_={"full_name": stmt.excluded.full_name,
              "installation_id": func.coalesce(stmt.excluded.installation_id, Repository.installation_id),
              "default_branch": func.coalesce(stmt.excluded.default_branch, Repository.default_branch),
              "private": func.coalesce(stmt.excluded.private, Repository.private)},
    ).returning(Repository.id)
    repo_id = db.execute(stmt).scalar_one()
    if commit:
        db.commit()
    return repo_id


def claim_review(db: Session, pr_id: int, head_sha: str, base_sha: str) -> tuple[Review | None, str]:
    """Create-or-lock the review row for this commit. Returns (review, reason)."""
    db.execute(pg_insert(Review).values(pr_id=pr_id, head_sha=head_sha, base_sha=base_sha, status="pending")
               .on_conflict_do_nothing(constraint="uq_reviews_pr_head_sha"))
    review = db.execute(select(Review).where(Review.pr_id == pr_id, Review.head_sha == head_sha)
                        .with_for_update()).scalar_one()
    now = _utcnow()
    if review.status == "completed":
        db.commit()
        return None, "already reviewed"
    if review.status == "running" and review.started_at and now - review.started_at < STALE_RUNNING_AFTER:
        db.commit()
        return None, "review in progress in another worker"
    if review.status != "analyzed":
        review.status = "running"
        review.error = None
    review.started_at = now
    review.attempts = (review.attempts or 0) + 1
    db.commit()
    return review, "claimed"


def _redis_lock(redis, name: str):
    if redis is None:
        return contextlib.nullcontext()
    return redis.lock(name, timeout=1800, blocking_timeout=1800)


def ensure_indexed(db: Session, repo: Repository, ref: str, deps: ReviewDeps) -> dict:
    """Index the repository at `ref` (normally the PR base commit) if needed."""
    if repo.indexed_sha == ref and deps.qdrant.collection_exists(collection_name(repo.github_id)):
        return {"indexed": False, "reason": "up to date"}
    with _redis_lock(deps.redis, f"codelens:index:{repo.github_id}"):
        db.refresh(repo)
        if repo.indexed_sha == ref and deps.qdrant.collection_exists(collection_name(repo.github_id)):
            return {"indexed": False, "reason": "indexed by another worker"}
        with tempfile.TemporaryDirectory(prefix="codelens-") as tmp:
            files = deps.github.download_python_sources(repo.full_name, ref, Path(tmp))
            chunks = chunk_repository(tmp)
            result = index_repository(repo.github_id, chunks, deps.gemini_client, deps.qdrant)
        repo.indexed_sha = ref
        repo.indexed_at = _utcnow()
        repo.indexed_chunks = result.chunks_indexed
        db.commit()
        logger.info("Indexed %s@%s: %d files, %d chunks (%d embedded, %d reused, %d pruned)",
                    repo.full_name, ref[:7], files, result.chunks_indexed, result.embedded,
                    result.reused, result.pruned)
        return {"indexed": True, "files": files, "chunks": result.chunks_indexed,
                "embedded": result.embedded, "reused": result.reused, "pruned": result.pruned,
                "embedding_tokens": result.total_tokens}


def persist_analysis(db: Session, review: Review, pr_id: int, repo_github_id: int, state: dict,
                     model: str | None) -> None:
    """Store review results, findings and retrieval logs in one transaction."""
    findings: list[ReviewFinding] = state.get("findings", [])
    risk = state["risk"]
    llm_calls = state.get("llm_calls", [])
    token_values = [c.get(k) for c in llm_calls if c.get("ok") for k in ("input_tokens", "output_tokens")]
    known = [t for t in token_values if t is not None]

    review.risk_score = risk["score"]
    review.risk_label = risk["label"]
    review.risk_breakdown = json.dumps(risk)
    review.issues_found = sum(1 for f in findings if f.status == "confirmed")
    review.files_reviewed = state["change_stats"]["files_reviewable"]
    review.model = model
    review.retrieval_mode = ",".join(sorted({r.mode for r in state.get("retrievals", [])})) or None
    review.summary = state["summary_markdown"]
    review.raw_output = json.dumps(to_json_safe(state), default=str)
    review.total_tokens = sum(known) if known else None
    review.total_cost_usd = 0.0   # free tier only
    review.status = "analyzed"

    db.query(ReviewFile).filter(ReviewFile.review_id == review.id).delete(synchronize_session=False)
    for changed in state.get("files", []):
        db.add(ReviewFile(review_id=review.id, path=changed.path, previous_path=changed.previous_path,
                          status=changed.status, additions=changed.additions or len(changed.added_lines),
                          deletions=changed.deletions or len(changed.removed_lines),
                          patch=changed.patch, skip_reason=changed.skip_reason))

    db.query(Finding).filter(Finding.review_id == review.id).delete(synchronize_session=False)
    seen: set[str] = set()
    for f in findings:
        if f.fingerprint in seen:
            continue
        seen.add(f.fingerprint)
        db.add(Finding(review_id=review.id, fingerprint=f.fingerprint, title=f.title[:500],
                       description=f.description, category=f.category, severity=f.severity,
                       confidence=f.confidence, status=f.status, source=f.source, file_path=f.file,
                       start_line=f.start_line, end_line=f.end_line, comment_line=f.comment_line,
                       evidence=f.evidence, explanation=f.explanation, suggested_fix=f.suggested_fix,
                       context_refs=json.dumps(f.context_refs)))

    for result in state.get("retrievals", []):
        db.add(RetrievalLog(
            pr_id=pr_id, review_id=review.id, repo_github_id=repo_github_id,
            query_text=result.query[:4000], strategy=result.mode,
            retrieved_chunk_ids=",".join(c.point_id for c in result.chunks),
            scores=",".join("" if c.dense_score is None else f"{c.dense_score:.4f}" for c in result.chunks),
            bm25_scores=",".join("" if c.bm25_score is None else f"{c.bm25_score:.4f}" for c in result.chunks),
            fused_scores=",".join(f"{c.score:.6f}" for c in result.chunks),
            retrieval_latency_ms=result.dense_latency_ms, bm25_latency_ms=result.bm25_latency_ms,
            token_cost=None, degraded_reason=result.degraded_reason,
        ))
    db.commit()


def post_review(db: Session, review: Review, repo_full_name: str, pr_number: int, github: GitHubClient) -> int:
    """Post the stored review to GitHub (summary + inline comments)."""
    rows = db.query(Finding).filter(Finding.review_id == review.id, Finding.status == "confirmed",
                                    Finding.comment_line.isnot(None)).all()
    rows.sort(key=lambda r: (-SEVERITY_RANK[r.severity], -r.confidence, r.id))
    rows = rows[:settings.REVIEW_MAX_INLINE_COMMENTS]
    locations = json.loads(review.raw_output or "{}").get("context", {})
    comments = []
    for row in rows:
        finding = ReviewFinding(title=row.title, description=row.description, category=row.category,
                                severity=row.severity, confidence=row.confidence, status=row.status,
                                source=row.source, file=row.file_path, start_line=row.start_line,
                                end_line=row.end_line, explanation=row.explanation or "",
                                suggested_fix=row.suggested_fix or "",
                                context_refs=json.loads(row.context_refs or "[]"))
        start = row.start_line if row.start_line < row.comment_line else None
        comments.append(InlineComment(row.file_path, row.comment_line, render_inline_body(finding, locations),
                                      start_line=start))
    posted = github.create_review(repo_full_name, pr_number, review.head_sha, review.summary or "", comments)
    if posted.inline_posted:
        for row in rows:
            row.posted_inline = True
    review.github_review_id = posted.review_id
    db.commit()
    return posted.review_id


# ---------------------------------------------------------------- orchestration

def run_review(db: Session, deps: ReviewDeps, repo_github_id: int, repo_full_name: str, pr_number: int,
               installation_id: int | None = None, post: bool | None = None) -> dict:
    started = time.perf_counter()
    post = settings.GITHUB_POST_REVIEWS if post is None else post

    pull = deps.github.get_pull(repo_full_name, pr_number)
    head_sha, base_sha = pull["head"]["sha"], pull["base"]["sha"]
    base_repo = (pull.get("base") or {}).get("repo") or {}
    repo_id, pr_id = upsert_repository_and_pr(
        db, repo_github_id, repo_full_name, pr_number, installation_id, pull.get("title"),
        (pull.get("user") or {}).get("login"), head_sha,
        default_branch=base_repo.get("default_branch"), private=base_repo.get("private"))
    if pull.get("state") != "open":
        db.query(PullRequest).filter_by(id=pr_id).update({"status": "completed"})
        db.commit()
        return {"status": "skipped", "reason": f"PR is {pull.get('state')}", "pr_id": pr_id}

    review, reason = claim_review(db, pr_id, head_sha, base_sha)
    if review is None:
        logger.info("Skipping review of %s#%d@%s: %s", repo_full_name, pr_number, head_sha[:7], reason)
        return {"status": "skipped", "reason": reason, "pr_id": pr_id}
    logger.info("Review %d claimed for %s#%d@%s (attempt %d)", review.id, repo_full_name, pr_number,
                head_sha[:7], review.attempts)

    try:
        index_info: dict = {}
        if review.status != "analyzed":
            repo = db.get(Repository, repo_id)
            notes: list[str] = []
            try:
                index_info = ensure_indexed(db, repo, base_sha, deps)
            except EmbeddingError as exc:
                db.rollback()
                notes.append(f"Repository indexing failed ({exc}); "
                             + ("using the previous index." if repo.indexed_sha else "no repository context."))
            retriever = None
            if deps.qdrant.collection_exists(collection_name(repo_github_id)):
                retriever = HybridRetriever(repo_github_id, deps.qdrant, deps.gemini_client)

            files = parse_pull_request_files(deps.github.list_pull_files(repo_full_name, pr_number),
                                             settings.REVIEW_MAX_PATCH_LINES, settings.REVIEW_MAX_FILES)
            contents = {}
            for f in files:
                if f.reviewable and f.added_lines:
                    text = deps.github.get_file_content(repo_full_name, f.path, head_sha)
                    if text is not None:
                        contents[f.path] = text

            state = ReviewPipeline(deps.llm, retriever, top_k=settings.REVIEW_TOP_K).run({
                "repo_full_name": repo_full_name, "pr_number": pr_number,
                "pr_title": pull.get("title") or "", "pr_body": pull.get("body") or "",
                "files": files, "file_contents": contents, "errors": notes,
            })
            persist_analysis(db, review, pr_id, repo_github_id, state, getattr(deps.llm, "model", None))

        github_review_id = None
        if post:
            github_review_id = post_review(db, review, repo_full_name, pr_number, deps.github)
        review.status = "completed"
        review.completed_at = _utcnow()
        review.total_latency_ms = int((time.perf_counter() - started) * 1000)
        db.query(PullRequest).filter_by(id=pr_id).update({"status": "completed"})
        db.commit()
        logger.info("Review %d completed: risk=%s (%.2f), %d confirmed issue(s), posted=%s, %d ms",
                    review.id, review.risk_label, review.risk_score or 0, review.issues_found or 0,
                    github_review_id is not None, review.total_latency_ms)
        return {"status": "completed", "review_id": review.id, "pr_id": pr_id, "risk_label": review.risk_label,
                "risk_score": review.risk_score, "issues_found": review.issues_found,
                "github_review_id": github_review_id, "index": index_info}
    except Exception as exc:
        db.rollback()
        review = db.get(Review, review.id)
        if review.status != "analyzed":    # keep analyzed results so a retry only re-posts
            review.status = "failed"
        review.error = f"{type(exc).__name__}: {exc}"[:4000]
        db.query(PullRequest).filter_by(id=pr_id).update({"status": "failed"})
        db.commit()
        logger.exception("Review %d failed: %s", review.id, exc)
        raise
