"""Dev tool: send a correctly signed pull_request webhook to the local API.

Usage (from project root):
    python scripts/send_test_webhook.py --pr 101 --action opened --title "Fix bug"
"""
import argparse
import hashlib
import hmac
import json

import requests
from dotenv import dotenv_values

parser = argparse.ArgumentParser()
parser.add_argument("--pr", type=int, default=101)
parser.add_argument("--action", default="opened", choices=["opened", "synchronize"])
parser.add_argument("--title", default="Test PR")
parser.add_argument("--url", default="http://localhost:8000/webhook")
args = parser.parse_args()

secret = dotenv_values(".env")["GITHUB_WEBHOOK_SECRET"]
payload = {
    "action": args.action,
    "installation": {"id": 12345},
    "repository": {"id": 1296269, "full_name": "octocat/hello-world"},
    "pull_request": {"number": args.pr, "title": args.title, "user": {"login": "dev"}},
}
body = json.dumps(payload).encode()
signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

resp = requests.post(
    args.url,
    data=body,
    headers={
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": signature,
    },
    timeout=15,
)
print(resp.status_code, resp.text)