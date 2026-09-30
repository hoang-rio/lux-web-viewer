import asyncio
import json
import sqlite3
import unittest
from unittest import mock

from web_viewer import notifications


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
        # fcm.py writes "%Y-%m-%d %H:%M:%S"
        ("2026-09-30 08:05:00", "Grid export", "Exporting above limit"),
        ("2026-09-30 14:23:05", "Battery full", "Battery reached 100%"),
        # trigger_engine/actions.py writes datetime.now().isoformat()
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


if __name__ == "__main__":
    unittest.main()
