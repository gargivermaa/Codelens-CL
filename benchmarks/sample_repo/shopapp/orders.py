"""Order lifecycle."""
from shopapp.auth import require_role
from shopapp.billing import order_total
from shopapp.notifications import send_email
from shopapp.storage import Database

VALID_TRANSITIONS = {"pending": {"paid", "cancelled"}, "paid": {"shipped", "refunded"}, "shipped": set()}


def create_order(db: Database, user: dict, items: list[dict], coupon: str | None, country: str) -> int:
    if not items:
        raise ValueError("an order needs at least one item")
    total = order_total(items, coupon, country)
    db.execute("INSERT INTO orders (user_id, total_cents, status) VALUES (%s, %s, 'pending')",
               (user["id"], total))
    row = db.fetch_one("SELECT max(id) AS id FROM orders WHERE user_id = %s", (user["id"],))
    return row["id"]


def get_order(db: Database, user: dict, order_id: int) -> dict:
    order = db.fetch_one("SELECT * FROM orders WHERE id = %s", (order_id,))
    if order is None or (order["user_id"] != user["id"] and user.get("role") != "admin"):
        raise LookupError("order not found")
    return order


def change_status(db: Database, order: dict, new_status: str) -> None:
    if new_status not in VALID_TRANSITIONS.get(order["status"], set()):
        raise ValueError(f"cannot move order from {order['status']} to {new_status}")
    db.execute("UPDATE orders SET status = %s WHERE id = %s", (new_status, order["id"]))


def refund_order(db: Database, admin: dict, order: dict) -> None:
    require_role(admin, "support")
    change_status(db, order, "refunded")
    send_email(order["email"], "Your refund is on its way", f"Order {order['id']} was refunded.")
