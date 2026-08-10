import json
import logging
import os
import sqlite3
from datetime import datetime, time as dtime
from typing import Optional

import tuya_manager
from play_audio import PlayAudio

logger = logging.getLogger(__name__)

_fcm_service = None
_config = {}
_player: PlayAudio | None = None


def set_fcm_service(fcm):
    """Set the shared FCM service instance (called from app.py)."""
    global _fcm_service
    _fcm_service = fcm

def set_config(config):
    """Set the shared config instance (called from app.py)."""
    global _config
    _config = config

def set_player(player):
    """Set the shared config instance (called from app.py)."""
    global _player
    _player = player

VALID_FIELDS = {"soc", "p_pv", "p_discharge", "p_charge", "fac", "p_eps", "p_to_grid", "p_to_user", "v_bat"}
VALID_OPERATORS = {">", "<", ">=", "<=", "==", "!="}
VALID_ACTION_TYPES = {"tuya_on", "tuya_off", "tuya_toggle", "tuya_set", "notification", "play_audio"}


def evaluate_triggers(inverter_data: dict, db_conn: sqlite3.Connection):
    """Evaluate all enabled triggers against current inverter data.

    Called from app.py main loop.
    """
    try:
        triggers = get_all_triggers(db_conn)
        now = datetime.now()
        device_status_cache: dict[str, dict] = {}
        for trigger in triggers:
            if not trigger["enabled"]:
                continue
            if not _is_in_time_window(trigger, now):
                logger.debug(
                    "Trigger '%s' (id=%s) skipped: outside time window (when_days=%s, %s-%s)",
                    trigger["name"], trigger["id"], trigger.get("when_days"),
                    trigger.get("when_start_time"), trigger.get("when_end_time"),
                )
                continue
            conditions_ok, conditions_reason = _check_conditions(
                trigger["conditions"], inverter_data, db_conn, device_status_cache,
            )
            if not conditions_ok:
                logger.debug(
                    "Trigger '%s' (id=%s) conditions not met: %s",
                    trigger["name"], trigger["id"], conditions_reason,
                )
                continue
            if not _check_cooldown(trigger, now):
                logger.debug(
                    "Trigger '%s' (id=%s) skipped: cooldown not elapsed (cooldown_seconds=%s)",
                    trigger["name"], trigger["id"], trigger.get("cooldown_seconds"),
                )
                continue
            actions = _get_actions(trigger)
            action_desc = ", ".join(a.get("action_type", "unknown") for a in actions)
            logger.info(
                "Trigger '%s' (id=%s) conditions met, executing actions: %s",
                trigger["name"], trigger["id"], action_desc,
            )
            try:
                _execute_actions(trigger, actions, db_conn, inverter_data)
                add_trigger_history(trigger["id"], "success", action_desc, db_conn, actions_detail=json.dumps(actions))
            except Exception as e:
                logger.error("Failed to execute actions for trigger '%s': %s", trigger["name"], e)
                add_trigger_history(trigger["id"], "error", str(e), db_conn, actions_detail=json.dumps(actions))
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


def _check_conditions(conditions: list, inverter_data: dict, db_conn: sqlite3.Connection, device_status_cache: dict) -> tuple[bool, str]:
    """All conditions must match (AND logic). Returns (matched, reason)."""
    if not conditions:
        return True, ""
    for idx, cond in enumerate(conditions):
        cond_type = cond.get("condition_type", "inverter")
        if cond_type == "device":
            cond_ok, cond_reason = _check_device_condition(cond, db_conn, device_status_cache)
        else:
            cond_ok, cond_reason = _check_inverter_condition(cond, inverter_data)
        if not cond_ok:
            return False, f"condition {idx + 1} ({cond_type}): {cond_reason}"
    return True, ""


