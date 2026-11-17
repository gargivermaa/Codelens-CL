"""
POST /webhook: receives GitHub webhook events.

Flow: verify HMAC signature -> parse -> enqueue Celery task -> return fast.
GitHub expects a response within ~10 seconds, so ALL real work happens in the
Celery worker, never in this request.
"""
import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request
from kombu.exceptions import OperationalError as BrokerUnavailableError

from app.config import settings

logger = logging.getLogger("codelens.webhook")
router = APIRouter()


def verify_github_signature(payload: bytes, signature: str | None) -> bool:
    """GitHub signs every webhook with HMAC-SHA256 using the shared secret.
    We recompute it and compare in constant time to prevent timing attacks."""
    if not signature:
        return False
    expected = "sha256=" + hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/webhook")
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
):
    # Signature must be computed over the RAW bytes, before any JSON parsing.
    raw_body = await request.body()

    if not verify_github_signature(raw_body, x_hub_signature_256):
        logger.warning("Rejected webhook: invalid or missing signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    action = payload.get("action")

    if x_github_event == "pull_request" and action in ("opened", "synchronize"):
        pr = payload["pull_request"]
        pr_number = pr["number"]
        repo_name = payload["repository"]["full_name"]
        logger.info("Received PR event: repo=%s pr=#%d action=%s", repo_name, pr_number, action)

        from app.tasks.review_task import process_pr_review

        try:
            process_pr_review.delay(
                repo_github_id=payload["repository"]["id"],
                repo_full_name=repo_name,
                pr_number=pr_number,
                installation_id=payload.get("installation", {}).get("id"),
                title=pr.get("title"),
                
                author=(pr.get("user") or {}).get("login"),
            )
        except BrokerUnavailableError:
            # Redis is down: the job was NOT queued. Return 503 (not 200) so the
            # failure is visible in GitHub's "Recent Deliveries" and can be redelivered.
            logger.exception("Queue unavailable: repo=%s pr=#%d", repo_name, pr_number)
            raise HTTPException(status_code=503, detail="Task queue unavailable")

        return {"status": "queued", "repo": repo_name, "pr": pr_number, "action": action}

    logger.info("Ignored event: type=%s action=%s", x_github_event, action)
    return {"status": "ignored", "event": x_github_event, "action": action}