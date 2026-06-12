import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shared.mysql_data import (
    MySqlClient,
    MySqlConfigurationError,
    translate_named_parameters,
)


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, values):
        self.executed = (sql, values)
        return len(self.rows)

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows=None):
        self.cursor_instance = FakeCursor(rows)
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class MySqlDataClientTest(unittest.TestCase):
    def test_translates_rds_data_named_parameters_to_mysql_placeholders(self):
        sql, values = translate_named_parameters(
            "SELECT * FROM users WHERE id = :user_id AND status = :status LIMIT :limit",
            {"user_id": "user-1", "status": "active", "limit": 1},
        )

        self.assertEqual(sql, "SELECT * FROM users WHERE id = %s AND status = %s LIMIT %s")
        self.assertEqual(values, ["user-1", "active", 1])

    def test_reuses_duplicate_named_parameters_in_value_order(self):
        sql, values = translate_named_parameters(
            "SELECT * FROM users WHERE id = :user_id OR owner_id = :user_id",
            {"user_id": "user-1"},
        )

        self.assertEqual(sql, "SELECT * FROM users WHERE id = %s OR owner_id = %s")
        self.assertEqual(values, ["user-1", "user-1"])

    def test_raises_when_a_sql_parameter_is_missing(self):
        with self.assertRaises(MySqlConfigurationError):
            translate_named_parameters("SELECT * FROM users WHERE id = :user_id", {})

    def test_fetch_all_uses_secret_credentials_and_returns_dict_rows(self):
        connection = FakeConnection(rows=[{"id": "user-1", "display_name": "Lovv User"}])
        client = MySqlClient(
            host="lovv-dev-mysql.example.com",
            database="lovvdev",
            secret_arn="secret-arn",
            secret_loader=lambda secret_arn: json.dumps({"username": "lovvadmin", "password": "secret"}),
            connection_factory=lambda **kwargs: connection,
        )

        rows = client.fetch_all("SELECT id, display_name FROM users WHERE id = :user_id", {"user_id": "user-1"})

        self.assertEqual(rows, [{"id": "user-1", "display_name": "Lovv User"}])
        self.assertEqual(
            connection.cursor_instance.executed,
            ("SELECT id, display_name FROM users WHERE id = %s", ["user-1"]),
        )
        self.assertTrue(connection.closed)

    def test_execute_commits_writes(self):
        connection = FakeConnection()
        client = MySqlClient(
            host="lovv-dev-mysql.example.com",
            database="lovvdev",
            username="lovvadmin",
            password="secret",
            connection_factory=lambda **kwargs: connection,
        )

        client.execute(
            "UPDATE users SET display_name = :display_name WHERE id = :user_id",
            {"display_name": "Lovv User", "user_id": "user-1"},
            include_result_metadata=False,
        )

        self.assertTrue(connection.committed)
        self.assertEqual(
            connection.cursor_instance.executed,
            ("UPDATE users SET display_name = %s WHERE id = %s", ["Lovv User", "user-1"]),
        )

    def test_execute_converts_api_utc_iso_strings_to_mysql_datetime_strings(self):
        connection = FakeConnection()
        client = MySqlClient(
            host="lovv-dev-mysql.example.com",
            database="lovvdev",
            username="lovvadmin",
            password="secret",
            connection_factory=lambda **kwargs: connection,
        )

        client.execute(
            "UPDATE users SET updated_at = :updated_at WHERE id = :user_id",
            {"updated_at": "2026-06-10T09:00:00Z", "user_id": "user-1"},
            include_result_metadata=False,
        )

        self.assertEqual(
            connection.cursor_instance.executed,
            ("UPDATE users SET updated_at = %s WHERE id = %s", ["2026-06-10 09:00:00", "user-1"]),
        )

    def test_fetch_all_converts_mysql_datetime_values_to_api_utc_iso_strings(self):
        connection = FakeConnection(rows=[{"id": "user-1", "created_at": datetime(2026, 6, 10, 9, 0, 0)}])
        client = MySqlClient(
            host="lovv-dev-mysql.example.com",
            database="lovvdev",
            username="lovvadmin",
            password="secret",
            connection_factory=lambda **kwargs: connection,
        )

        rows = client.fetch_all("SELECT id, created_at FROM users")

        self.assertEqual(rows, [{"id": "user-1", "created_at": "2026-06-10T09:00:00Z"}])


if __name__ == "__main__":
    unittest.main()
