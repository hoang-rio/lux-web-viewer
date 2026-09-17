from aiohttp.aiohttp import web

from . import config
from .db import get_db_connection


async def get_settings(_: web.Request):
    try:
        import settings
        resp = dict(settings.settings)
        resp["CAST_DEVICE_CONFIGURED"] = "true" if settings.config.get("CAST_DEVICE_NAME", "").strip() else "false"
        return web.json_response(resp)
    except Exception as e:
        config.logger.error(f"Error in get_settings: {e}")
        return web.json_response({})


async def update_settings(request: web.Request):
    try:
        data = await request.json()
        conn = get_db_connection()
        import settings
        for key, value in data.items():
            settings.save_setting(key, value, conn)
        return web.json_response({"success": True})
    except Exception as e:
        config.logger.error(f"Error in update_settings: {e}")
        return web.json_response({"success": False})


async def options_settings(_: web.Request):
    return web.Response()