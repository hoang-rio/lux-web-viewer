import ast
import asyncio
import json
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from time_utils import DATETIME_FORMAT, format_datetime, parse_datetime
from web_viewer import notifications
import migration

ROOT = Path(__file__).resolve().parent.parent


class FakeRequest:
    def __init__(self, query=None):
        self.query = query or {}


def run_handler(query):
    return asyncio.run(notifications.notification_history(FakeRequest(query)))


def make_connection():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE notification_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "notified_at TEXT, title TEXT, body TEXT, read INTEGER DEFAULT 0)"
    )
    rows = [
        ("2026-09-30 08:05:00", "Grid export", "Exporting above limit"),
        ("2026-09-30 14:23:05", "Battery full", "Battery reached 100%"),
        # Written before notified_at was normalized to "%Y-%m-%d %H:%M:%S".
        # Must still order chronologically and be filterable by date.
        ("2026-09-29T21:00:00.123456", "Off grid", "Grid disconnected"),
        ("2026-09-28 07:15:00", "High usage", "Consumption above average"),
    ]
    conn.executemany(
        "INSERT INTO notification_history (notified_at, title, body, read) VALUES (?, ?, ?, 0)",
        rows,
    )
    conn.commit()
    return conn


def payload_of(response):
    return json.loads(response.text)["notifications"]


class TestParseDateParam(unittest.TestCase):
    def test_accepts_iso_date(self):
        self.assertEqual(notifications._parse_date_param("2026-09-30"), "2026-09-30")

    def test_absent_and_empty_are_not_a_filter(self):
        self.assertIsNone(notifications._parse_date_param(None))
        self.assertIsNone(notifications._parse_date_param(""))

    def test_rejects_malformed_values(self):
        for raw in [
            "2026-9-3",
            "20260930",
            "2026/09/30",
            "2026-13-01",
            "2026-02-30",
            "2026-09-30T00:00",
            "abc",
            "'; DROP TABLE notification_history; --",
        ]:
            with self.subTest(raw=raw):
                self.assertIsNone(notifications._parse_date_param(raw))


