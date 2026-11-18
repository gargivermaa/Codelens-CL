"""
Dashboard API (/api/*): read-only views over persisted repositories, PRs,
reviews, findings and benchmark reports. Every number comes from PostgreSQL
or from the benchmark JSON files - nothing here is synthesized.
"""
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.finding import Finding
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.retrieval_log import RetrievalLog
from app.models.review import Review
from app.models.review_file import ReviewFile

router = APIRouter(prefix="/api", tags=["dashboard"])

SEVERITIES = ("critical", "high", "medium", "low", "info")
RISK_LABELS = ("low", "medium", "high", "critical")
BENCHMARK_RESULTS = Path(__file__).resolve().parents[2] / "benchmarks" / "results"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _latest_review_ids(db: Session):
    """Subquery: latest review id per pull request."""
    return (db.query(Review.pr_id, func.max(Review.id).label("review_id"))
            .group_by(Review.pr_id).subquery())


def _severity_counts(db: Session, review_ids: list[int], confirmed_only: bool = False) -> dict[int, dict]:
    if not review_ids:
        return {}
    query = db.query(Finding.review_id, Finding.severity, func.count()).filter(Finding.review_id.in_(review_ids))
    if confirmed_only:
        query = query.filter(Finding.status == "confirmed")
    counts: dict[int, dict] = defaultdict(lambda: {s: 0 for s in SEVERITIES})
    for review_id, severity, n in query.group_by(Finding.review_id, Finding.severity):
        counts[review_id][severity] = n
    return counts


def _repo_dict(repo: Repository) -> dict:
    return {
        "github_id": repo.github_id, "full_name": repo.full_name, "default_branch": repo.default_branch,
        "private": repo.private, "installation_id": repo.installation_id,
        "indexed": repo.indexed_sha is not None, "indexed_sha": repo.indexed_sha,
        "indexed_at": _iso(repo.indexed_at), "indexed_chunks": repo.indexed_chunks,
        "created_at": _iso(repo.created_at), "html_url": f"https://github.com/{repo.full_name}",
    }


def _review_summary(review: Review | None, counts: dict | None) -> dict | None:
    if review is None:
        return None
    return {
        "id": review.id, "status": review.status, "head_sha": review.head_sha,
        "risk_score": review.risk_score, "risk_label": review.risk_label,
        "issues_found": review.issues_found, "severity_counts": counts or {s: 0 for s in SEVERITIES},
        "total_latency_ms": review.total_latency_ms, "created_at": _iso(review.created_at),
        "completed_at": _iso(review.completed_at), "error": review.error,
        "posted": review.github_review_id is not None,
    }


def _pull_rows(db: Session, repo_github_id: int | None = None, risk: str | None = None,
               status: str | None = None, limit: int = 100) -> list[dict]:
    latest = _latest_review_ids(db)
    query = (db.query(PullRequest, Repository, Review)
             .join(Repository, Repository.id == PullRequest.repo_id)
             .outerjoin(latest, latest.c.pr_id == PullRequest.id)
             .outerjoin(Review, Review.id == latest.c.review_id))
    if repo_github_id is not None:
        query = query.filter(Repository.github_id == repo_github_id)
    if risk:
        query = query.filter(Review.risk_label == risk)
    if status:
        query = query.filter(PullRequest.status == status)
    rows = query.order_by(func.coalesce(Review.created_at, PullRequest.created_at).desc()).limit(limit).all()
    counts = _severity_counts(db, [r.id for _, _, r in rows if r is not None])
    return [{
        "id": pr.id, "number": pr.pr_number, "title": pr.title, "author": pr.author, "status": pr.status,
        "head_sha": pr.head_sha, "created_at": _iso(pr.created_at),
        "repository": {"github_id": repo.github_id, "full_name": repo.full_name},
        "html_url": f"https://github.com/{repo.full_name}/pull/{pr.pr_number}",
        "latest_review": _review_summary(review, counts.get(review.id) if review else None),
    } for pr, repo, review in rows]


# ------------------------------------------------------------------ endpoints

@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    latest = _latest_review_ids(db)
    latest_reviews = db.query(Review).join(latest, latest.c.review_id == Review.id).all()
    risk_distribution = Counter(r.risk_label for r in latest_reviews if r.risk_label)
    severity = dict.fromkeys(SEVERITIES, 0)
    for sev, n in db.query(Finding.severity, func.count()).group_by(Finding.severity):
        severity[sev] = n
    return {
        "repositories": db.query(func.count(Repository.id)).scalar(),
        "pull_requests": db.query(func.count(PullRequest.id)).scalar(),
        "pull_requests_reviewed": sum(1 for r in latest_reviews if r.status in ("analyzed", "completed")),
        "reviews_total": db.query(func.count(Review.id)).scalar(),
        "high_risk_pull_requests": sum(1 for r in latest_reviews if r.risk_label in ("high", "critical")),
        "findings_total": db.query(func.count(Finding.id)).scalar(),
        "findings_confirmed": db.query(func.count(Finding.id)).filter(Finding.status == "confirmed").scalar(),
        "findings_by_severity": severity,
        "risk_distribution": {label: risk_distribution.get(label, 0) for label in RISK_LABELS},
        "failed_reviews": sum(1 for r in latest_reviews if r.status == "failed"),
        "recent_pull_requests": _pull_rows(db, limit=8),
    }


