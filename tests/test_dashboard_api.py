"""Dashboard API over data produced by the real review service (PostgreSQL test DB)."""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.database import get_db
from app.services.review_service import run_review, upsert_repository
from tests.test_review_service import REPO_ID, FakeGitHub, _deps, db  # noqa: F401  (db fixture)


@pytest.fixture
def client(db):  # noqa: F811
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


class RepoMetaGitHub(FakeGitHub):
    def get_pull(self, repo, number):
        pull = super().get_pull(repo, number)
        pull["base"]["repo"] = {"default_branch": "main", "private": False}
        return pull


def test_review_detail_contains_findings_diff_and_breakdown(db, client):  # noqa: F811
    result = run_review(db, _deps(RepoMetaGitHub()), REPO_ID, "o/r", 7, post=False)
    data = client.get(f"/api/reviews/{result['review_id']}").json()

    assert data["review"]["status"] == "completed" and data["review"]["risk_label"] in {"high", "critical"}
    assert data["review"]["risk_breakdown"]["formula"].startswith("1 - exp")
    assert data["pull_request"]["number"] == 7 and data["repository"]["full_name"] == "o/r"
    [file] = data["files"]
    assert file["path"] == "app/auth/session.py" and file["patch"].startswith("@@ -1,6 +1,12 @@")
    assert file["additions"] == 6 and file["skip_reason"] is None
    sources = {f["source"] for f in data["findings"]}
    assert sources == {"llm:bug", "rule:SEC002"}
    finding = next(f for f in data["findings"] if f["source"] == "rule:SEC002")
    assert finding["comment_line"] == 11 and "shell=True" in finding["evidence"]
    assert data["review"]["severity_counts"]["high"] >= 1
    assert data["retrieval_queries"] >= 1 and data["llm_calls"]


def test_overview_pulls_repositories_and_analytics_reflect_persisted_reviews(db, client):  # noqa: F811
    run_review(db, _deps(RepoMetaGitHub()), REPO_ID, "o/r", 7, post=False)
    upsert_repository(db, 555, "o/installed-only", installation_id=9)

    overview = client.get("/api/overview").json()
    assert overview["repositories"] == 2 and overview["pull_requests_reviewed"] == 1
    assert overview["high_risk_pull_requests"] == 1 and overview["findings_total"] == 2
    assert sum(overview["risk_distribution"].values()) == 1
    assert overview["recent_pull_requests"][0]["latest_review"]["issues_found"] == 2

    repos = {r["full_name"]: r for r in client.get("/api/repositories").json()["repositories"]}
    assert repos["o/r"]["default_branch"] == "main" and repos["o/r"]["indexed"]
    assert repos["o/r"]["reviews"] == 1 and repos["o/installed-only"]["pull_requests"] == 0

    detail = client.get(f"/api/repositories/{REPO_ID}").json()
    assert [p["number"] for p in detail["pull_requests"]] == [7]

    pulls = client.get("/api/pulls", params={"risk": overview["recent_pull_requests"][0]["latest_review"]["risk_label"]}).json()
    assert len(pulls["pull_requests"]) == 1
    assert client.get("/api/pulls", params={"risk": "low"}).json()["pull_requests"] == []
    pr_id = pulls["pull_requests"][0]["id"]
    assert len(client.get(f"/api/pulls/{pr_id}").json()["reviews"]) == 1

    analytics = client.get("/api/analytics").json()
    assert analytics["review_history"][0]["reviews"] == 1
    assert {c["category"] for c in analytics["categories"]} == {"bug", "security"}
    assert analytics["finding_sources"] == {"llm": 1, "rule": 1}
    assert analytics["repositories"][0]["full_name"] == "o/r"


def test_unknown_ids_return_404(client):
    assert client.get("/api/reviews/999999").status_code == 404
    assert client.get("/api/repositories/999999").status_code == 404
    assert client.get("/api/pulls/999999").status_code == 404


def test_benchmarks_endpoint_reads_measured_reports(client, monkeypatch, tmp_path):
    from app.api import dashboard
    from app.evaluation import run as bench
    monkeypatch.setattr(bench, "RESULTS", tmp_path)
    monkeypatch.setattr(dashboard, "BENCHMARK_RESULTS", tmp_path)
    review = bench.run_review("rules", split="holdout")
    bench._write(review, review["run_id"])
    retrieval = bench.run_retrieval(["bm25"], 5, "holdout")
    bench._write(retrieval, retrieval["run_id"])

    data = client.get("/api/benchmarks").json()
    rules = data["reports"]["review:rules:holdout"]
    assert rules["measured"] and "cases" not in rules and "per_case" not in rules["confirmed_only"]
    assert rules["confirmed_only"]["confusion_matrix"]["rows"] == ["security", "bug", "clean"]
    assert "per_query" not in data["reports"]["retrieval:holdout"]["modes"]["bm25"]
    full = client.get(f"/api/benchmarks/runs/{review['run_id']}").json()
    assert len(full["cases"]) == review["n_cases"]
    assert client.get("/api/benchmarks/runs/..%2F..%2Fetc").status_code in (400, 404)
    assert client.get("/api/benchmarks/runs/does-not-exist").status_code == 404


def _signed(payload, event):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(settings.GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return TestClient(app).post("/webhook", content=body,
                                headers={"X-GitHub-Event": event, "X-Hub-Signature-256": sig})


def test_ping_and_installation_events(monkeypatch):
    calls = []
    monkeypatch.setattr("app.tasks.review_task.register_repositories.delay", lambda **kw: calls.append(kw))
    assert _signed({"zen": "Keep it simple"}, "ping").json()["status"] == "pong"
    installed = {"action": "created", "installation": {"id": 42},
                 "repositories": [{"id": 1, "full_name": "me/codelens-demo", "private": False}]}
    assert _signed(installed, "installation").json() == {"status": "queued", "event": "installation",
                                                         "repositories": 1}
    added = {"action": "added", "installation": {"id": 42},
             "repositories_added": [{"id": 2, "full_name": "me/other"}]}
    assert _signed(added, "installation_repositories").json()["repositories"] == 1
    assert calls[0] == {"installation_id": 42,
                        "repositories": [{"id": 1, "full_name": "me/codelens-demo", "private": False}]}