class TestDatetimeFormatContract(unittest.TestCase):
    """Timestamp columns are ordered with plain string comparison in SQL, so every
    writer must go through the one shared formatter in time_utils."""

    WRITER_SOURCES = [
        "app.py",
        "database.py",
        "dongle_handler.py",
        "dongle_server.py",
        "fcm.py",
        "tuya_manager.py",
        "trigger_engine/actions.py",
        "trigger_engine/storage.py",
        "web_viewer/charts.py",
    ]

    def test_format_is_fixed_width_and_truncates_microseconds(self):
        moment = datetime(2026, 9, 30, 14, 23, 5, 123456)
        self.assertEqual(format_datetime(moment), "2026-09-30 14:23:05")

    def test_no_writer_bypasses_the_shared_formatter(self):
        for rel_path in self.WRITER_SOURCES:
            with self.subTest(source=rel_path):
                source = (ROOT / rel_path).read_text()
                self.assertNotIn(
                    "isoformat()",
                    source,
                    f"{rel_path} formats timestamps via isoformat() instead of "
                    f"time_utils.format_datetime()",
                )
                self.assertNotIn(
                    DATETIME_FORMAT,
                    source,
                    f"{rel_path} hardcodes the {DATETIME_FORMAT!r} literal instead of "
                    f"using time_utils, so it will drift when the format changes",
                )

    def test_openapi_documents_the_single_format(self):
        openapi = (ROOT / "openapi.yml").read_text()
        self.assertNotIn(
            "depending on the",
            openapi,
            "openapi.yml still documents notified_at as writer-dependent",
        )
        expected_pattern = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
        lines = openapi.splitlines()
        occurrences = 0
        for index, line in enumerate(lines):
            key = line.strip().removesuffix(":")
            if key not in (
                "notified_at",
                "triggered_at",
                "last_triggered_at",
                "created_at",
            ):
                continue
            occurrences += 1
            indent = len(line) - len(line.lstrip())
            # Every documented timestamp property must pin the pattern on the
            # following lines, within the same mapping block.
            block = "\n".join(lines[index + 1:index + 12])
            with self.subTest(column=key, line=index + 1):
                self.assertIn(f"pattern: '{expected_pattern}'", block)
        self.assertEqual(
            occurrences,
            5,
            "expected notified_at, triggered_at, last_triggered_at and both "
            "created_at properties to be documented",
        )

    def test_format_sorts_lexicographically_in_chronological_order(self):
        moments = sorted([
            datetime(2026, 9, 30, 6, 0, 0),
            datetime(2026, 9, 30, 22, 0, 0),
            datetime(2026, 9, 29, 23, 59, 59),
            datetime(2026, 9, 30, 14, 23, 5),
        ])
        written = [format_datetime(m) for m in moments]
        self.assertEqual(sorted(written), written)

    def test_parser_round_trips_and_tolerates_legacy_rows(self):
        moment = datetime(2026, 9, 30, 14, 23, 5)
        self.assertEqual(parse_datetime(format_datetime(moment)), moment)
        # Rows written before the format was normalized must stay readable.
        legacy = "2026-09-30T14:23:05.123456"
        self.assertEqual(
            parse_datetime(legacy), datetime(2026, 9, 30, 14, 23, 5, 123456)
        )
        self.assertIsNone(parse_datetime(None))
        self.assertIsNone(parse_datetime(""))
        self.assertIsNone(parse_datetime("not-a-date"))

    def test_every_timestamp_column_has_a_normalizing_migration(self):
        migration_sql = " ".join(migration.MIGRATIONS_SQL)
        # (table, column) pairs, so a column name shared by two tables is checked
        # against both and a migration for only one of them is caught.
        for table, column in (
            ("notification_history", "notified_at"),
            ("trigger_history", "triggered_at"),
            ("automation_triggers", "last_triggered_at"),
            ("automation_triggers", "created_at"),
            ("tuya_devices", "created_at"),
        ):
            with self.subTest(table=table, column=column):
                self.assertIn(
                    f"UPDATE {table} SET {column} = "
                    f"replace(substr({column}, 1, 19), 'T', ' ')",
                    migration_sql,
                )


    def test_migration_is_not_gated_on_run_web_viewer(self):
        """handle_grid_status writes notification_history in every mode, so the
        schema and timestamp normalization must not wait for RUN_WEB_VIEWER."""
        tree = ast.parse((ROOT / "app.py").read_text())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "run_migration"
        ]
        self.assertTrue(calls, "app.py no longer calls run_migration")

        for branch in ast.walk(tree):
            if not isinstance(branch, ast.If):
                continue
            conditions = {
                node.id for node in ast.walk(branch.test) if isinstance(node, ast.Name)
            }
            if "run_web_view" not in conditions:
                continue
            for stmt in branch.body:
                for call in calls:
                    if call in ast.walk(stmt):
                        with self.subTest(line=call.lineno):
                            self.fail(
                                f"run_migration at app.py:{call.lineno} is nested "
                                f"inside a run_web_view branch, so the database is "
                                f"left unnormalized when RUN_WEB_VIEWER is off"
                            )