@router.get("/repositories")
def list_repositories(db: Session = Depends(get_db)) -> dict:
    repos = db.query(Repository).order_by(Repository.full_name).all()
    pr_counts = dict(db.query(PullRequest.repo_id, func.count()).group_by(PullRequest.repo_id).all())
    review_stats = {repo_id: (n, last) for repo_id, n, last in
                    db.query(PullRequest.repo_id, func.count(Review.id), func.max(Review.created_at))
                    .join(Review, Review.pr_id == PullRequest.id).group_by(PullRequest.repo_id)}
    out = []
    for repo in repos:
        n_reviews, last = review_stats.get(repo.id, (0, None))
        out.append({**_repo_dict(repo), "pull_requests": pr_counts.get(repo.id, 0),
                    "reviews": n_reviews, "last_review_at": _iso(last)})
    return {"repositories": out}


@router.get("/repositories/{github_id}")
def repository_detail(github_id: int, db: Session = Depends(get_db)) -> dict:
    repo = db.query(Repository).filter_by(github_id=github_id).first()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return {"repository": _repo_dict(repo), "pull_requests": _pull_rows(db, repo_github_id=github_id, limit=500)}


@router.get("/pulls")
def list_pulls(repo: int | None = Query(None, description="repository GitHub id"),
               risk: str | None = Query(None), status: str | None = Query(None),
               limit: int = Query(200, le=1000), db: Session = Depends(get_db)) -> dict:
    return {"pull_requests": _pull_rows(db, repo, risk, status, limit)}


@router.get("/pulls/{pr_id}")
def pull_detail(pr_id: int, db: Session = Depends(get_db)) -> dict:
    pr = db.get(PullRequest, pr_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="Pull request not found")
    repo = db.get(Repository, pr.repo_id)
    reviews = db.query(Review).filter_by(pr_id=pr.id).order_by(Review.id.desc()).all()
    counts = _severity_counts(db, [r.id for r in reviews])
    return {"id": pr.id, "number": pr.pr_number, "title": pr.title, "author": pr.author, "status": pr.status,
            "repository": _repo_dict(repo), "html_url": f"https://github.com/{repo.full_name}/pull/{pr.pr_number}",
            "reviews": [_review_summary(r, counts.get(r.id)) for r in reviews]}


@router.get("/reviews/{review_id}")
def review_detail(review_id: int, db: Session = Depends(get_db)) -> dict:
    review = db.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    pr = db.get(PullRequest, review.pr_id)
    repo = db.get(Repository, pr.repo_id)
    raw = json.loads(review.raw_output) if review.raw_output else {}
    files = db.query(ReviewFile).filter_by(review_id=review.id).order_by(ReviewFile.id).all()
    retrievals = db.query(func.count(RetrievalLog.id)).filter_by(review_id=review.id).scalar()
    counts = _severity_counts(db, [review.id]).get(review.id)
    return {
        "review": {**_review_summary(review, counts), "base_sha": review.base_sha, "model": review.model,
                   "retrieval_mode": review.retrieval_mode, "files_reviewed": review.files_reviewed,
                   "total_tokens": review.total_tokens, "attempts": review.attempts,
                   "github_review_id": review.github_review_id, "summary_markdown": review.summary,
                   "risk_breakdown": json.loads(review.risk_breakdown) if review.risk_breakdown else None},
        "pull_request": {"id": pr.id, "number": pr.pr_number, "title": pr.title, "author": pr.author,
                         "status": pr.status,
                         "html_url": f"https://github.com/{repo.full_name}/pull/{pr.pr_number}"},
        "repository": {"github_id": repo.github_id, "full_name": repo.full_name},
        "findings": [{
            "id": f.id, "title": f.title, "description": f.description, "category": f.category,
            "severity": f.severity, "confidence": f.confidence, "status": f.status, "source": f.source,
            "file": f.file_path, "start_line": f.start_line, "end_line": f.end_line,
            "comment_line": f.comment_line, "evidence": f.evidence, "explanation": f.explanation,
            "suggested_fix": f.suggested_fix, "posted_inline": f.posted_inline,
            "context_refs": json.loads(f.context_refs or "[]"),
        } for f in review.findings],
        "files": [{"path": f.path, "previous_path": f.previous_path, "status": f.status,
                   "additions": f.additions, "deletions": f.deletions, "patch": f.patch,
                   "skip_reason": f.skip_reason} for f in files],
        "context": raw.get("context", {}),
        "notes": raw.get("errors", []),
        "dropped_findings": raw.get("dropped", []),
        "llm_calls": raw.get("llm_calls", []),
        "timings_ms": raw.get("timings_ms", {}),
        "retrieval_queries": retrievals,
    }


