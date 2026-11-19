"""Read-only review API (results are produced by the Celery worker)."""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.finding import Finding
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.review import Review

router = APIRouter(tags=["reviews"])


def _review_dict(review: Review, include_findings: bool) -> dict:
    data = {
        "id": review.id, "pr_id": review.pr_id, "head_sha": review.head_sha, "status": review.status,
        "error": review.error, "risk_score": review.risk_score, "risk_label": review.risk_label,
        "risk_breakdown": json.loads(review.risk_breakdown) if review.risk_breakdown else None,
        "issues_found": review.issues_found, "files_reviewed": review.files_reviewed,
        "model": review.model, "retrieval_mode": review.retrieval_mode,
        "total_latency_ms": review.total_latency_ms, "total_tokens": review.total_tokens,
        "github_review_id": review.github_review_id, "attempts": review.attempts,
        "created_at": review.created_at, "completed_at": review.completed_at,
    }
    if include_findings:
        data["summary"] = review.summary
        data["findings"] = [{
            "id": f.id, "title": f.title, "description": f.description, "category": f.category,
            "severity": f.severity, "confidence": f.confidence, "status": f.status, "source": f.source,
            "file": f.file_path, "start_line": f.start_line, "end_line": f.end_line,
            "comment_line": f.comment_line, "evidence": f.evidence, "explanation": f.explanation,
            "suggested_fix": f.suggested_fix, "posted_inline": f.posted_inline,
        } for f in review.findings]
    return data


@router.get("/reviews/{review_id}")
def get_review(review_id: int, db: Session = Depends(get_db)) -> dict:
    review = db.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    return _review_dict(review, include_findings=True)


@router.get("/repos/{repo_github_id}/pulls/{pr_number}/reviews")
def list_pr_reviews(repo_github_id: int, pr_number: int, db: Session = Depends(get_db)) -> dict:
    pr = (db.query(PullRequest).join(Repository, Repository.id == PullRequest.repo_id)
          .filter(Repository.github_id == repo_github_id, PullRequest.pr_number == pr_number).first())
    if pr is None:
        raise HTTPException(status_code=404, detail="Pull request not found")
    reviews = db.query(Review).filter(Review.pr_id == pr.id).order_by(Review.id.desc()).all()
    return {"pr_number": pr_number, "status": pr.status, "head_sha": pr.head_sha,
            "reviews": [_review_dict(r, include_findings=False) for r in reviews]}


@router.get("/findings/stats")
def finding_stats(db: Session = Depends(get_db)) -> dict:
    """Aggregate counts for evaluation dashboards."""
    from sqlalchemy import func
    rows = db.query(Finding.category, Finding.severity, Finding.status, func.count()).group_by(
        Finding.category, Finding.severity, Finding.status).all()
    return {"counts": [{"category": c, "severity": s, "status": st, "count": n} for c, s, st, n in rows]}
