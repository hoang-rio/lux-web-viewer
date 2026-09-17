"""Trigger scheduling and condition evaluation."""

import logging
from datetime import datetime, time as dtime

import tuya_manager

from . import constants

logger = logging.getLogger("trigger_engine")


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


def _check_conditions(conditions: list, inverter_data: dict, db_conn, device_status_cache: dict) -> tuple[bool, str]:
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
    if field not in constants.VALID_FIELDS or op not in constants.VALID_OPERATORS or value is None:
        return False, f"invalid condition (field={field!r}, op={op!r}, value={value!r})"
    actual = inverter_data.get(field)
    if actual is None:
        return False, f"field '{field}' not present in inverter data"
    if _compare(actual, op, value):
        return True, ""
    return False, f"{field} {op} {value} (actual {actual})"


def _check_device_condition(cond: dict, db_conn, cache: dict) -> tuple[bool, str]:
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