import asyncio
import json
import logging
import sqlite3
from datetime import datetime, time as dtime
from typing import Optional

import tuya_manager

logger = logging.getLogger(__name__)

VALID_FIELDS = {"soc", "p_pv", "p_discharge", "p_charge", "fac", "p_eps", "p_to_grid", "p_to_user", "v_bat"}
VALID_OPERATORS = {">", "<", ">=", "<=", "==", "!="}
VALID_ACTION_TYPES = {"tuya_on", "tuya_off", "tuya_toggle", "tuya_set", "notification"}


def evaluate_triggers(inverter_data: dict, db_conn: sqlite3.Connection):
    """Evaluate all enabled triggers against current inverter data.

    Called from app.py main loop.
    """
    try:
        triggers = get_all_triggers(db_conn)
        now = datetime.now()
        for trigger in triggers:
            if not trigger["enabled"]:
                continue
            if not _is_in_time_window(trigger, now):
                continue
            if not _check_conditions(trigger["conditions"], inverter_data):
                continue
            if not _check_cooldown(trigger, now):
                continue
            logger.info(
                "Trigger '%s' (id=%s) conditions met, executing action: %s",
                trigger["name"], trigger["id"], trigger["action_type"],
            )
            _execute_action_sync(trigger, db_conn)
            _update_last_triggered(trigger["id"], now, db_conn)
    except Exception as e:
        logger.error("Error evaluating triggers: %s", e)


def _is_in_time_window(trigger: dict, now: datetime) -> bool:
    """Check if current time falls within the trigger's When window."""
    start_str = trigger.get("when_start_time")
    end_str = trigger.get("when_end_time")
    days_str = trigger.get("when_days")

    if days_str:
        allowed_days = [int(d.strip()) for d in days_str.split(",") if d.strip()]
        iso_weekday = now.isoweekday()
        if iso_weekday not in allowed_days:
            return False

    if start_str and end_str:
        try:
            start_h, start_m = map(int, start_str.split(":"))
            end_h, end_m = map(int, end_str.split(":"))
            start = dtime(start_h, start_m)
            end = dtime(end_h, end_m)
            current = now.time().replace(second=0, microsecond=0)
            if start <= end:
                return start <= current <= end
            else:
                return current >= start or current <= end
        except (ValueError, AttributeError):
            return True

    return True


def _check_conditions(conditions: list, inverter_data: dict) -> bool:
    """All conditions must match (AND logic)."""
    if not conditions:
        return True
    for cond in conditions:
        field = cond.get("field", "")
        op = cond.get("op", "")
        value = cond.get("value")
        if field not in VALID_FIELDS or op not in VALID_OPERATORS or value is None:
            continue
        actual = inverter_data.get(field)
        if actual is None:
            return False
        if not _compare(actual, op, value):
            return False
    return True


def _compare(actual, op: str, expected) -> bool:
    try:
        actual_num = float(actual)
        expected_num = float(expected)
    except (TypeError, ValueError):
        actual_num = actual
        expected_num = expected

    if op == ">":
        return actual_num > expected_num
    elif op == "<":
        return actual_num < expected_num
    elif op == ">=":
        return actual_num >= expected_num
    elif op == "<=":
        return actual_num <= expected_num
    elif op == "==":
        return actual_num == expected_num
    elif op == "!=":
        return actual_num != expected_num
    return False


def _check_cooldown(trigger: dict, now: datetime) -> bool:
    """Check if enough time has passed since last trigger."""
    last_str = trigger.get("last_triggered_at")
    if not last_str:
        return True
    try:
        last = datetime.fromisoformat(last_str)
        cooldown = trigger.get("cooldown_seconds", 300)
        return (now - last).total_seconds() >= cooldown
    except (ValueError, TypeError):
        return True


def _execute_action_sync(trigger: dict, db_conn: sqlite3.Connection):
    """Execute the trigger's action. Runs tuya commands synchronously."""
    action_type = trigger["action_type"]
    device_id = trigger.get("action_device_id")
    params = None
    if trigger.get("action_params"):
        try:
            params = json.loads(trigger["action_params"])
        except (json.JSONDecodeError, TypeError):
            params = {}

    try:
        if action_type in ("tuya_on", "tuya_off", "tuya_toggle", "tuya_set"):
            if not device_id:
                logger.warning("Trigger '%s' has no device_id for Tuya action", trigger["name"])
                return
            action_map = {
                "tuya_on": "turn_on",
                "tuya_off": "turn_off",
                "tuya_toggle": "toggle",
                "tuya_set": "set_value",
            }
            row = db_conn.execute(
                "SELECT id, ip, local_key, protocol_version FROM tuya_devices WHERE id = ?",
                (device_id,),
            ).fetchone()
            if not row:
                logger.warning("Trigger '%s': device %s not found", trigger["name"], device_id)
                return
            tuya_action = action_map[action_type]
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    tuya_manager.control_device(row[0], row[1], row[2], tuya_action, row[3], params)
                )
            finally:
                loop.close()
        elif action_type == "notification":
            _send_notification(trigger, params, db_conn)
        else:
            logger.warning("Trigger '%s': unknown action_type '%s'", trigger["name"], action_type)
    except Exception as e:
        logger.error("Failed to execute action for trigger '%s': %s", trigger["name"], e)