class TestNotificationHistory(unittest.TestCase):
    def setUp(self):
        self.conn = make_connection()
        self.patches = [
            mock.patch.object(
                notifications, "get_db_connection", return_value=self.conn
            ),
            mock.patch.object(notifications.config, "logger", mock.Mock()),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_returns_all_notifications_without_date(self):
        result = run_handler({})
        self.assertEqual(len(payload_of(result)), 4)
        # Newest first
        self.assertEqual(payload_of(result)[0]["title"], "Battery full")

    def test_filters_by_date_across_both_timestamp_formats(self):
        result = run_handler({"date": "2026-09-30"})
        titles = [row["title"] for row in payload_of(result)]
        self.assertEqual(titles, ["Battery full", "Grid export"])

    def test_filters_by_date_for_iso_timestamp_rows(self):
        result = run_handler({"date": "2026-09-29"})
        rows = payload_of(result)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Off grid")
        self.assertEqual(rows[0]["notified_at"], "2026-09-29T21:00:00.123456")

    def test_date_with_no_rows_returns_empty_list(self):
        result = run_handler({"date": "2020-01-01"})
        self.assertEqual(payload_of(result), [])

    def test_malformed_date_returns_empty_list_without_touching_db(self):
        with mock.patch.object(
            notifications, "get_db_connection", side_effect=AssertionError("db hit")
        ):
            result = run_handler({"date": "2026-13-45"})
        self.assertEqual(payload_of(result), [])
        notifications.config.logger.warning.assert_called_once()


class TestRunMigrationResume(unittest.TestCase):
    """run_migration must rebuild from any incomplete state, not silently no-op."""

    TOTAL = len(migration.MIGRATIONS_SQL)
    TABLES = {
        "hourly_chart",
        "daily_chart",
        "notification_history",
        "settings",
        "tuya_devices",
        "automation_triggers",
        "trigger_history",
    }

    def migrate(self, conn):
        with mock.patch.object(migration, "logger", mock.Mock()):
            migration.run_migration(conn)
        return conn

    def assertFullyMigrated(self, conn):
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertEqual(self.TABLES - tables, set(), "tables were never created")
        for table, column in [
            ("daily_chart", "updated"),
            ("notification_history", "read"),
            ("trigger_history", "actions_detail"),
        ]:
            columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            self.assertIn(column, columns, f"{table}.{column} missing")
        count, highest = conn.execute(
            "SELECT COUNT(*), MAX(id) FROM migration"
        ).fetchone()
        self.assertEqual((count, highest), (self.TOTAL, self.TOTAL))

    def test_empty_database(self):
        self.assertFullyMigrated(self.migrate(sqlite3.connect(":memory:")))

    def test_migration_table_exists_but_is_empty(self):
        """DDL auto-commits, so a crash before the first tracking INSERT leaves
        this state; it must rebuild the schema, not no-op forever."""
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE migration (id INTEGER PRIMARY KEY, applied_at TEXT)")
        conn.commit()
        self.assertFullyMigrated(self.migrate(conn))

    def test_empty_migration_table_still_normalizes_legacy_rows(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE migration (id INTEGER PRIMARY KEY, applied_at TEXT)")
        conn.execute(
            "CREATE TABLE notification_history (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " notified_at TEXT, title TEXT, body TEXT)"
        )
        conn.execute(
            "INSERT INTO notification_history (notified_at, title, body)"
            " VALUES ('2026-10-01T10:00:00.123456', 't', 'b')"
        )
        conn.commit()
        self.migrate(conn)
        self.assertEqual(
            conn.execute("SELECT notified_at FROM notification_history").fetchone()[0],
            "2026-10-01 10:00:00",
        )

    def test_fully_migrated_is_idempotent(self):
        conn = self.migrate(sqlite3.connect(":memory:"))
        self.migrate(conn)
        self.migrate(conn)
        self.assertFullyMigrated(conn)

    def test_partially_migrated_resumes_from_missing_rows(self):
        conn = self.migrate(sqlite3.connect(":memory:"))
        conn.execute("DELETE FROM migration WHERE id > 4")
        conn.commit()
        self.assertFullyMigrated(self.migrate(conn))

    def test_legacy_deployment_normalizes_existing_iso_data(self):
        conn = sqlite3.connect(":memory:")
        for index, sql in enumerate(migration.MIGRATIONS_SQL[:12], start=1):
            conn.execute(sql)
            conn.execute("INSERT INTO migration (id, applied_at) VALUES (?, 0)", (index,))
        conn.execute(
            "INSERT INTO trigger_history (trigger_id, triggered_at)"
            " VALUES (1, '2026-10-01T09:30:00.500000')"
        )
        conn.execute(
            "INSERT INTO tuya_devices (id, name, ip, local_key, created_at)"
            " VALUES ('d', 'n', 'i', 'k', '2026-10-01T09:00:00.1')"
        )
        conn.commit()
        self.assertFullyMigrated(self.migrate(conn))
        for table, column, expected in [
            ("trigger_history", "triggered_at", datetime(2026, 10, 1, 9, 30)),
            ("tuya_devices", "created_at", datetime(2026, 10, 1, 9)),
        ]:
            value = conn.execute(f"SELECT {column} FROM {table}").fetchone()[0]
            self.assertNotIn("T", value, f"{table}.{column} left as ISO: {value}")
            self.assertEqual(datetime.strptime(value, DATETIME_FORMAT), expected)


if __name__ == "__main__":
    unittest.main()
