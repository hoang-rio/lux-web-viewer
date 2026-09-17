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
        trigger_engine._execute_actions(tr, actions, conn, inverter_data, is_manual=True)
        trigger_engine._update_last_triggered(trigger_id, now, conn)
        action_desc = ", ".join(a.get("action_type", "unknown") for a in actions)
        trigger_engine.add_trigger_history(trigger_id, "success", f"Manual test: {action_desc}", conn, actions_detail=json.dumps(actions))
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