"""Trigger engine: evaluate automation triggers against inverter/Tuya state.

Re-exports the full public API above the internal submodules so existing
imports (`import trigger_engine; trigger_engine.evaluate_triggers_async(...)`,
`from trigger_engine import get_all_triggers`, ...) keep working.
"""

import logging

from .constants import (
    DEFAULT_RETRY_COUNT,
    FAILURE_ACTION_TYPES,
    MAX_RETRY_COUNT,
    TUYA_ACTION_TYPES,
    VALID_ACTION_TYPES,
    VALID_FIELDS,
    VALID_OPERATORS,
)
from .state import set_config, set_fcm_service, set_player
from .actions import (
    _execute_actions,
    _execute_failed_action,
    _execute_single_action,
    _get_actions,
    _get_execute_failed,
    _play_audio,
    _resolve_notification_params,
    _send_notification,
    build_history_detail,
)
from .conditions import (
    _check_conditions,
    _check_cooldown,
    _check_device_condition,
    _check_inverter_condition,
    _coerce_boolean,
    _compare,
    _is_in_time_window,
)
from .storage import (
    _parse_trigger_row,
    _update_last_triggered,
    add_trigger_history,
    delete_trigger,
    get_all_triggers,
    get_trigger,
    get_trigger_history,
    save_trigger,
)
from .evaluate import _run_evaluation_worker, evaluate_triggers, evaluate_triggers_async

logger = logging.getLogger("trigger_engine")

__all__ = [
    "VALID_ACTION_TYPES",
    "VALID_FIELDS",
    "VALID_OPERATORS",
    "TUYA_ACTION_TYPES",
    "FAILURE_ACTION_TYPES",
    "DEFAULT_RETRY_COUNT",
    "MAX_RETRY_COUNT",
    "set_config",
    "set_fcm_service",
    "set_player",
    "evaluate_triggers",
    "evaluate_triggers_async",
    "_run_evaluation_worker",
    "_execute_actions",
    "_execute_failed_action",
    "_execute_single_action",
    "_get_actions",
    "_get_execute_failed",
    "_play_audio",
    "_resolve_notification_params",
    "_send_notification",
    "build_history_detail",
    "_check_conditions",
    "_check_cooldown",
    "_check_device_condition",
    "_check_inverter_condition",
    "_coerce_boolean",
    "_compare",
    "_is_in_time_window",
    "_parse_trigger_row",
    "_update_last_triggered",
    "add_trigger_history",
    "delete_trigger",
    "get_all_triggers",
    "get_trigger",
    "get_trigger_history",
    "save_trigger",
]