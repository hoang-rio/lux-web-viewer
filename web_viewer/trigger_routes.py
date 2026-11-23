import json
from datetime import datetime

import trigger_engine
from aiohttp.aiohttp import web

from . import config
from .db import get_db_connection
from . import streaming


async def get_triggers(_: web.Request):
    try:
        conn = get_db_connection()
        triggers = trigger_engine.get_all_triggers(conn)
        return web.json_response({"triggers": triggers})
    except Exception as e:
        config.logger.error("Error in get_triggers: %s", e)
        return web.json_response({"triggers": []})


async def save_trigger_route(request: web.Request):
    try:
        data = await request.json()
        if not data.get("name"):
            return web.json_response({"success": False, "message": "Name is required"}, status=400)
        if not data.get("action_type"):
            return web.json_response({"success": False, "message": "Action type is required"}, status=400)
        action_params = data.get("action_params")
        failure_config = action_params.get("execute_failed") if isinstance(action_params, dict) else None
        if failure_config is not None:
            actions = data.get("actions") or []
            main_action_type = (
                actions[0].get("action_type") if actions and isinstance(actions[0], dict)
                else data.get("action_type")
            )
            if main_action_type not in trigger_engine.TUYA_ACTION_TYPES:
                return web.json_response(
                    {"success": False, "message": "Failure action requires a Tuya device control main action"},
                    status=400,
                )
            if not isinstance(failure_config, dict) or failure_config.get("action_type") not in trigger_engine.FAILURE_ACTION_TYPES:
                return web.json_response(
                    {"success": False, "message": "Invalid failure action type"},
                    status=400,
                )
        conn = get_db_connection()
        result = trigger_engine.save_trigger(data, conn)
        return web.json_response({"success": True, "trigger": result})
    except Exception as e:
        config.logger.error("Error in save_trigger: %s", e)
        return web.json_response({"success": False}, status=500)


async def delete_trigger_route(request: web.Request):
    try:
        trigger_id = int(request.match_info.get("id"))
        conn = get_db_connection()
        deleted = trigger_engine.delete_trigger(trigger_id, conn)
        return web.json_response({"success": deleted})
    except Exception as e:
        config.logger.error("Error in delete_trigger: %s", e)
        return web.json_response({"success": False}, status=500)


async def test_trigger_route(request: web.Request):
    try:
        trigger_id = int(request.match_info.get("id"))
        conn = get_db_connection()
        tr = trigger_engine.get_trigger(trigger_id, conn)
        if not tr:
            return web.json_response({"success": False, "message": "Trigger not found"}, status=404)
        now = datetime.now()
        try:
            inverter_data = json.loads(streaming.last_inverter_data)["inverter_data"]
        except Exception:
            inverter_data = {}
        actions = trigger_engine._get_actions(tr)
        success = trigger_engine._execute_actions(tr, actions, conn, inverter_data, is_manual=True)
        trigger_engine._update_last_triggered(trigger_id, now, conn)
        action_desc = ", ".join(a.get("action_type", "unknown") for a in actions)
        status = "success" if success else "error"
        message = f"Manual test: {action_desc}"
        trigger_engine.add_trigger_history(
            trigger_id, status, message, conn,
            actions_detail=trigger_engine.build_history_detail(tr, actions),
        )
        return web.json_response({"success": True})
    except Exception as e:
        config.logger.error("Error in test_trigger: %s", e)
        return web.json_response({"success": False}, status=500)


async def get_trigger_history_route(request: web.Request):
    try:
        trigger_id = int(request.match_info.get("id"))
        conn = get_db_connection()
        history = trigger_engine.get_trigger_history(trigger_id, conn)
        return web.json_response({"history": history})
    except Exception as e:
        config.logger.error("Error in get_trigger_history: %s", e)
        return web.json_response({"history": []})