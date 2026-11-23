"""Trigger action execution: Tuya controls, FCM notifications, audio playback."""

import json
import logging
from typing import Optional

import tuya_manager
from play_audio import PlayAudio
from time_utils import format_datetime

from . import constants
from . import state

logger = logging.getLogger("trigger_engine")


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


def _get_execute_failed(trigger: dict) -> Optional[dict]:
    """Return the configured 'execute failed' action, or None when absent/invalid."""
    params = trigger.get("action_params") or {}
    if not isinstance(params, dict):
        return None
    config = params.get("execute_failed")
    if not isinstance(config, dict):
        return None
    if config.get("action_type") not in constants.FAILURE_ACTION_TYPES:
        return None
    return config


def _normalize_retry_count(value) -> int:
    """Clamp the configured retry count to a safe, non-zero range."""
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = constants.DEFAULT_RETRY_COUNT
    return max(1, min(constants.MAX_RETRY_COUNT, count))


def build_history_detail(trigger: dict, actions: list[dict]) -> str:
    """Serialize executed actions and the optional failure action for history."""
    detail = {"actions": actions}
    failure_config = _get_execute_failed(trigger)
    if failure_config:
        detail["execute_failed"] = failure_config
    return json.dumps(detail)


def _execute_actions(trigger: dict, actions: list[dict], db_conn, inverter_data: dict | None = None, is_manual: bool = False) -> bool:
    """Execute all actions, then the failure action if any Tuya control failed.

    Returns True only when every action succeeded (after retries).
    """
    other_actions_ok = True
    failed_tuya_actions: list[dict] = []
    for action in actions:
        ok = _execute_single_action(trigger, action, db_conn, inverter_data, is_manual=is_manual)
        if action.get("action_type") in constants.TUYA_ACTION_TYPES:
            if not ok:
                failed_tuya_actions.append(action)
        else:
            other_actions_ok = other_actions_ok and ok

    if not failed_tuya_actions:
        return other_actions_ok

    failure_config = _get_execute_failed(trigger)
    if not failure_config:
        return False

    failure_ok = _execute_failed_action(
        trigger, failure_config, failed_tuya_actions, db_conn,
        inverter_data, is_manual=is_manual,
    )
    # A successful retry resolves the failure; a notification still records the failed control.
    if failure_config.get("action_type") == "retry":
        return other_actions_ok and failure_ok
    return False


def _execute_failed_action(trigger: dict, config: dict, failed_actions: list[dict], db_conn,
                           inverter_data: dict | None = None, is_manual: bool = False) -> bool:
    """Run the single configured failure action for the failed Tuya controls."""
    action_type = config.get("action_type")
    if action_type == "notification":
        _send_notification(trigger, config, db_conn, inverter_data, is_manual=is_manual)
        return True
    if action_type == "retry":
        retry_count = _normalize_retry_count(config.get("retry_count"))
        all_ok = True
        for failed_action in failed_actions:
            ok = False
            for attempt in range(1, retry_count + 1):
                ok = _execute_single_action(
                    trigger, failed_action, db_conn, inverter_data, is_manual=is_manual,
                )
                if ok:
                    logger.info(
                        "Trigger '%s': retry %s/%s succeeded for action '%s'",
                        trigger["name"], attempt, retry_count, failed_action.get("action_type"),
                    )
                    break
            if not ok:
                logger.warning(
                    "Trigger '%s': all %s retries failed for action '%s'",
                    trigger["name"], retry_count, failed_action.get("action_type"),
                )
            all_ok = all_ok and ok
        return all_ok
    return False


def _execute_single_action(trigger: dict, action: dict, db_conn, inverter_data: dict | None = None, is_manual: bool = False) -> bool:
    """Execute a single action. Runs tuya commands synchronously.

    Returns True when the action succeeded, False otherwise.
    """
    action_type = action.get("action_type", "")
    device_id = action.get("device_id")
    params = action.get("params") or {}

    try:
        if action_type in constants.TUYA_ACTION_TYPES:
            if not device_id:
                logger.warning("Trigger '%s' action has no device_id for Tuya action", trigger["name"])
                return False
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
                return False
            tuya_action = action_map[action_type]
            result = tuya_manager._sync_control(row[0], row[1], row[2], tuya_action, row[3], params, db_conn)
            if isinstance(result, dict) and result.get("error"):
                logger.warning("Trigger '%s': Tuya action '%s' failed: %s", trigger["name"], action_type, result["error"])
                return False
            if not (isinstance(result, dict) and result.get("success") is True):
                logger.warning("Trigger '%s': unexpected result from Tuya action '%s': %s", trigger["name"], action_type, result)
                return False
            return True
        elif action_type == "notification":
            _send_notification(trigger, params, db_conn, inverter_data, is_manual=is_manual)
            return True
        elif action_type == "play_audio":
            _play_audio(trigger, params)
            return True
        else:
            logger.warning("Trigger '%s': unknown action_type '%s'", trigger["name"], action_type)
            return False
    except Exception as e:
        logger.error("Failed to execute action for trigger '%s': %s", trigger["name"], e)
        return False


def _resolve_notification_params(text: str, trigger: dict, inverter_data: dict | None, db_conn) -> str:
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
        for field in constants.VALID_FIELDS:
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


def _send_notification(trigger: dict, params: Optional[dict], db_conn, inverter_data: dict | None = None, is_manual: bool = False):
    """Send an FCM push notification and log to notification_history."""
    if params is None:
        params = {}
    title = params.get("notification_title", trigger.get("name", "Trigger Alert"))
    body = params.get("notification_body", f"Trigger '{trigger.get('name')}' fired.")
    title = _resolve_notification_params(title, trigger, inverter_data, db_conn)
    body = _resolve_notification_params(body, trigger, inverter_data, db_conn)
    if is_manual:
        title = f"[Thử nghiệm] {title}"
    if state._fcm_service is not None:
        try:
            state._fcm_service.send_notification(title, body)
            logger.info("Sent notification from trigger '%s': %s - %s", trigger["name"], title, body)
            return
        except Exception as e:
            logger.error("Failed to send notification for trigger '%s': %s", trigger["name"], e)
    # Fallback: log to notification_history even if FCM is unavailable
    try:
        db_conn.execute(
            "INSERT INTO notification_history (notified_at, title, body) VALUES (?, ?, ?)",
            (format_datetime(), title, body),
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
    cast_device_name = state._config.get("CAST_DEVICE_NAME", "")
    if not cast_device_name:
        logger.warning("Trigger '%s': CAST_DEVICE_NAME not configured", trigger["name"])
        return

    try:
        if state._player is not None:
            state._player.stop()
        state._player = PlayAudio(
            audio_url, repeat, wait_duration,
            {"CAST_DEVICE_NAME": cast_device_name, "AUDIO_BASE_URL": ""},
            logger,
        )
        state._player.start()
    except Exception as e:
        logger.error("Trigger '%s': play_audio error: %s", trigger["name"], e)
