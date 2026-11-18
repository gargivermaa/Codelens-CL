"""Celery task retry policy, new webhook actions and the read-only reviews API."""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.github.client import GitHubError, GitHubRateLimitError, GitHubTransientError
from app.main import app
from app.tasks import review_task


class _Retry(Exception):
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def task(monkeypatch):
    monkeypatch.setattr(review_task, "build_deps", lambda installation_id: object())
    monkeypatch.setattr(review_task, "SessionLocal", lambda: type("S", (), {"close": lambda self: None})())
    monkeypatch.setattr(review_task.process_pr_review, "retry", lambda **kw: _Retry(**kw))
    return review_task.process_pr_review


def _raise(exc):
    def run(*args, **kwargs):
        raise exc
    return run


def test_successful_task_returns_the_service_result(task, monkeypatch):
    monkeypatch.setattr(review_task, "run_review", lambda *a, **k: {"status": "completed", "review_id": 1})
    assert task(1, "o/r", 2) == {"status": "completed", "review_id": 1}


def test_rate_limit_retries_after_github_reset(task, monkeypatch):
    monkeypatch.setattr(review_task, "run_review", _raise(GitHubRateLimitError("limit", retry_after=42)))
    with pytest.raises(_Retry) as info:
        task(1, "o/r", 2)
    assert info.value.kwargs["countdown"] == 42


@pytest.mark.parametrize("exc", [GitHubTransientError("502", 502),
                                 OperationalError("SELECT 1", {}, Exception("db down"))])
def test_transient_failures_retry_with_backoff(task, monkeypatch, exc):
    monkeypatch.setattr(review_task, "run_review", _raise(exc))
    with pytest.raises(_Retry) as info:
        task(1, "o/r", 2)
    assert info.value.kwargs["countdown"] == 10


def test_permanent_failures_are_not_retried(task, monkeypatch):
    monkeypatch.setattr(review_task, "run_review", _raise(GitHubError("404", 404)))
    with pytest.raises(GitHubError):
        task(1, "o/r", 2)


def _post(client, payload, event="pull_request"):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(settings.GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhook", content=body, headers={"X-GitHub-Event": event, "X-Hub-Signature-256": sig})


@pytest.mark.parametrize("action, queued", [("reopened", True), ("ready_for_review", True),
                                            ("closed", False), ("edited", False)])
def test_review_actions(monkeypatch, action, queued):
    calls = []
    monkeypatch.setattr("app.tasks.review_task.process_pr_review.delay", lambda **kw: calls.append(kw))
    payload = {"action": action, "repository": {"id": 1, "full_name": "o/r"},
               "pull_request": {"number": 3, "title": "t", "user": {"login": "u"}}}
    response = _post(TestClient(app), payload)
    assert response.status_code == 200 and (response.json()["status"] == "queued") == queued
    assert len(calls) == (1 if queued else 0)


def test_unknown_review_returns_404():
    assert TestClient(app).get("/reviews/999999999").status_code == 404
