"""Admin operations."""
import logging

from shopapp.auth import require_role
from shopapp.storage import Database

logger = logging.getLogger(__name__)


def list_users(db: Database, admin: dict) -> list[dict]:
    require_role(admin, "admin")
    return db.fetch_all("SELECT id, email, role FROM users ORDER BY id")


def set_user_role(db: Database, admin: dict, user_id: int, role: str) -> None:
    require_role(admin, "admin")
    if role not in ("customer", "support", "admin"):
        raise ValueError(f"unknown role {role}")
    db.execute("UPDATE users SET role = %s WHERE id = %s", (role, user_id))
    logger.info("admin %s set role of user %s to %s", admin["id"], user_id, role)


def delete_user(db: Database, admin: dict, user_id: int) -> None:
    require_role(admin, "admin")
    if user_id == admin["id"]:
        raise ValueError("admins cannot delete themselves")
    db.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
    db.execute("DELETE FROM users WHERE id = %s", (user_id,))
