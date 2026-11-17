"""
Webhook tests. No database, no Redis: the Celery .delay() call is replaced by
a fake, so these verify only the HTTP layer (signature check, event routing,
argument mapping, queue-failure handling).
"""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from kombu.exceptions import OperationalError

from app.config import settings
from app.main import app

client = TestClient(app)

PR_PAYLOAD = {
    "action": "opened",
    "installation": {"id": 12345},
    "repository": {"id": 1296269, "full_name": "octocat/hello-world"},
    "pull_request": {"number": 42, "title": "Fix bug", "user": {"login": "dev"}},
}


def _sign(body: bytes) -> str:
    digest = hmac.new(settings.GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256)
    return "sha256=" + digest.hexdigest()


def _post(payload: dict, event: str = "pull_request", signature: str | None = "auto"):
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event, "Content-Type": "application/json"}
    if signature == "auto":
        headers["X-Hub-Signature-256"] = _sign(body)
    elif signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return client.post("/webhook", content=body, headers=headers)


@pytest.fixture
def fake_delay(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.tasks.review_task.process_pr_review.delay",
        lambda **kwargs: calls.append(kwargs),
    )
    return calls


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_missing_signature_rejected(fake_delay):
    assert _post(PR_PAYLOAD, signature=None).status_code == 401
    assert fake_delay == []


def test_wrong_signature_rejected(fake_delay):
    assert _post(PR_PAYLOAD, signature="sha256=deadbeef").status_code == 401
    assert fake_delay == []


@pytest.mark.parametrize("action", ["opened", "synchronize"])
def test_pr_events_are_queued_with_correct_args(fake_delay, action):
    resp = _post({**PR_PAYLOAD, "action": action})
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert fake_delay == [{
        "repo_github_id": 1296269,
        "repo_full_name": "octocat/hello-world",
        "pr_number": 42,
        "installation_id": 12345,
        "title": "Fix bug",
        "author": "dev",
    }]


def test_irrelevant_event_ignored(fake_delay):
    resp = _post({"action": "labeled"}, event="issues")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert fake_delay == []


def test_queue_down_returns_503(monkeypatch):
    def boom(**kwargs):
        raise OperationalError("redis down")

    monkeypatch.setattr("app.tasks.review_task.process_pr_review.delay", boom)
    assert _post(PR_PAYLOAD).status_code == 503