@router.get("/analytics")
def analytics(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)) -> dict:
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    reviews = db.query(Review).filter(Review.created_at >= since).order_by(Review.created_at).all()
    history: dict[str, dict] = {}
    for r in reviews:
        day = r.created_at.date().isoformat()
        entry = history.setdefault(day, {"date": day, "reviews": 0, "high_risk": 0, "findings": 0, "_risk": []})
        entry["reviews"] += 1
        entry["findings"] += r.issues_found or 0
        if r.risk_label in ("high", "critical"):
            entry["high_risk"] += 1
        if r.risk_score is not None:
            entry["_risk"].append(r.risk_score)
    for entry in history.values():
        scores = entry.pop("_risk")
        entry["avg_risk"] = round(sum(scores) / len(scores), 3) if scores else None

    categories = [{"category": c, "count": n} for c, n in
                  db.query(Finding.category, func.count()).group_by(Finding.category).order_by(func.count().desc())]
    sources = Counter()
    for source, n in db.query(Finding.source, func.count()).group_by(Finding.source):
        for part in source.split("+"):
            sources[part.split(":")[0]] += n
    status = dict(db.query(Finding.status, func.count()).group_by(Finding.status).all())

    repo_rows = (db.query(Repository.full_name, func.count(Review.id), func.avg(Review.risk_score),
                          func.sum(Review.issues_found))
                 .join(PullRequest, PullRequest.repo_id == Repository.id)
                 .join(Review, Review.pr_id == PullRequest.id)
                 .group_by(Repository.full_name).all())
    latency = sorted(r.total_latency_ms for r in reviews if r.total_latency_ms)
    risk = Counter(r.risk_label for r in reviews if r.risk_label)
    return {
        "days": days,
        "review_history": list(history.values()),
        "risk_distribution": {label: risk.get(label, 0) for label in RISK_LABELS},
        "categories": categories,
        "finding_status": {"confirmed": status.get("confirmed", 0), "possible": status.get("possible", 0)},
        "finding_sources": dict(sources),
        "repositories": [{"full_name": name, "reviews": n, "avg_risk": round(avg or 0, 3),
                          "findings": int(total or 0)} for name, n, avg, total in repo_rows],
        "latency_ms": {"n": len(latency),
                       "median": latency[len(latency) // 2] if latency else None,
                       "max": latency[-1] if latency else None},
    }


def _load_report(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _report_key(report: dict) -> str | None:
    kind = report.get("benchmark")
    if kind == "review":
        # v1 reports had only `mode`: "llm" meant LLM + repository context.
        config = report.get("config") or {"llm": "llm_context"}.get(report.get("mode"), report.get("mode"))
        return f"review:{config}:{report.get('split', 'v1')}"
    if kind == "retrieval":
        return f"retrieval:{report.get('split', 'v1')}"
    if kind == "retrieval-sweep":
        return "retrieval-sweep:validation"
    return None


def _summarize_report(path: Path, report: dict) -> dict:
    summary = {k: v for k, v in report.items() if k not in ("cases", "results")}
    summary["file"] = path.name
    summary["run_id"] = report.get("run_id") or path.stem
    cases = report.get("cases") or []
    if report.get("benchmark") == "review" and "cases_with_errors" not in report:
        llm_mode = (report.get("config") or report.get("mode")) not in ("rules", None)
        summary["cases_with_errors"] = sum(1 for c in cases if c.get("errors")) if llm_mode else 0
    for block in ("confirmed_only", "including_possible"):
        if isinstance(summary.get(block), dict):
            summary[block] = {k: v for k, v in summary[block].items() if k != "per_case"}
    for mode in (summary.get("modes") or {}).values():
        if isinstance(mode, dict):
            mode.pop("per_query", None)
    if report.get("benchmark") == "retrieval-sweep":
        summary["results"] = report.get("results", [])
    return summary


@router.get("/benchmarks")
def benchmarks() -> dict:
    """Latest measured report per (benchmark, configuration, split), plus the run list."""
    latest: dict[str, dict] = {}
    runs = []
    if BENCHMARK_RESULTS.is_dir():
        for path in sorted(BENCHMARK_RESULTS.glob("*.json"), key=lambda p: p.stat().st_mtime):
            report = _load_report(path)
            key = _report_key(report) if report else None
            if key is None:
                continue
            summary = _summarize_report(path, report)
            latest[key] = summary
            runs.append({"run_id": summary["run_id"], "key": key, "file": path.name,
                         "timestamp": (report.get("environment") or {}).get("timestamp")})
    return {"reports": latest, "runs": list(reversed(runs))}


@router.get("/benchmarks/runs/{run_id}")
def benchmark_run(run_id: str) -> dict:
    """Full report of one run, including every case (for case inspection)."""
    if not run_id.replace("-", "").replace("_", "").replace(".", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid run id")
    path = BENCHMARK_RESULTS / f"{run_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    report = _load_report(path)
    if report is None:
        raise HTTPException(status_code=500, detail="Unreadable report")
    return report
