"""Admin reports."""
import csv
import io

from shopapp.auth import require_role
from shopapp.storage import Database


def sales_by_country(db: Database, admin: dict) -> list[dict]:
    require_role(admin, "admin")
    return db.fetch_all("SELECT country, sum(total_cents) AS cents FROM orders GROUP BY country")


def export_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return buffer.getvalue()
