"""HTTP request handlers (framework-agnostic: request is a dict, response is a dict)."""
import json
import urllib.request
from urllib.parse import urlparse

from shopapp.auth import get_user_for_session
from shopapp.orders import get_order
from shopapp.storage import Database

ALLOWED_WEBHOOK_HOSTS = {"hooks.partner.example"}


def current_user(db: Database, request: dict) -> dict:
    user = get_user_for_session(db, request.get("cookies", {}).get("session", ""))
    if user is None:
        raise PermissionError("login required")
    return user


def handle_get_order(db: Database, request: dict) -> dict:
    user = current_user(db, request)
    order = get_order(db, user, int(request["params"]["order_id"]))
    return {"status": 200, "body": order}


def handle_import_settings(request: dict) -> dict:
    settings = json.loads(request["body"])
    return {"status": 200, "body": {"imported": sorted(settings)}}


def notify_partner(url: str, payload: dict) -> int:
    host = urlparse(url).hostname
    if host not in ALLOWED_WEBHOOK_HOSTS:
        raise ValueError("webhook host not allowed")
    data = json.dumps(payload).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=5) as response:
        return response.status
