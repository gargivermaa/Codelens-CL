"""
GitHub REST API client: pull requests, files, repository archives, reviews.

Error model (drives Celery retry decisions):
  GitHubRateLimitError  -> retry after `retry_after` seconds
  GitHubTransientError  -> 5xx / network errors, retry with backoff
  GitHubError           -> other 4xx, not retryable
"""
import base64
import io
import logging
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx

from app.config import settings
from app.github.auth import TokenProvider

logger = logging.getLogger("codelens.github")

MAX_PR_FILES = 3000              # GitHub's own limit for the files endpoint
MAX_ARCHIVE_BYTES = 200_000_000  # refuse absurdly large repository archives
MAX_EXTRACTED_FILE_BYTES = 1_000_000


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class GitHubTransientError(GitHubError):
    pass


class GitHubRateLimitError(GitHubTransientError):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message, 429)
        self.retry_after = retry_after


@dataclass
class InlineComment:
    path: str
    line: int
    body: str
    start_line: int | None = None

    def to_api(self) -> dict:
        data = {"path": self.path, "line": self.line, "side": "RIGHT", "body": self.body}
        if self.start_line and self.start_line < self.line:
            data["start_line"] = self.start_line
            data["start_side"] = "RIGHT"
        return data


@dataclass
class PostedReview:
    review_id: int
    inline_posted: int
    inline_dropped: int


class GitHubClient:
    def __init__(self, token: str | None = None, api_url: str | None = None,
                 http: httpx.Client | None = None):
        self.api_url = (api_url or settings.GITHUB_API_URL).rstrip("/")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "codelens-ci"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = http or httpx.Client(timeout=60, follow_redirects=True)
        self.http.headers.update(headers)

    @classmethod
    def for_installation(cls, installation_id: int | None,
                         provider: TokenProvider | None = None) -> "GitHubClient":
        provider = provider or TokenProvider.from_settings()
        return cls(token=provider.token_for(installation_id))

    # ---- transport --------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.api_url}{path}"
        try:
            response = self.http.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            raise GitHubTransientError(f"GitHub request failed: {exc.__class__.__name__}") from exc

        if response.status_code < 400:
            return response
        message = _error_message(response)
        remaining = response.headers.get("x-ratelimit-remaining")
        if response.status_code == 429 or (response.status_code == 403 and
                                           (remaining == "0" or "rate limit" in message.lower())):
            retry_after = response.headers.get("retry-after")
            reset = response.headers.get("x-ratelimit-reset")
            if retry_after and retry_after.isdigit():
                wait = int(retry_after)
            elif reset and reset.isdigit():
                wait = max(1, int(reset) - int(time.time()))
            else:
                wait = 60
            raise GitHubRateLimitError(f"GitHub rate limit: {message}", retry_after=min(wait, 3600))
        if response.status_code >= 500:
            raise GitHubTransientError(f"GitHub {response.status_code}: {message}", response.status_code)
        raise GitHubError(f"GitHub {response.status_code} on {method} {path}: {message}",
                          response.status_code)

    def _paginate(self, path: str, limit: int) -> list[dict]:
        items: list[dict] = []
        url: str | None = path
        params: dict | None = {"per_page": 100}
        while url and len(items) < limit:
            response = self._request("GET", url, params=params)
            items.extend(response.json())
            url = response.links.get("next", {}).get("url")
            params = None  # the "next" link already carries the query string
        return items[:limit]

    # ---- pull requests ----------------------------------------------------
    def get_pull(self, repo: str, number: int) -> dict:
        return self._request("GET", f"/repos/{repo}/pulls/{number}").json()

    def list_pull_files(self, repo: str, number: int) -> list[dict]:
        return self._paginate(f"/repos/{repo}/pulls/{number}/files", MAX_PR_FILES)

    def get_file_content(self, repo: str, path: str, ref: str) -> str | None:
        """Return a text file's content at `ref`, or None if missing/binary/too large."""
        try:
            data = self._request("GET", f"/repos/{repo}/contents/{path}", params={"ref": ref}).json()
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        if not isinstance(data, dict) or data.get("type") != "file":
            return None
        if data.get("encoding") == "base64" and data.get("content"):
            raw = base64.b64decode(data["content"])
        elif data.get("download_url") and (data.get("size") or 0) <= MAX_EXTRACTED_FILE_BYTES:
            raw = self._request("GET", data["download_url"]).content
        else:
            return None
        if b"\0" in raw[:8000]:
            return None
        return raw.decode("utf-8", errors="replace")

    # ---- repository archive (for indexing) --------------------------------
    def download_python_sources(self, repo: str, ref: str, dest: Path) -> int:
        """Extract the repository's .py files at `ref` into `dest`. Returns file count.

        The archive is untrusted: only regular files with safe relative paths are
        written (no absolute paths, '..', links or devices), and nothing is executed.
        """
        response = self._request("GET", f"/repos/{repo}/tarball/{ref}")
        if len(response.content) > MAX_ARCHIVE_BYTES:
            raise GitHubError(f"Repository archive larger than {MAX_ARCHIVE_BYTES} bytes")
        return extract_python_sources(response.content, dest)

    # ---- reviews ----------------------------------------------------------
    def create_review(self, repo: str, number: int, commit_id: str, body: str,
                      comments: list[InlineComment]) -> PostedReview:
        """Post one review (event=COMMENT). If GitHub rejects inline positions
        (422, e.g. the diff moved on), post the summary alone and list the
        dropped comments in the body instead of losing them."""
        payload = {"commit_id": commit_id, "body": body, "event": "COMMENT",
                   "comments": [c.to_api() for c in comments]}
        try:
            data = self._request("POST", f"/repos/{repo}/pulls/{number}/reviews", json=payload).json()
            return PostedReview(int(data["id"]), len(comments), 0)
        except GitHubError as exc:
            if exc.status != 422 or not comments:
                raise
            logger.warning("GitHub rejected inline comment positions (%s); posting summary only", exc)
        fallback = body + "\n\n<details><summary>Inline comments that could not be placed "\
            "on the current diff</summary>\n\n" + "\n\n".join(
                f"**`{c.path}:{c.line}`**\n\n{c.body}" for c in comments) + "\n</details>"
        data = self._request("POST", f"/repos/{repo}/pulls/{number}/reviews",
                             json={"commit_id": commit_id, "body": fallback, "event": "COMMENT"}).json()
        return PostedReview(int(data["id"]), 0, len(comments))


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("message", ""))[:300]
    except ValueError:
        return response.text[:300]


def extract_python_sources(archive: bytes, dest: Path) -> int:
    dest = Path(dest).resolve()
    count = 0
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".py"):
                continue  # skips dirs, symlinks, hardlinks, devices, non-Python files
            parts = PurePosixPath(member.name).parts
            if len(parts) < 2:
                continue
            relative = PurePosixPath(*parts[1:])   # strip "<owner>-<repo>-<sha>/"
            if relative.is_absolute() or ".." in relative.parts:
                logger.warning("Skipping unsafe archive path %r", member.name)
                continue
            if member.size > MAX_EXTRACTED_FILE_BYTES:
                continue
            target = (dest / relative).resolve()
            if dest not in target.parents:
                continue
            source = tar.extractfile(member)
            if source is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
            count += 1
    return count