def _check_inverter_condition(cond: dict, inverter_data: dict) -> tuple[bool, str]:
    field = cond.get("field", "")
    op = cond.get("op", "")
    value = cond.get("value")
    if field not in VALID_FIELDS or op not in VALID_OPERATORS or value is None:
        return False, f"invalid condition (field={field!r}, op={op!r}, value={value!r})"
    actual = inverter_data.get(field)
    if actual is None:
        return False, f"field '{field}' not present in inverter data"
    if _compare(actual, op, value):
        return True, ""
    return False, f"{field} {op} {value} (actual {actual})"


def _check_device_condition(cond: dict, db_conn: sqlite3.Connection, cache: dict) -> tuple[bool, str]:
    device_id = cond.get("device_id", "")
    dps_key = cond.get("dps_key", "1")
    op = cond.get("op", "==")
    expected = cond.get("compare_value")
    if not device_id or expected is None:
        return False, f"missing device_id or compare_value (device_id={device_id!r}, compare_value={expected!r})"

    if device_id not in cache:
        try:
            row = db_conn.execute(
                "SELECT id, ip, local_key, protocol_version FROM tuya_devices WHERE id = ?",
                (device_id,),
            ).fetchone()
            if not row:
                cache[device_id] = {"error": "not found"}
            else:
                cache[device_id] = tuya_manager._sync_get_status(row[0], row[1], row[2], row[3], db_conn)
        except Exception as e:
            logger.warning("Failed to get status for device %s (condition): %s", device_id, e)
            cache[device_id] = {"error": str(e)}

    status = cache.get(device_id, {})
    if "error" in status:
        logger.warning("Trigger condition device %s unreachable: %s", device_id, status["error"])
        return False, f"device {device_id} status error: {status['error']}"

    dps_data = status.get("dps", status)
    actual = dps_data.get(str(dps_key))
    if actual is None:
        return False, f"device {device_id} has no dps[{dps_key}] (available: {sorted(dps_data.keys())})"
    if _compare(actual, op, expected):
        return True, ""
    return False, f"device {device_id} dps[{dps_key}] {op} {expected!r} (actual {actual!r})"


