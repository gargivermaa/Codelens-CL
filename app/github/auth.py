"""
GitHub authentication.

GitHub App flow (production):
  1. Sign a short-lived JWT (RS256) with the App's private key; iss = App ID.
  2. Exchange it for an installation access token:
       POST /app/installations/{installation_id}/access_tokens
  3. Use the installation token (valid 1 hour) for repository API calls.
Tokens are cached per installation and refreshed 5 minutes before expiry.

Fallback (local demo): a personal access token in GITHUB_TOKEN.
Without either, requests are anonymous (public repositories, read only).

Tokens and keys are never logged.
"""
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx
import jwt

from app.config import settings

logger = logging.getLogger("codelens.github.auth")

_REFRESH_MARGIN_S = 300


class GitHubAuthError(RuntimeError):
    pass


def create_app_jwt(app_id: str, private_key_pem: str, now: float | None = None) -> str:
    now = int(now if now is not None else time.time())
    payload = {
        "iat": now - 60,        # allow for clock drift
        "exp": now + 9 * 60,    # GitHub's maximum is 10 minutes
        "iss": str(app_id),
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


class TokenProvider:
    """Returns a bearer token for an installation (or None for anonymous access)."""

    def __init__(self, app_id: str | None = None, private_key_pem: str | None = None,
                 fallback_token: str | None = None, api_url: str | None = None,
                 http: httpx.Client | None = None):
        self.app_id = app_id
        self.private_key_pem = private_key_pem
        self.fallback_token = fallback_token or None
        self.api_url = (api_url or settings.GITHUB_API_URL).rstrip("/")
        self.http = http or httpx.Client(timeout=30)
        self._cache: dict[int, tuple[str, float]] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls) -> "TokenProvider":
        private_key = None
        if settings.GITHUB_APP_ID:
            path = Path(settings.GITHUB_PRIVATE_KEY_PATH)
            if not path.is_file():
                raise GitHubAuthError(f"GITHUB_APP_ID is set but the private key file "
                                      f"{settings.GITHUB_PRIVATE_KEY_PATH} does not exist")
            private_key = path.read_text()
        return cls(settings.GITHUB_APP_ID or None, private_key, settings.GITHUB_TOKEN or None)

    @property
    def app_configured(self) -> bool:
        return bool(self.app_id and self.private_key_pem)

    def token_for(self, installation_id: int | None) -> str | None:
        if self.app_configured and installation_id:
            return self._installation_token(int(installation_id))
        return self.fallback_token

    def _installation_token(self, installation_id: int) -> str:
        with self._lock:
            cached = self._cache.get(installation_id)
            if cached and cached[1] - _REFRESH_MARGIN_S > time.time():
                return cached[0]
            app_jwt = create_app_jwt(self.app_id, self.private_key_pem)
            response = self.http.post(
                f"{self.api_url}/app/installations/{installation_id}/access_tokens",
                headers={"Authorization": f"Bearer {app_jwt}",
                         "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"},
            )
            if response.status_code != 201:
                raise GitHubAuthError(f"Could not create installation token for installation "
                                      f"{installation_id}: HTTP {response.status_code}")
            data = response.json()
            expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).timestamp()
            self._cache[installation_id] = (data["token"], expires_at)
            logger.info("Obtained installation token for installation %s", installation_id)
            return data["token"]
