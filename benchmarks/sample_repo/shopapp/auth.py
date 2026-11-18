"""Authentication: password hashing, sessions and permission checks."""
import hashlib
import hmac
import secrets

from shopapp.storage import Database

SESSION_TTL_SECONDS = 3600


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)


def verify_password(password: str, salt: bytes, expected: bytes) -> bool:
    return hmac.compare_digest(hash_password(password, salt), expected)


def create_session(db: Database, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO sessions (token, user_id) VALUES (%s, %s)", (token, user_id))
    return token


def get_user_for_session(db: Database, token: str) -> dict | None:
    row = db.fetch_one("SELECT user_id FROM sessions WHERE token = %s", (token,))
    if row is None:
        return None
    return db.fetch_one("SELECT id, email, role FROM users WHERE id = %s", (row["user_id"],))


def require_role(user: dict | None, role: str) -> None:
    if user is None or user.get("role") not in (role, "admin"):
        raise PermissionError(f"{role} role required")