def _coerce_boolean(value):
    """Map common boolean representations to bool.

    Tuya devices may report boolean DPS values as bool, int (0/1) or strings
    ("true"/"false", "on"/"off"). Normalize them so `==`/`!=` conditions match
    regardless of representation.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "on", "1"):
            return True
        if low in ("false", "off", "0"):
            return False
    return value


def _compare(actual, op: str, expected) -> bool:
    actual = _coerce_boolean(actual)
    expected = _coerce_boolean(expected)
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


def _get_actions(trigger: dict) -> list[dict]:
    """Extract actions list from trigger. Supports both legacy single-action and new multi-action."""
    params = trigger.get("action_params") or {}
    if isinstance(params, dict) and "actions" in params and isinstance(params["actions"], list):
        return params["actions"]
    return [{
        "action_type": trigger.get("action_type", "notification"),
        "device_id": trigger.get("action_device_id"),
        "params": params,
    }]


def _execute_actions(trigger: dict, actions: list[dict], db_conn: sqlite3.Connection, inverter_data: dict | None = None, is_manual: bool = False):
    """Execute all actions for a trigger."""
    for action in actions:
        _execute_single_action(trigger, action, db_conn, inverter_data, is_manual=is_manual)


def _execute_single_action(trigger: dict, action: dict, db_conn: sqlite3.Connection, inverter_data: dict | None = None, is_manual: bool = False):
    """Execute a single action. Runs tuya commands synchronously."""
    action_type = action.get("action_type", "")
    device_id = action.get("device_id")
    params = action.get("params") or {}

    try:
        if action_type in ("tuya_on", "tuya_off", "tuya_toggle", "tuya_set"):
            if not device_id:
                logger.warning("Trigger '%s' action has no device_id for Tuya action", trigger["name"])
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
            tuya_manager._sync_control(row[0], row[1], row[2], tuya_action, row[3], params, db_conn)
        elif action_type == "notification":
            _send_notification(trigger, params, db_conn, inverter_data, is_manual=is_manual)
        elif action_type == "play_audio":
            _play_audio(trigger, params)
        else:
            logger.warning("Trigger '%s': unknown action_type '%s'", trigger["name"], action_type)
    except Exception as e:
        logger.error("Failed to execute action for trigger '%s': %s", trigger["name"], e)


def _resolve_notification_params(text: str, trigger: dict, inverter_data: dict | None, db_conn: sqlite3.Connection) -> str:
    """Replace $param placeholders in notification text with actual values.

    Available params are derived from all conditions of the same type:
    - If trigger has any inverter condition: ALL inverter fields ($soc, $p_pv, etc.)
    - If trigger has any device condition on a device: ALL DPS values ($device_{device_name}_{dps_code})
    """
    if not text or '$' not in text:
        return text

    conditions = trigger.get("conditions", [])
    has_inverter = any(c.get("condition_type", "inverter") == "inverter" for c in conditions)
    device_ids = list({c.get("device_id") for c in conditions if c.get("condition_type") == "device" and c.get("device_id")})

    if has_inverter and inverter_data is not None:
        for field in VALID_FIELDS:
            param = f"${field}"
            if param in text:
                actual = inverter_data.get(field)
                if actual is not None:
                    text = text.replace(param, str(actual))

    for device_id in device_ids:
        try:
            row = db_conn.execute(
                "SELECT id, name, ip, local_key, protocol_version FROM tuya_devices WHERE id = ?",
                (device_id,),
            ).fetchone()
            if not row:
                continue
            dev_name = row[1] or device_id
            status = tuya_manager._sync_get_status(row[0], row[2], row[3], row[4], db_conn)
            dps_data = status.get("dps", status)
            for dps_key, dps_val in dps_data.items():
                if dps_val is None:
                    continue
                dps_code = ""
                for c in conditions:
                    if c.get("condition_type") == "device" and c.get("device_id") == device_id and str(c.get("dps_key")) == str(dps_key):
                        dps_code = c.get("dps_code") or ""
                        break
                param = f"${dev_name.replace(' ', '_')}_{dps_code}" if dps_code else f"${dev_name.replace(' ', '_')}_{dps_key}"
                if param in text:
                    text = text.replace(param, str(dps_val))
        except Exception as e:
            logger.error("Failed to resolve device params for %s: %s", device_id, e)

    return text


def _send_notification(trigger: dict, params: Optional[dict], db_conn: sqlite3.Connection, inverter_data: dict | None = None, is_manual: bool = False):
    """Send an FCM push notification and log to notification_history."""
    if params is None:
        params = {}
    title = params.get("notification_title", trigger.get("name", "Trigger Alert"))
    body = params.get("notification_body", f"Trigger '{trigger.get('name')}' fired.")
    title = _resolve_notification_params(title, trigger, inverter_data, db_conn)
    body = _resolve_notification_params(body, trigger, inverter_data, db_conn)
    if is_manual:
        title = f"[Thử nghiệm] {title}"
    if _fcm_service is not None:
        try:
            _fcm_service.send_notification(title, body)
            logger.info("Sent notification from trigger '%s': %s - %s", trigger["name"], title, body)
            return
        except Exception as e:
            logger.error("Failed to send notification for trigger '%s': %s", trigger["name"], e)
    # Fallback: log to notification_history even if FCM is unavailable
    try:
        db_conn.execute(
            "INSERT INTO notification_history (notified_at, title, body) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), title, body),
        )
        db_conn.commit()
    except Exception:
        pass


def _play_audio(trigger: dict, params: dict):
    """Play audio on Chromecast using the shared PlayAudio class."""
    audio_url = params.get("audio_url", "")
    repeat = int(params.get("audio_repeat", 1))
    wait_duration = int(params.get("audio_wait", 5))

    if not audio_url:
        logger.warning("Trigger '%s': play_audio has no audio_url", trigger["name"])
        return
    global _config
    cast_device_name = _config.get("CAST_DEVICE_NAME", "")
    if not cast_device_name:
        logger.warning("Trigger '%s': CAST_DEVICE_NAME not configured", trigger["name"])
        return

    try:
        global _player
        if _player is not None:
            _player.stop()
        _player = PlayAudio(
            audio_url, repeat, wait_duration,
            {"CAST_DEVICE_NAME": cast_device_name, "AUDIO_BASE_URL": ""},
            logger,
        )
        _player.start()
    except Exception as e:
        logger.error("Trigger '%s': play_audio error: %s", trigger["name"], e)


def _update_last_triggered(trigger_id: int, now: datetime, db_conn: sqlite3.Connection):
    db_conn.execute(
        "UPDATE automation_triggers SET last_triggered_at = ? WHERE id = ?",
        (now.isoformat(), trigger_id),
    )
    db_conn.commit()


def _parse_trigger_row(row) -> dict:
    action_params_raw = json.loads(row[9]) if row[9] else None
    actions_list = None
    if isinstance(action_params_raw, dict) and "actions" in action_params_raw and isinstance(action_params_raw["actions"], list):
        actions_list = action_params_raw["actions"]
    return {
        "id": row[0],
        "name": row[1],
        "enabled": bool(row[2]),
        "when_start_time": row[3],
        "when_end_time": row[4],
        "when_days": row[5],
        "conditions": json.loads(row[6]) if row[6] else [],
        "actions": actions_list,
        "action_type": row[7],
        "action_device_id": row[8],
        "action_params": action_params_raw,
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

    actions = data.get("actions")
    if actions is not None:
        action_params_payload = {"actions": actions}
        first = actions[0] if actions else {}
        action_type = first.get("action_type", "notification")
        action_device_id = first.get("device_id")
    else:
        action_type = data.get("action_type", "notification")
        action_device_id = data.get("action_device_id")
        action_params_payload = data.get("action_params")

    action_params_json = json.dumps(action_params_payload) if action_params_payload is not None else None
    enabled = 1 if data.get("enabled", True) else 0

    if trigger_id:
        db_conn.execute(
            "UPDATE automation_triggers SET name=?, enabled=?, when_start_time=?, when_end_time=?, "
            "when_days=?, conditions=?, action_type=?, action_device_id=?, action_params=?, "
            "cooldown_seconds=? WHERE id=?",
            (
                data["name"], enabled, data.get("when_start_time"), data.get("when_end_time"),
                data.get("when_days"), conditions_json, action_type,
                action_device_id, action_params_json,
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
                data.get("when_days"), conditions_json, action_type,
                action_device_id, action_params_json,
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


def add_trigger_history(trigger_id: int, status: str, message: str, db_conn: sqlite3.Connection, actions_detail: str = ""):
    """Save trigger execution history. Keeps max 10 records per trigger."""
    db_conn.execute(
        "INSERT INTO trigger_history (trigger_id, triggered_at, status, message, actions_detail) VALUES (?, ?, ?, ?, ?)",
        (trigger_id, datetime.now().isoformat(), status, message, actions_detail),
    )
    # Keep only latest 10 per trigger
    db_conn.execute(
        "DELETE FROM trigger_history WHERE trigger_id = ? AND id NOT IN "
        "(SELECT id FROM trigger_history WHERE trigger_id = ? ORDER BY triggered_at DESC LIMIT 10)",
        (trigger_id, trigger_id),
    )
    db_conn.commit()


def get_trigger_history(trigger_id: int, db_conn: sqlite3.Connection) -> list[dict]:
    """Get trigger execution history ordered by date descending."""
    rows = db_conn.execute(
        "SELECT id, trigger_id, triggered_at, status, message, actions_detail FROM trigger_history "
        "WHERE trigger_id = ? ORDER BY triggered_at DESC",
        (trigger_id,),
    ).fetchall()
    return [
        {"id": r[0], "trigger_id": r[1], "triggered_at": r[2], "status": r[3], "message": r[4], "actions_detail": r[5] or ""}
        for r in rows
    ]
