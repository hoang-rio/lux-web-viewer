from aiohttp.aiohttp import web

from . import config

from api_storage import read_grid_state, register_device_token


async def mobile_state(_: web.Request):
    try:
        state_file = config.config.get("STATE_FILE", "grid_connect_state.ini")
        history_file = config.config.get("HISTORY_FILE", "history.json")
        return web.json_response(
            read_grid_state(state_file, history_file),
        )
    except Exception as error:
        config.logger.error(f"Error in mobile_state: {error}")
        return web.json_response(
            {"is_connected": False, "history": []},
        )


async def register_fcm(request: web.Request):
    try:
        token = ""
        if request.can_read_body:
            content_type = request.content_type.lower() if request.content_type else ""
            if content_type == "application/json":
                payload = await request.json()
                if isinstance(payload, dict):
                    token = str(payload.get("token", ""))
            else:
                payload = await request.post()
                token = str(payload.get("token", ""))

        result = register_device_token(
            config.config.get("DEVICE_IDS_JSON_FILE", "fcm_devices.json"),
            token,
        )
        status = 200 if result["is_success"] else 400
        return web.json_response(result, status=status)
    except Exception as error:
        config.logger.error(f"Error in register_fcm: {error}")
        return web.json_response(
            {
                "is_success": False,
                "message": f"Got exception {error}",
                "device_count": 0,
            },
            status=500,
        )