"""GitHub auth/client tests: real httpx client against a MockTransport (no network)."""
import io
import json
import tarfile
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.github.auth import GitHubAuthError, TokenProvider, create_app_jwt
from app.github.client import (GitHubClient, GitHubError, GitHubRateLimitError, GitHubTransientError,
                               InlineComment, extract_python_sources)


@pytest.fixture(scope="module")
def rsa_keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return private, public


def _client(handler, token="tok"):
    return GitHubClient(token=token, api_url="https://api.github.test",
                        http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_app_jwt_is_rs256_signed_with_short_expiry(rsa_keys):
    private, public = rsa_keys
    token = create_app_jwt("12345", private, now=1_700_000_000)
    claims = jwt.decode(token, public, algorithms=["RS256"], options={"verify_exp": False})
    assert claims == {"iss": "12345", "iat": 1_700_000_000 - 60, "exp": 1_700_000_000 + 540}


def test_installation_token_is_fetched_once_and_cached(rsa_keys):
    private, public = rsa_keys
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/app/installations/77/access_tokens"
        jwt.decode(request.headers["Authorization"].split()[1], public, algorithms=["RS256"])
        expiry = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
        return httpx.Response(201, json={"token": "ghs_installation", "expires_at": expiry})

    provider = TokenProvider("12345", private, api_url="https://api.github.test",
                             http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert provider.token_for(77) == "ghs_installation"
    assert provider.token_for(77) == "ghs_installation"
    assert len(calls) == 1


def test_expiring_token_is_refreshed(rsa_keys):
    private, _ = rsa_keys
    calls = []

    def handler(request):
        calls.append(1)
        expiry = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 120))  # < refresh margin
        return httpx.Response(201, json={"token": f"t{len(calls)}", "expires_at": expiry})

    provider = TokenProvider("1", private, api_url="https://api.github.test",
                             http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert provider.token_for(5) == "t1" and provider.token_for(5) == "t2"


def test_installation_token_failure_is_reported(rsa_keys):
    provider = TokenProvider("1", rsa_keys[0], api_url="https://api.github.test",
                             http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    with pytest.raises(GitHubAuthError, match="installation 9"):
        provider.token_for(9)


def test_fallback_token_and_anonymous_access():
    assert TokenProvider(fallback_token="pat").token_for(123) == "pat"
    assert TokenProvider().token_for(123) is None


def test_auth_header_and_pagination():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer tok"
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[{"filename": "c.py"}])
        return httpx.Response(200, json=[{"filename": "a.py"}, {"filename": "b.py"}], headers={
            "Link": '<https://api.github.test/repos/o/r/pulls/1/files?per_page=100&page=2>; rel="next"'})

    files = _client(handler).list_pull_files("o/r", 1)
    assert [f["filename"] for f in files] == ["a.py", "b.py", "c.py"]


@pytest.mark.parametrize("response, error", [
    (httpx.Response(403, json={"message": "API rate limit exceeded"},
                    headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(time.time()) + 30)}),
     GitHubRateLimitError),
    (httpx.Response(429, json={"message": "secondary rate limit"}, headers={"retry-after": "7"}),
     GitHubRateLimitError),
    (httpx.Response(502, text="bad gateway"), GitHubTransientError),
    (httpx.Response(404, json={"message": "Not Found"}), GitHubError),
])
def test_errors_are_classified_for_retry_decisions(response, error):
    with pytest.raises(error) as info:
        _client(lambda r: response).get_pull("o/r", 1)
    if error is GitHubRateLimitError:
        assert 1 <= info.value.retry_after <= 60
    if error is GitHubError:
        assert not isinstance(info.value, GitHubTransientError)


def test_get_file_content_decodes_and_handles_missing_and_binary():
    import base64

    def handler(request):
        path = request.url.path
        if path.endswith("missing.py"):
            return httpx.Response(404, json={"message": "Not Found"})
        content = b"\x00\x01binary" if path.endswith("img.py") else b"x = 1\n"
        return httpx.Response(200, json={"type": "file", "encoding": "base64",
                                         "content": base64.b64encode(content).decode()})

    client = _client(handler)
    assert client.get_file_content("o/r", "a.py", "sha") == "x = 1\n"
    assert client.get_file_content("o/r", "missing.py", "sha") is None
    assert client.get_file_content("o/r", "img.py", "sha") is None


def test_create_review_posts_inline_comments_on_the_right_side():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"id": 555})

    posted = _client(handler).create_review("o/r", 3, "abc", "summary",
                                            [InlineComment("a.py", 10, "bug", start_line=8)])
    assert posted.review_id == 555 and posted.inline_posted == 1
    assert seen[0]["event"] == "COMMENT" and seen[0]["commit_id"] == "abc"
    assert seen[0]["comments"] == [{"path": "a.py", "line": 10, "side": "RIGHT", "body": "bug",
                                    "start_line": 8, "start_side": "RIGHT"}]


def test_invalid_inline_positions_fall_back_to_summary_without_losing_comments():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if body.get("comments"):
            return httpx.Response(422, json={"message": "Unprocessable Entity: line must be part of the diff"})
        return httpx.Response(200, json={"id": 9})

    posted = _client(handler).create_review("o/r", 3, "abc", "summary", [InlineComment("a.py", 99, "issue X")])
    assert posted.inline_posted == 0 and posted.inline_dropped == 1
    assert "issue X" in bodies[-1]["body"] and "a.py:99" in bodies[-1]["body"]


def _tar(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            else:
                info.type = tarfile.SYMTYPE
                info.linkname = data
                tar.addfile(info)
    return buffer.getvalue()


def test_archive_extraction_keeps_only_safe_python_files(tmp_path):
    archive = _tar([
        ("o-r-sha/app/main.py", b"def main(): pass\n", "file"),
        ("o-r-sha/README.md", b"# hi", "file"),
        ("o-r-sha/../../evil.py", b"import os", "file"),
        ("o-r-sha/link.py", "/etc/passwd", "link"),
    ])
    count = extract_python_sources(archive, tmp_path / "repo")
    assert count == 1
    assert (tmp_path / "repo/app/main.py").read_text() == "def main(): pass\n"
    assert not (tmp_path / "evil.py").exists() and not (tmp_path / "repo/link.py").exists()
