from aiohttp.aiohttp import web

from multi_tenant import repository as mt_repo
from multi_tenant.db import get_db_session
from sleep_cache import ALLOWED_SLEEP_TIME_SETTING_VALUES, clear_sleep_time_cache

from . import config
from .db import get_db_connection
from .security import _require_jwt_user_id


async def get_settings(request: web.Request):
    if config.USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error
        try:
            import settings
            session = next(get_db_session())
            try:
                user_settings = mt_repo.get_user_settings(session, user_id)
            finally:
                session.close()
            merged = dict(settings.settings)
            merged["SLEEP_TIME"] = str(settings.get_sleep_time())
            merged.update(user_settings)
            # Basic Auth settings are redundant in multi-tenant mode
            merged.pop("AUTH_ENABLED", None)
            merged.pop("AUTH_USERNAME", None)
            merged.pop("AUTH_PASSWORD", None)
            merged.pop("AUTH_BYPASS_CIDR", None)
            return web.json_response(merged)
        except Exception as e:
            config.logger.error(f"Error in get_settings (multi-tenant): {e}")
            return web.json_response({})

    try:
        import settings
        merged = dict(settings.settings)
        merged["SLEEP_TIME"] = str(settings.get_sleep_time())
        return web.json_response(merged)
    except Exception as e:
        config.logger.error(f"Error in get_settings: {e}")
        return web.json_response({})


async def update_settings(request: web.Request):
    if config.USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error
        try:
            data = await request.json()
            session = next(get_db_session())
            try:
                sleep_time_changed = False
                for key, value in data.items():
                    # Basic auth settings are deprecated in multi-tenant mode.
                    if key in {"AUTH_ENABLED", "AUTH_USERNAME", "AUTH_PASSWORD", "AUTH_BYPASS_CIDR"}:
                        continue
                    if key == "SLEEP_TIME" and str(value) not in ALLOWED_SLEEP_TIME_SETTING_VALUES:
                        return web.json_response({"success": False}, status=400)
                    if key == "SLEEP_TIME":
                        sleep_time_changed = True
                    mt_repo.upsert_user_setting(session, user_id, key, str(value))
                session.commit()
                if sleep_time_changed:
                    clear_sleep_time_cache(str(user_id))
                return web.json_response({"success": True})
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        except Exception as e:
            config.logger.error(f"Error in update_settings (multi-tenant): {e}")
            return web.json_response({"success": False})

    try:
        data = await request.json()
        conn = get_db_connection()
        import settings
        saved_sleep_value = None
        for key, value in data.items():
            if key == "SLEEP_TIME" and str(value) not in ALLOWED_SLEEP_TIME_SETTING_VALUES:
                return web.json_response({"success": False}, status=400)
            settings.save_setting(key, value, conn)
            if key == "SLEEP_TIME":
                saved_sleep_value = str(value)
        # If non-PG mode and sleep time changed, update in-memory app config so running app picks it up
        if saved_sleep_value is not None:
            try:
                import sys
                app_mod = sys.modules.get("app") or sys.modules.get("__main__")
                if app_mod and hasattr(app_mod, "config"):
                    app_mod.config["SLEEP_TIME"] = saved_sleep_value
            except Exception:
                pass
        return web.json_response({"success": True})
    except Exception as e:
        config.logger.error(f"Error in update_settings: {e}")
        return web.json_response({"success": False})