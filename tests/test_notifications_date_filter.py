import json
import sqlite3
import unittest
import uuid
from datetime import datetime
from unittest import mock

import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from yarl import URL

from multi_tenant import repository as mt_repo
from multi_tenant.models import NotificationHistory
from web_viewer import notifications, security


USER_ID = uuid.uuid4()
INVERTER_A = uuid.uuid4()
INVERTER_B = uuid.uuid4()


class FakeRequest:
    def __init__(self, query=None, authorization="Bearer test-token"):
        self.rel_url = URL("/notification-history").with_query(query or {})
        self.headers = {"Authorization": authorization}


def make_repository_session():
    engine = create_engine("sqlite://")
    # Only the notification table is needed; SQLite does not enforce the
    # user/inverter foreign keys by default.
    NotificationHistory.__table__.create(engine)
    return sessionmaker(bind=engine)(), engine


def seed(session):
    rows = [
        (USER_ID, INVERTER_A, datetime(2026, 9, 30, 8, 5), "Grid export", "Above limit"),
        (USER_ID, INVERTER_A, datetime(2026, 9, 30, 14, 23, 5), "Battery full", "Reached 100%"),
        (USER_ID, INVERTER_B, datetime(2026, 9, 30, 18, 0), "Other inverter", "Not mine"),
        (USER_ID, INVERTER_A, datetime(2026, 9, 29, 21, 0), "Off grid", "Disconnected"),
        (USER_ID, None, datetime(2026, 9, 28, 7, 15), "Unscoped", "No inverter"),
    ]
    session.add_all(
        [
            NotificationHistory(
                user_id=uid,
                inverter_id=inv,
                notified_at=when,
                title=title,
                body=body,
                read=False,
            )
            for uid, inv, when, title, body in rows
        ]
    )
    session.commit()


def make_sqlite_connection():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE notification_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "notified_at TEXT, title TEXT, body TEXT, read INTEGER DEFAULT 0)"
    )
    conn.executemany(
        "INSERT INTO notification_history (notified_at, title, body, read) VALUES (?, ?, ?, 0)",
        [
            ("2026-09-30 08:05:00", "Grid export", "Above limit"),
            ("2026-09-30 14:23:05", "Battery full", "Reached 100%"),
            ("2026-09-29T21:00:00.123456", "Off grid", "Disconnected"),
        ],
    )
    conn.commit()
    return conn


def titles(result):
    return [row.title for row in result]


class TestParseDateParam(unittest.TestCase):
    def test_accepts_iso_date(self):
        self.assertEqual(
            notifications._parse_date_param("2026-09-30").isoformat(), "2026-09-30"
        )

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


class TestGetNotificationHistoryRepository(unittest.TestCase):
    def setUp(self):
        self.session, engine = make_repository_session()
        self.addCleanup(engine.dispose)
        self.addCleanup(self.session.close)
        seed(self.session)

    def test_no_filters_returns_all_for_user_newest_first(self):
        result = mt_repo.get_notification_history(self.session, USER_ID)
        self.assertEqual(
            titles(result),
            [
                "Other inverter",
                "Battery full",
                "Grid export",
                "Off grid",
                "Unscoped",
            ],
        )

    def test_day_filter_uses_a_half_open_range(self):
        result = mt_repo.get_notification_history(
            self.session, USER_ID, day=datetime(2026, 9, 30).date()
        )
        self.assertEqual(titles(result), ["Other inverter", "Battery full", "Grid export"])

    def test_day_filter_matches_only_the_requested_day(self):
        result = mt_repo.get_notification_history(
            self.session, USER_ID, day=datetime(2026, 9, 29).date()
        )
        self.assertEqual(titles(result), ["Off grid"])

    def test_inverter_filter_scopes_results(self):
        result = mt_repo.get_notification_history(
            self.session, USER_ID, inverter_id=INVERTER_B
        )
        self.assertEqual(titles(result), ["Other inverter"])

    def test_day_and_inverter_filters_combine(self):
        result = mt_repo.get_notification_history(
            self.session,
            USER_ID,
            day=datetime(2026, 9, 30).date(),
            inverter_id=INVERTER_A,
        )
        self.assertEqual(titles(result), ["Battery full", "Grid export"])

    def test_other_users_rows_are_never_returned(self):
        other = uuid.uuid4()
        self.session.add(
            NotificationHistory(
                user_id=other,
                inverter_id=INVERTER_A,
                notified_at=datetime(2026, 9, 30, 9, 0),
                title="Other user",
                body="secret",
                read=False,
            )
        )
        self.session.commit()
        result = mt_repo.get_notification_history(
            self.session, USER_ID, day=datetime(2026, 9, 30).date()
        )
        self.assertNotIn("Other user", titles(result))


class TestNotificationHistorySqliteFallback(unittest.TestCase):
    def setUp(self):
        self.conn = make_sqlite_connection()
        patcher = mock.patch.object(
            notifications, "get_db_connection", return_value=self.conn
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(notifications.config, "logger", mock.Mock())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, query):
        import asyncio

        return asyncio.run(notifications.notification_history(FakeRequest(query)))

    def test_no_date_returns_everything(self):
        rows = json.loads(self._run({}).text)["notifications"]
        self.assertEqual(len(rows), 3)

    def test_date_filter_spans_both_timestamp_formats(self):
        rows = json.loads(self._run({"date": "2026-09-30"}).text)["notifications"]
        self.assertEqual([r["title"] for r in rows], ["Battery full", "Grid export"])

        rows = json.loads(self._run({"date": "2026-09-29"}).text)["notifications"]
        self.assertEqual([r["title"] for r in rows], ["Off grid"])

    def test_malformed_date_returns_empty_without_touching_db(self):
        with mock.patch.object(
            notifications, "get_db_connection", side_effect=AssertionError("db hit")
        ):
            rows = json.loads(self._run({"date": "not-a-date"}).text)["notifications"]
        self.assertEqual(rows, [])
        notifications.config.logger.warning.assert_called_once()


class TestNotificationHistoryAuthOrdering(unittest.IsolatedAsyncioTestCase):
    async def test_expired_token_wins_over_malformed_date(self) -> None:
        error = jwt.ExpiredSignatureError("Signature has expired")
        with mock.patch.object(notifications.config, "USE_PG", True), mock.patch.object(
            security, "decode_access_token", side_effect=error
        ):
            response = await notifications.notification_history(
                FakeRequest({"date": "not-a-date"})
            )
        self.assertEqual(response.status, 401)

    async def test_valid_date_still_reaches_the_inverter_lookup(self) -> None:
        session = mock.Mock()
        with mock.patch.object(notifications.config, "USE_PG", True), mock.patch.object(
            notifications, "_require_jwt_user_id", return_value=(uuid.uuid4(), None)
        ), mock.patch.object(
            notifications, "get_db_session", return_value=iter([session])
        ), mock.patch.object(
            notifications, "_resolve_request_inverter", return_value=None
        ) as resolve:
            response = await notifications.notification_history(
                FakeRequest({"date": "2026-09-30"})
            )
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text)["notifications"], [])
        resolve.assert_called_once()


if __name__ == "__main__":
    unittest.main()