def _send_notification(trigger: dict, params: Optional[dict], db_conn: sqlite3.Connection):
    """Send an FCM push notification."""
    if params is None:
        params = {}
    title = params.get("notification_title", trigger.get("name", "Trigger Alert"))
    body = params.get("notification_body", f"Trigger '{trigger.get('name')}' fired.")
    try:
        from web_viewer import get_db_connection
        from fcm import FCM
        import settings as app_settings

        config = {}
        try:
            from dotenv import dotenv_values
            from os import environ
            config = {**dotenv_values(".env"), **environ}
        except Exception:
            pass
        fcm = FCM(logger, config)
        fcm.send_notification(title, body)
        db_conn.execute(
            "INSERT INTO notification_history (notified_at, title, body) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), title, body),
        )
        db_conn.commit()
        logger.info("Sent notification from trigger '%s': %s - %s", trigger["name"], title, body)
    except Exception as e:
        logger.error("Failed to send notification for trigger '%s': %s", trigger["name"], e)


def _update_last_triggered(trigger_id: int, now: datetime, db_conn: sqlite3.Connection):
    db_conn.execute(
        "UPDATE automation_triggers SET last_triggered_at = ? WHERE id = ?",
        (now.isoformat(), trigger_id),
    )
    db_conn.commit()


def _parse_trigger_row(row) -> dict:
    return {
        "id": row[0],
        "name": row[1],
        "enabled": bool(row[2]),
        "when_start_time": row[3],
        "when_end_time": row[4],
        "when_days": row[5],
        "conditions": json.loads(row[6]) if row[6] else [],
        "action_type": row[7],
        "action_device_id": row[8],
        "action_params": json.loads(row[9]) if row[9] else None,
        "cooldown_seconds": row[10],
        "last_triggered_at": row[11],
        "created_at": row[12],
    }


def get_all_triggers(db_conn: sqlite3.Connection) -> list[dict]:
    rows = db_conn.execute(
        "SELECT id, name, enabled, when_start_time, when_end_time, when_days, "
        "conditions, action_type, action_device_id, action_params, cooldown_seconds, "
        "last_triggered_at, created_at FROM automation_triggers"
    ).fetchall()
    return [_parse_trigger_row(r) for r in rows]


def get_trigger(trigger_id: int, db_conn: sqlite3.Connection) -> Optional[dict]:
    row = db_conn.execute(
        "SELECT id, name, enabled, when_start_time, when_end_time, when_days, "
        "conditions, action_type, action_device_id, action_params, cooldown_seconds, "
        "last_triggered_at, created_at FROM automation_triggers WHERE id = ?",
        (trigger_id,),
    ).fetchone()
    if not row:
        return None
    return _parse_trigger_row(row)


def save_trigger(data: dict, db_conn: sqlite3.Connection) -> dict:
    trigger_id = data.get("id")
    conditions_json = json.dumps(data.get("conditions", []))
    action_params_json = json.dumps(data.get("action_params")) if data.get("action_params") is not None else None
    enabled = 1 if data.get("enabled", True) else 0

    if trigger_id:
        db_conn.execute(
            "UPDATE automation_triggers SET name=?, enabled=?, when_start_time=?, when_end_time=?, "
            "when_days=?, conditions=?, action_type=?, action_device_id=?, action_params=?, "
            "cooldown_seconds=? WHERE id=?",
            (
                data["name"], enabled, data.get("when_start_time"), data.get("when_end_time"),
                data.get("when_days"), conditions_json, data["action_type"],
                data.get("action_device_id"), action_params_json,
                data.get("cooldown_seconds", 300), trigger_id,
            ),
        )
    else:
        cursor = db_conn.execute(
            "INSERT INTO automation_triggers "
            "(name, enabled, when_start_time, when_end_time, when_days, conditions, "
            "action_type, action_device_id, action_params, cooldown_seconds, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["name"], enabled, data.get("when_start_time"), data.get("when_end_time"),
                data.get("when_days"), conditions_json, data["action_type"],
                data.get("action_device_id"), action_params_json,
                data.get("cooldown_seconds", 300), datetime.now().isoformat(),
            ),
        )
        trigger_id = cursor.lastrowid
    db_conn.commit()
    result = get_trigger(trigger_id, db_conn)
    return result or {"id": trigger_id}


def delete_trigger(trigger_id: int, db_conn: sqlite3.Connection) -> bool:
    cursor = db_conn.execute("DELETE FROM automation_triggers WHERE id = ?", (trigger_id,))
    db_conn.commit()
    return cursor.rowcount > 0
