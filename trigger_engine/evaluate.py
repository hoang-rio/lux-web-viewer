"""Trigger evaluation orchestration (sync loop + async/threaded worker)."""

import asyncio
import json
import logging
import sqlite3
from datetime import datetime

from . import actions
from . import conditions
from . import state
from . import storage

logger = logging.getLogger("trigger_engine")


def evaluate_triggers(inverter_data: dict, db_conn):
    """Evaluate all enabled triggers against current inverter data.

    Called from app.py main loop.
    """
    try:
        triggers = storage.get_all_triggers(db_conn)
        now = datetime.now()
        device_status_cache: dict[str, dict] = {}
        for trigger in triggers:
            if not trigger["enabled"]:
                continue
            if not conditions._is_in_time_window(trigger, now):
                logger.debug(
                    "Trigger '%s' (id=%s) skipped: outside time window (when_days=%s, %s-%s)",
                    trigger["name"], trigger["id"], trigger.get("when_days"),
                    trigger.get("when_start_time"), trigger.get("when_end_time"),
                )
                continue
            conditions_ok, conditions_reason = conditions._check_conditions(
                trigger["conditions"], inverter_data, db_conn, device_status_cache,
            )
            if not conditions_ok:
                logger.debug(
                    "Trigger '%s' (id=%s) conditions not met: %s",
                    trigger["name"], trigger["id"], conditions_reason,
                )
                continue
            if not conditions._check_cooldown(trigger, now):
                logger.debug(
                    "Trigger '%s' (id=%s) skipped: cooldown not elapsed (cooldown_seconds=%s)",
                    trigger["name"], trigger["id"], trigger.get("cooldown_seconds"),
                )
                continue
            action_list = actions._get_actions(trigger)
            action_desc = ", ".join(a.get("action_type", "unknown") for a in action_list)
            logger.info(
                "Trigger '%s' (id=%s) conditions met, executing actions: %s",
                trigger["name"], trigger["id"], action_desc,
            )
            try:
                actions._execute_actions(trigger, action_list, db_conn, inverter_data)
                storage.add_trigger_history(trigger["id"], "success", action_desc, db_conn, actions_detail=json.dumps(action_list))
            except Exception as e:
                logger.error("Failed to execute actions for trigger '%s': %s", trigger["name"], e)
                storage.add_trigger_history(trigger["id"], "error", str(e), db_conn, actions_detail=json.dumps(action_list))
            storage._update_last_triggered(trigger["id"], now, db_conn)
    except Exception as e:
        logger.error("Error evaluating triggers: %s", e)


def _run_evaluation_worker(inverter_data: dict):
    """Run trigger evaluation in a worker thread with a dedicated DB connection.

    Uses its own connection so the blocking tinytuya/FCM I/O inside
    evaluate_triggers() never stalls the main event loop, and no sqlite3
    connection object is shared across threads.
    """
    if not state._eval_lock.acquire(blocking=False):
        logger.debug("Trigger evaluation still running, skipping this round")
        return
    conn = None
    try:
        db_name = state._config.get("DB_NAME") if state._config else None
        if not db_name:
            logger.warning("DB_NAME not configured; skipping trigger evaluation")
            return
        conn = sqlite3.connect(db_name, timeout=10)
        evaluate_triggers(inverter_data, conn)
    except Exception as e:
        logger.error("Error evaluating triggers in worker: %s", e)
    finally:
        if conn is not None:
            conn.close()
        state._eval_lock.release()


async def evaluate_triggers_async(inverter_data: dict):
    """Evaluate triggers off the main event loop.

    Device conditions/actions and FCM sends perform blocking network I/O
    (tinytuya status reads/controls, up to 15s IP-fallback scans), so they
    run in a thread pool instead of blocking the event loop.
    """
    await asyncio.to_thread(_run_evaluation_worker, inverter_data)