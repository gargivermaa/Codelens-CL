"""
POST /webhook — receives GitHub webhook events.

Day 1-2 scope (locked):
  - Verify the request is genuinely from GitHub (HMAC-SHA256 signature check)
  - Parse the payload
  - Log which PR event was received
  - Return immediately

NOT in scope yet:
  - Celery dispatch (Day 5-6)
  - PostgreSQL writes (Day 3-4)
  - Chunking/retrieval/review (Day 7+)
These are added by editing this file on their locked days — the signature
verification and routing logic below does not change when that happens.
"""
import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request

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
        pr_number = payload["pull_request"]["number"]
        repo_name = payload["repository"]["full_name"]
        logger.info("Received PR event: repo=%s pr=#%d action=%s", repo_name, pr_number, action)

        # Day 5-6: replace the line below with
        #   process_pr_review.delay(repo_name, pr_number, installation_id)
        return {"status": "received", "repo": repo_name, "pr": pr_number, "action": action}

    logger.info("Ignored event: type=%s action=%s", x_github_event, action)
    return {"status": "ignored", "event": x_github_event, "action": action}