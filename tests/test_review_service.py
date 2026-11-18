"""
Service-level integration tests: real PostgreSQL (codelens_test) + real LangGraph
pipeline + in-memory Qdrant. GitHub and Gemini are replaced by fakes, because
CI must not call external services; their real clients are tested separately
against mock HTTP transports (test_github.py, test_embedder.py, test_review.py).
"""
import json
from pathlib import Path

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.github.client import GitHubTransientError, PostedReview
from app.models import Finding, PullRequest, Repository, RetrievalLog, Review
from app.services.review_service import ReviewDeps, run_review
from tests.fakes import HashingGemini
from tests.test_review import NEW_SESSION, PATCH, ScriptedLLM, finding

TEST_DATABASE_URL = settings.DATABASE_URL.rsplit("/", 1)[0] + "/codelens_test"
REPO_ID = 987654321
BASE_FILES = {
    "app/auth/session.py": "import subprocess\n\n\ndef create_session(store, user_id):\n"
                           "    token = make_token(user_id)\n    store.save(token, user_id)\n    return token\n",
    "app/auth/tokens.py": "def make_token(user_id):\n    return sign(user_id, secrets.token_urlsafe(16))\n",
}


class FakeGitHub:
    def __init__(self, head="h1", base="b1", state="open", fail_post=0):
        self.head, self.base, self.state = head, base, state
        self.fail_post = fail_post
        self.posted: list[dict] = []
        self.archive_downloads: list[str] = []
        self.base_files = dict(BASE_FILES)

    def get_pull(self, repo, number):
        return {"head": {"sha": self.head}, "base": {"sha": self.base}, "state": self.state,
                "title": "Session changes", "body": "Refactor", "user": {"login": "dev"}}

    def list_pull_files(self, repo, number):
        return [{"filename": "app/auth/session.py", "status": "modified", "patch": PATCH,
                 "additions": 6, "deletions": 1}]

    def get_file_content(self, repo, path, ref):
        return NEW_SESSION if path == "app/auth/session.py" else None

    def download_python_sources(self, repo, ref, dest: Path):
        self.archive_downloads.append(ref)
        for rel, text in self.base_files.items():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            (dest / rel).write_text(text)
        return len(self.base_files)

    def create_review(self, repo, number, commit_id, body, comments):
        if self.fail_post:
            self.fail_post -= 1
            raise GitHubTransientError("GitHub 502", 502)
        self.posted.append({"commit_id": commit_id, "body": body, "comments": comments})
        return PostedReview(review_id=4242 + len(self.posted), inline_posted=len(comments), inline_dropped=0)


@pytest.fixture
def db():
    engine = create_engine(TEST_DATABASE_URL)
    session = sessionmaker(bind=engine)()
    for model in (RetrievalLog, Finding, Review, PullRequest, Repository):
        session.query(model).delete(synchronize_session=False)
    session.commit()
    yield session
    session.rollback()
    session.close()
    engine.dispose()


def _llm():
    return ScriptedLLM({"bug": [finding(title="Token no longer bound to user", context_refs=["C1"])]})


def _deps(github, llm=None, qdrant=None):
    return ReviewDeps(github=github, qdrant=qdrant or QdrantClient(":memory:"), llm=llm or _llm(),
                      gemini_client=HashingGemini())


def test_end_to_end_review_is_indexed_analyzed_persisted_and_posted(db):
    github = FakeGitHub()
    result = run_review(db, _deps(github), REPO_ID, "o/r", 7, installation_id=55, post=True)

    assert result["status"] == "completed" and result["github_review_id"] == 4243
    assert result["index"]["indexed"] and result["index"]["chunks"] >= 2
    assert github.archive_downloads == ["b1"]                     # base commit indexed

    review = db.get(Review, result["review_id"])
    assert (review.status, review.head_sha, review.base_sha) == ("completed", "h1", "b1")
    assert review.risk_label in {"high", "critical"} and review.issues_found == 2
    assert json.loads(review.risk_breakdown)["formula"].startswith("1 - exp")
    assert review.total_tokens == 360 and review.total_cost_usd == 0.0   # 3 LLM calls x (100 + 20)
    assert review.retrieval_mode == "hybrid" and review.total_latency_ms is not None

    findings = db.query(Finding).filter_by(review_id=review.id).all()
    assert {f.source for f in findings} == {"llm:bug", "rule:SEC002"}
    assert all(f.posted_inline for f in findings if f.comment_line)

    logs = db.query(RetrievalLog).filter_by(review_id=review.id).all()
    assert logs and all(l.strategy == "hybrid" and l.retrieved_chunk_ids for l in logs)

    repo = db.query(Repository).filter_by(github_id=REPO_ID).one()
    assert repo.indexed_sha == "b1" and repo.installation_id == 55
    pr = db.query(PullRequest).filter_by(repo_id=repo.id, pr_number=7).one()
    assert (pr.status, pr.head_sha) == ("completed", "h1")

    posted = github.posted[0]
    assert posted["commit_id"] == "h1" and "CodeLens CI review" in posted["body"]
    assert {c.line for c in posted["comments"]} == {5, 11}
    body = next(c.body for c in posted["comments"] if c.line == 5)
    context = json.loads(review.raw_output)["context"]
    assert f"Related repository code: `{context['C1']}`" in body     # cites a real retrieved chunk


