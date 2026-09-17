"""Trigger persistence: CRUD + execution history in SQLite."""

import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("trigger_engine")


def _update_last_triggered(trigger_id: int, now: datetime, db_conn) -> None:
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


def get_all_triggers(db_conn) -> list[dict]:
    rows = db_conn.execute(
        "SELECT id, name, enabled, when_start_time, when_end_time, when_days, "
        "conditions, action_type, action_device_id, action_params, cooldown_seconds, "
        "last_triggered_at, created_at FROM automation_triggers"
    ).fetchall()
    return [_parse_trigger_row(r) for r in rows]


def get_trigger(trigger_id: int, db_conn) -> Optional[dict]:
    row = db_conn.execute(
        "SELECT id, name, enabled, when_start_time, when_end_time, when_days, "
        "conditions, action_type, action_device_id, action_params, cooldown_seconds, "
        "last_triggered_at, created_at FROM automation_triggers WHERE id = ?",
        (trigger_id,),
    ).fetchone()
    if not row:
        return None
    return _parse_trigger_row(row)


def save_trigger(data: dict, db_conn) -> dict:
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


def delete_trigger(trigger_id: int, db_conn) -> bool:
    cursor = db_conn.execute("DELETE FROM automation_triggers WHERE id = ?", (trigger_id,))
    db_conn.commit()
    return cursor.rowcount > 0


def add_trigger_history(trigger_id: int, status: str, message: str, db_conn, actions_detail: str = ""):
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


def get_trigger_history(trigger_id: int, db_conn) -> list[dict]:
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