"""Tests for the trigger 'execute failed action' failure handling."""

import json
import sqlite3
import sys
import types
import unittest
from unittest import mock

# Hardware libraries are imported at module load; their calls are mocked in these tests.
if "tinytuya" not in sys.modules:
    tinytuya_stub = types.ModuleType("tinytuya")
    tinytuya_stub.OutletDevice = object
    tinytuya_stub.deviceScan = lambda **kwargs: {}
    sys.modules["tinytuya"] = tinytuya_stub
if "pychromecast" not in sys.modules:
    chromecast_stub = types.ModuleType("pychromecast")
    chromecast_stub.get_listed_chromecasts = lambda *args, **kwargs: ([], None)
    sys.modules["pychromecast"] = chromecast_stub

from trigger_engine import actions, storage


def make_trigger(failure=None, action_types=None):
    action_types = action_types or ["tuya_on"]
    action_list = [
        {"action_type": action_type, "device_id": "dev1", "params": {}}
        for action_type in action_types
    ]
    return {
        "name": "Test trigger",
        "conditions": [],
        "actions": action_list,
        "action_params": {"actions": action_list, "execute_failed": failure},
    }


class ExecuteFailedActionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE tuya_devices ("
            "id TEXT PRIMARY KEY, ip TEXT, local_key TEXT, protocol_version TEXT)"
        )
        self.conn.execute("INSERT INTO tuya_devices VALUES ('dev1', '1.2.3.4', 'key', '3.3')")
        self.conn.execute(
            "CREATE TABLE notification_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, notified_at TEXT, title TEXT, body TEXT)"
        )

    def tearDown(self):
        self.conn.close()

    def test_tuya_action_returns_outcome_from_sync_control(self):
        trigger = make_trigger()
        action = trigger["actions"][0]

        with mock.patch.object(
            actions.tuya_manager, "_sync_control",
            return_value={"success": True, "action": "turn_on"},
        ):
            self.assertTrue(actions._execute_single_action(trigger, action, self.conn))

        with mock.patch.object(
            actions.tuya_manager, "_sync_control",
            return_value={"error": "device offline"},
        ):
            self.assertFalse(actions._execute_single_action(trigger, action, self.conn))

    def test_failure_notification_runs_when_any_tuya_action_fails(self):
        trigger = make_trigger(
            failure={
                "action_type": "notification",
                "notification_title": "Relay failed",
                "notification_body": "Could not switch device",
            },
            action_types=["notification", "tuya_off"],
        )

        with mock.patch.object(actions.tuya_manager, "_sync_control", return_value={"error": "offline"}):
            result = actions._execute_actions(trigger, trigger["actions"], self.conn)

        self.assertFalse(result)
        titles = [row[0] for row in self.conn.execute("SELECT title FROM notification_history")]
        self.assertIn("Relay failed", titles)

    def test_retry_reruns_only_failed_tuya_action_and_stops_on_success(self):
        trigger = make_trigger(
            failure={"action_type": "retry", "retry_count": 3},
            action_types=["tuya_on", "tuya_off"],
        )

        with mock.patch.object(
            actions.tuya_manager, "_sync_control",
            side_effect=[{"error": "offline"}, {"success": True}, {"success": True}],
        ) as control:
            result = actions._execute_actions(trigger, trigger["actions"], self.conn)

        self.assertTrue(result)
        self.assertEqual(control.call_count, 3)
        self.assertEqual(control.call_args_list[0].args[3], "turn_on")
        self.assertEqual(control.call_args_list[1].args[3], "turn_off")
        self.assertEqual(control.call_args_list[2].args[3], "turn_on")

    def test_retry_stops_after_configured_attempts(self):
        trigger = make_trigger(failure={"action_type": "retry", "retry_count": 2})

        with mock.patch.object(
            actions.tuya_manager, "_sync_control", return_value={"error": "offline"}
        ) as control:
            result = actions._execute_actions(trigger, trigger["actions"], self.conn)

        self.assertFalse(result)
        self.assertEqual(control.call_count, 3)  # initial attempt + 2 retries

    def test_retry_count_is_clamped(self):
        self.assertEqual(actions._normalize_retry_count(0), 1)
        self.assertEqual(actions._normalize_retry_count(999), 10)
        self.assertEqual(actions._normalize_retry_count("bad"), 3)

    def test_no_failure_action_runs_when_all_actions_succeed(self):
        trigger = make_trigger(failure={"action_type": "notification", "notification_title": "Failed"})

        with mock.patch.object(
            actions.tuya_manager, "_sync_control", return_value={"success": True}
        ) as control:
            result = actions._execute_actions(trigger, trigger["actions"], self.conn)

        self.assertTrue(result)
        self.assertEqual(control.call_count, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM notification_history").fetchone()[0], 0)


class TriggerPersistenceTests(unittest.TestCase):
    def test_save_trigger_preserves_execute_failed(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE automation_triggers ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, enabled INTEGER, "
            "when_start_time TEXT, when_end_time TEXT, when_days TEXT, conditions TEXT, "
            "action_type TEXT, action_device_id TEXT, action_params TEXT, "
            "cooldown_seconds INTEGER, last_triggered_at TEXT, created_at TEXT)"
        )
        payload = make_trigger(failure={"action_type": "retry", "retry_count": 4})
        payload.update({"name": "Test", "enabled": True, "conditions": [], "cooldown_seconds": 300})

        saved = storage.save_trigger(payload, conn)

        self.assertEqual(
            saved["action_params"]["execute_failed"],
            {"action_type": "retry", "retry_count": 4},
        )
        self.assertEqual(saved["action_params"]["actions"], payload["actions"])
        conn.close()

    def test_build_history_detail_includes_failure_action(self):
        trigger = make_trigger(failure={"action_type": "retry", "retry_count": 2})

        detail = json.loads(actions.build_history_detail(trigger, trigger["actions"]))

        self.assertEqual(detail["actions"], trigger["actions"])
        self.assertEqual(detail["execute_failed"], {"action_type": "retry", "retry_count": 2})


if __name__ == "__main__":
    unittest.main()