def test_duplicate_delivery_for_the_same_commit_is_not_reviewed_twice(db):
    github, llm = FakeGitHub(), _llm()
    deps = _deps(github, llm)
    run_review(db, deps, REPO_ID, "o/r", 7, post=True)
    calls_after_first = len(llm.systems)
    second = run_review(db, deps, REPO_ID, "o/r", 7, post=True)
    assert second == {"status": "skipped", "reason": "already reviewed", "pr_id": second["pr_id"]}
    assert len(github.posted) == 1 and len(llm.systems) == calls_after_first
    assert db.query(Review).count() == 1


def test_post_failure_keeps_analysis_and_retry_only_posts(db):
    github, llm = FakeGitHub(fail_post=1), _llm()
    deps = _deps(github, llm)
    with pytest.raises(GitHubTransientError):
        run_review(db, deps, REPO_ID, "o/r", 7, post=True)
    review = db.query(Review).one()
    assert review.status == "analyzed" and "GitHub 502" in review.error
    llm_calls = len(llm.systems)

    result = run_review(db, deps, REPO_ID, "o/r", 7, post=True)   # what the Celery retry does
    assert result["status"] == "completed" and len(github.posted) == 1
    assert len(llm.systems) == llm_calls                           # no second (paid-for) analysis
    assert db.query(Review).one().attempts == 2


def test_new_commit_gets_a_new_review_and_reuses_the_index(db):
    qdrant = QdrantClient(":memory:")
    github = FakeGitHub()
    run_review(db, _deps(github, qdrant=qdrant), REPO_ID, "o/r", 7, post=False)
    github.head = "h2"                                             # new push, same base
    result = run_review(db, _deps(github, qdrant=qdrant), REPO_ID, "o/r", 7, post=False)
    assert result["status"] == "completed" and result["index"] == {"indexed": False, "reason": "up to date"}
    assert github.archive_downloads == ["b1"] and db.query(Review).count() == 2

    github.head, github.base = "h3", "b2"                          # base branch moved on
    github.base_files["app/auth/new_module.py"] = "def audit(event):\n    return log(event)\n"
    gemini = HashingGemini()
    deps = _deps(github, qdrant=qdrant)
    deps.gemini_client = gemini
    result = run_review(db, deps, REPO_ID, "o/r", 7, post=False)
    assert result["index"]["embedded"] == 1 and result["index"]["reused"] >= 2   # incremental
    assert gemini.calls[0] == ["def audit(event):\n    return log(event)"]


def test_closed_pull_request_is_skipped(db):
    result = run_review(db, _deps(FakeGitHub(state="closed")), REPO_ID, "o/r", 7, post=True)
    assert result["status"] == "skipped" and "closed" in result["reason"]
    assert db.query(Review).count() == 0


def test_merged_pull_request_can_be_reviewed_explicitly_for_evaluation(db):
    result = run_review(db, _deps(FakeGitHub(state="closed")), REPO_ID, "o/r", 7, post=False, allow_closed=True)
    assert result["status"] == "completed" and result["issues_found"] == 2


def test_unexpected_failure_marks_review_failed_and_redelivery_can_retry(db):
    github = FakeGitHub()
    github.list_pull_files = lambda repo, number: (_ for _ in ()).throw(ValueError("boom"))
    with pytest.raises(ValueError):
        run_review(db, _deps(github), REPO_ID, "o/r", 7, post=False)
    review = db.query(Review).one()
    assert review.status == "failed" and "boom" in review.error
    assert db.query(PullRequest).one().status == "failed"

    github.list_pull_files = FakeGitHub().list_pull_files
    assert run_review(db, _deps(github), REPO_ID, "o/r", 7, post=False)["status"] == "completed"


def test_reviews_api_returns_stored_review_and_findings(db):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models.database import get_db

    result = run_review(db, _deps(FakeGitHub()), REPO_ID, "o/r", 7, post=False)
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        review = client.get(f"/reviews/{result['review_id']}").json()
        listing = client.get(f"/repos/{REPO_ID}/pulls/7/reviews").json()
    finally:
        app.dependency_overrides.clear()
    assert review["status"] == "completed" and review["risk_breakdown"]["label"] == review["risk_label"]
    assert {f["source"] for f in review["findings"]} == {"llm:bug", "rule:SEC002"}
    assert listing["reviews"][0]["id"] == result["review_id"] and listing["head_sha"] == "h1"
