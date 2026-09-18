import json
import unittest
from unittest.mock import patch

import jwt
from yarl import URL

import web_viewer


class _RequestStub:
    def __init__(self, path: str, method: str = "GET") -> None:
        self.method = method
        self.headers = {"Authorization": "Bearer expired-token"}
        self.rel_url = URL(path)
        self.path_qs = path
        self.can_read_body = False
        self.content_type = ""

    async def json(self):
        return {}

    async def post(self):
        return {}


class TestChartAuthResponses(unittest.IsolatedAsyncioTestCase):
    async def test_expired_token_returns_401_for_chart_and_total_endpoints(self) -> None:
        expired_error = jwt.ExpiredSignatureError("Signature has expired")
        handlers = [
            (web_viewer.total, "/total"),
            (web_viewer.hourly_chart, "/hourly-chart?date=2026-04-08"),
            (web_viewer.daily_chart, "/daily-chart?month=2026-04"),
            (web_viewer.monthly_chart, "/monthly-chart?year=2026"),
            (web_viewer.yearly_chart, "/yearly-chart"),
        ]

        with patch.object(web_viewer.config, "USE_PG", True), patch.object(
            web_viewer.security,
            "decode_access_token",
            side_effect=expired_error,
        ):
            for handler, path in handlers:
                with self.subTest(path=path):
                    request = _RequestStub(path)

                    response = await handler(request)
                    payload = json.loads(response.text)

                    self.assertEqual(response.status, 401)
                    self.assertFalse(payload["success"])
                    self.assertEqual(payload["message"], "Token expired")

    async def test_other_protected_endpoints_also_return_401_for_expired_token(self) -> None:
        expired_error = jwt.ExpiredSignatureError("Signature has expired")
        handlers = [
            (web_viewer.sse_handler, "/events?inverter_id=test-inverter", "GET"),
            (web_viewer.state, "/state?inverter_id=test-inverter", "GET"),
            (web_viewer.mobile_state, "/mobile/state?inverter_id=test-inverter", "GET"),
            (web_viewer.register_fcm, "/fcm/register", "POST"),
            (web_viewer.notification_history, "/notification-history", "GET"),
            (web_viewer.mark_notifications_read, "/notification-mark-read", "POST"),
            (web_viewer.notification_unread_count, "/notification-unread-count", "GET"),
            (web_viewer.get_settings, "/settings", "GET"),
            (web_viewer.update_settings, "/settings", "POST"),
        ]

        with patch.object(web_viewer.config, "USE_PG", True), patch.object(
            web_viewer.security,
            "decode_access_token",
            side_effect=expired_error,
        ):
            for handler, path, method in handlers:
                with self.subTest(path=path, method=method):
                    response = await handler(_RequestStub(path, method=method))
                    payload = json.loads(response.text)

                    self.assertEqual(response.status, 401)
                    self.assertFalse(payload["success"])
                    self.assertEqual(payload["message"], "Token expired")


if __name__ == "__main__":
    unittest.main()