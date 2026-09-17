import modbus_controller
import modbus_registers
from aiohttp.aiohttp import web

from . import config


async def modbus_status(request: web.Request):
    try:
        return web.json_response(modbus_controller.controller.status)
    except Exception as e:
        config.logger.error("Error in modbus_status: %s", e)
        return web.json_response(modbus_controller.controller.status)


async def modbus_registers_route(request: web.Request):
    try:
        categories = []
        for category in modbus_registers.categories():
            items = [
                modbus_registers.public_item(item)
                for item in modbus_registers.get_items(category["key"])
            ]
            categories.append({
                "key": category["key"],
                "items": items,
            })
        return web.json_response({
            "categories": categories,
            "status": modbus_controller.controller.status,
        })
    except Exception as e:
        config.logger.error("Error in modbus_registers: %s", e)
        return web.json_response({"categories": [], "message": str(e)}, status=500)


async def modbus_read(request: web.Request):
    try:
        if not modbus_controller.controller.available:
            return web.json_response({
                "success": False,
                "message": "Modbus is not available in the current mode",
                "status": modbus_controller.controller.status,
            }, status=503)
        category = request.query.get("category")
        keys = request.query.get("keys")
        if keys:
            key_list = [k.strip() for k in keys.split(",") if k.strip()]
        elif category:
            key_list = [item["key"] for item in modbus_registers.get_items(category)]
        else:
            key_list = [item["key"] for item in modbus_registers.all_items()]
        values = await modbus_controller.controller.read_items(key_list)
        return web.json_response({"success": True, "values": values, "status": modbus_controller.controller.status})
    except Exception as e:
        # asyncio.wait_for (and dongle_server.py:292) surface a BARE
        # asyncio.TimeoutError() whose str() is the empty string; the FE
        # would otherwise receive {"message": ""} with nothing to show.
        message = str(e) or "No response from Modbus dongle (timeout)"
        config.logger.error("Error in modbus_read: %s", message)
        return web.json_response({"success": False, "message": message}, status=500)


async def modbus_write(request: web.Request):
    try:
        data = await request.json()
        key = data.get("key")
        item = modbus_registers.get_item(key)
        if item is None:
            return web.json_response({"success": False, "message": "Unknown register key: %s" % key}, status=400)
        if not modbus_controller.controller.available:
            return web.json_response({
                "success": False,
                "message": "Modbus is not available in the current mode",
                "status": modbus_controller.controller.status,
            }, status=503)
        new_value = await modbus_controller.controller.write_item(item, data.get("value"), confirm=bool(data.get("confirm")))
        return web.json_response({"success": True, "key": key, "value": new_value})
    except ValueError as e:
        config.logger.warning("Modbus write rejected: %s", e)
        return web.json_response({"success": False, "message": str(e)}, status=400)
    except Exception as e:
        message = str(e) or "Failed to write Modbus register (timeout)"
        config.logger.error("Error in modbus_write: %s", message)
        return web.json_response({"success": False, "message": message}, status=500)