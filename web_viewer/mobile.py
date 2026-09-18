import json

from aiohttp.aiohttp import web

from api_storage import read_grid_state, register_device_token
from multi_tenant import repository as mt_repo
from multi_tenant.db import get_db_session

from . import config
from .security import _lmsg, _require_jwt_user_id, _resolve_request_inverter


async def mobile_state(request: web.Request):
    try:
        if config.USE_PG:
            user_id, auth_error = _require_jwt_user_id(request)
            if auth_error is not None:
                return auth_error
            session = next(get_db_session())
            try:
                inverter = _resolve_request_inverter(session, user_id, request)
                if not inverter:
                    session.close()
                    return web.json_response(
                        {"is_connected": False, "history": []},
                    )
                inverter_id = str(inverter.id)
                is_connected_str = mt_repo.get_scoped_setting(session, "inverter", inverter_id, "mobile_is_connected")
                is_connected = is_connected_str == "True" if is_connected_str else False
                history_str = mt_repo.get_scoped_setting(session, "inverter", inverter_id, "mobile_history")
                history = json.loads(history_str) if history_str else []
                session.close()
                return web.json_response(
                    {"is_connected": is_connected, "history": history},
                )
            except Exception:
                session.close()
                raise
        else:
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

        if config.USE_PG:
            user_id, auth_error = _require_jwt_user_id(request)
            if auth_error is not None:
                return auth_error
            if not token.strip():
                return web.json_response(
                    {"is_success": False, "message": _lmsg(request, "Missing required parameter 'token'"), "device_count": 0},
                    status=400,
                )
            session = next(get_db_session())
            try:
                mt_repo.upsert_device_token(session, user_id, token.strip())
                count = len(mt_repo.get_device_tokens_by_user(session, user_id))
                session.commit()
                return web.json_response(
                    {"is_success": True, "message": _lmsg(request, "Device register success"), "device_count": count},
                )
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        result = register_device_token(
            config.config.get("DEVICE_IDS_JSON_FILE", "devices.json"),
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