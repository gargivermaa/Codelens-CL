"""Thin database wrapper. Always use parameterized queries."""


class Database:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
        self.connection.commit()

    def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
