"""Stock levels and reservations."""
from shopapp.storage import Database

LOW_STOCK_THRESHOLD = 5


def get_stock(db: Database, product_id: int) -> int:
    row = db.fetch_one("SELECT quantity FROM stock WHERE product_id = %s", (product_id,))
    return row["quantity"] if row else 0


def reserve_items(db: Database, items: list[dict]) -> None:
    for item in items:
        available = get_stock(db, item["product_id"])
        if item["quantity"] > available:
            raise ValueError(f"only {available} left of product {item['product_id']}")
    for item in items:
        db.execute("UPDATE stock SET quantity = quantity - %s WHERE product_id = %s",
                   (item["quantity"], item["product_id"]))


def low_stock_products(db: Database) -> list[dict]:
    return db.fetch_all("SELECT product_id, quantity FROM stock WHERE quantity < %s",
                        (LOW_STOCK_THRESHOLD,))


def paginate(rows: list, page: int, page_size: int = 20) -> list:
    if page < 1:
        raise ValueError("page starts at 1")
    start = (page - 1) * page_size
    return rows[start:start + page_size]
