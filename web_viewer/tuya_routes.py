import tuya_manager
from aiohttp.aiohttp import web

from . import config
from .db import get_db_connection


async def get_tuya_devices(_: web.Request):
    try:
        conn = get_db_connection()
        devices = tuya_manager.get_all_devices(conn)
        return web.json_response({"devices": devices})
    except Exception as e:
        config.logger.error("Error in get_tuya_devices: %s", e)
        return web.json_response({"devices": []})


async def scan_tuya_devices(_: web.Request):
    try:
        devices = await tuya_manager.scan_devices()
        return web.json_response({"devices": devices})
    except Exception as e:
        config.logger.error("Error in scan_tuya_devices: %s", e)
        return web.json_response({"devices": []})


async def tuya_wizard_status(_: web.Request):
    return web.json_response({"wizard_run": tuya_manager.is_wizard_run()})


async def add_tuya_device(request: web.Request):
    try:
        data = await request.json()
        required = ["id", "name", "ip", "local_key"]
        for field in required:
            if not data.get(field):
                return web.json_response({"success": False, "message": f"Missing field: {field}"}, status=400)
        conn = get_db_connection()
        result = tuya_manager.add_device(data, conn)
        return web.json_response({"success": True, "device": result})
    except Exception as e:
        config.logger.error("Error in add_tuya_device: %s", e)
        return web.json_response({"success": False}, status=500)


async def get_device_mappings(_: web.Request):
    try:
        mappings = tuya_manager.get_device_mappings()
        return web.json_response({"mappings": mappings})
    except Exception as e:
        config.logger.error("Error in get_device_mappings: %s", e)
        return web.json_response({"mappings": {}})


async def delete_tuya_device(request: web.Request):
    try:
        device_id = request.match_info.get("id")
        conn = get_db_connection()
        deleted = tuya_manager.delete_device(device_id, conn)
        return web.json_response({"success": deleted})
    except Exception as e:
        config.logger.error("Error in delete_tuya_device: %s", e)
        return web.json_response({"success": False}, status=500)


async def update_tuya_device(request: web.Request):
    try:
        device_id = request.match_info.get("id")
        data = await request.json()
        name = (data.get("name") or "").strip()
        if not name:
            return web.json_response({"success": False, "message": "Name is required"}, status=400)
        conn = get_db_connection()
        ok = tuya_manager.update_device_name(device_id, name, conn)
        return web.json_response({"success": ok})
    except Exception as e:
        config.logger.error("Error in update_tuya_device: %s", e)
        return web.json_response({"success": False}, status=500)


async def get_device_status(request: web.Request):
    try:
        device_id = request.match_info.get("id")
        conn = get_db_connection()
        status = await tuya_manager.get_device_status_from_db(device_id, conn)
        if status is None:
            return web.json_response({"error": "Device not found"}, status=404)
        return web.json_response(status)
    except Exception as e:
        config.logger.error("Error in get_device_status: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def batch_device_status(request: web.Request):
    try:
        data = await request.json()
        ids = data.get("ids") if isinstance(data, dict) else None
        if not ids or not isinstance(ids, list):
            return web.json_response({"success": False, "message": "ids list required"}, status=400)
        conn = get_db_connection()
        statuses = await tuya_manager.get_devices_status_batch(conn, ids)
        return web.json_response({"success": True, "statuses": statuses})
    except Exception as e:
        config.logger.error("Error in batch_device_status: %s", e)
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def control_device(request: web.Request):
    try:
        device_id = request.match_info.get("id")
        data = await request.json()
        action = data.get("action", "")
        params = data.get("params")
        conn = get_db_connection()
        result = await tuya_manager.control_device_from_db(device_id, action, conn, params)
        return web.json_response(result)
    except Exception as e:
        config.logger.error("Error in control_device: %s", e)
        return web.json_response({"error": str(e)}, status=500)