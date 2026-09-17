"""Modbus register read/write API routes, scoped to the logged-in user in
multi-tenant (PostgreSQL) mode.

In single-tenant mode (no PostgreSQL) the request is protected by the HTTP
Basic middleware; in multi-tenant mode every handler requires a valid JWT and
the target dongle is resolved from the user's own inverters.
"""
from __future__ import annotations

import uuid
from logging import getLogger
from os import environ

import jwt as _jwt
from aiohttp.aiohttp import web
from dotenv import dotenv_values

import modbus_controller
import modbus_registers
from multi_tenant.auth import decode_access_token
from multi_tenant.db import get_db_session
from multi_tenant import repository as repo
from multi_tenant.i18n import get_locale_from_accept_language, translate

logger = getLogger(__name__)

_CFG = {**dotenv_values(".env"), **environ}
_USE_PG = bool(_CFG.get("POSTGRES_DB_URL") or _CFG.get("DATABASE_URL"))


def _locale(request: web.Request) -> str:
    return get_locale_from_accept_language(request.headers.get("Accept-Language"))


def _msg(request: web.Request, message: str) -> str:
    return translate(message, _locale(request))


def _err(request: web.Request, message: str, status: int = 400) -> web.Response:
    return web.json_response(
        {"success": False, "message": _msg(request, message)},
        status=status,
    )


def _require_jwt(request: web.Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, _err(request, "Unauthorized", 401)
    try:
        payload = decode_access_token(auth[7:])
        return payload, None
    except _jwt.ExpiredSignatureError:
        return None, _err(request, "Token expired", 401)
    except Exception:
        return None, _err(request, "Invalid token", 401)


def _resolve_dongle_serial(request: web.Request, user_id):
    """Resolve the target dongle_serial for a Modbus operation.

    Returns (dongle_serial, error_response). In multi-tenant mode the target is
    scoped to the logged-in user: an explicit ``inverter_id`` / ``dongle_serial``
    query parameter is validated for ownership, otherwise the user's first
    inverter is used.
    """
    dongle_serial = request.query.get("dongle_serial", "").strip()
    if not _USE_PG:
        return dongle_serial or None, None

    inverter_id_str = request.query.get("inverter_id", "").strip()
    session = next(get_db_session())
    try:
        if inverter_id_str:
            try:
                inverter_id = uuid.UUID(inverter_id_str)
            except ValueError:
                return None, _err(request, "Invalid inverter_id", 400)
            inverter = repo.get_inverter_by_id_and_user(session, inverter_id, uuid.UUID(str(user_id)))
            if inverter is None:
                return None, _err(request, "Inverter not found", 404)
            return inverter.dongle_serial, None
        if dongle_serial:
            inverter = repo.get_inverter_by_dongle_serial(session, dongle_serial)
            if inverter is None or str(inverter.user_id) != str(user_id):
                return None, _err(request, "Not authorized for this dongle", 403)
            return inverter.dongle_serial, None
        inverters = repo.get_inverters_by_user(session, uuid.UUID(str(user_id)))
        if not inverters:
            return None, _err(request, "No inverter registered", 404)
        return inverters[0].dongle_serial, None
    finally:
        session.close()


async def modbus_status(request: web.Request) -> web.Response:
    payload, auth_err = _require_jwt(request)
    if _USE_PG and auth_err is not None:
        return auth_err
    try:
        return web.json_response(modbus_controller.controller.status)
    except Exception as e:
        logger.error("Error in modbus_status: %s", e)
        return web.json_response(modbus_controller.controller.status)


async def modbus_registers_route(request: web.Request) -> web.Response:
    payload, auth_err = _require_jwt(request)
    if _USE_PG and auth_err is not None:
        return auth_err
    try:
        categories = []
        for category in modbus_registers.categories():
            items = [
                modbus_registers.public_item(item)
                for item in modbus_registers.get_items(category["key"])
            ]
            categories.append({
                "key": category["key"],
                "name": category.get("name", category["key"]),
                "items": items,
            })
        return web.json_response({
            "categories": categories,
            "status": modbus_controller.controller.status,
        })
    except Exception as e:
        logger.error("Error in modbus_registers: %s", e)
        return web.json_response({"categories": [], "message": str(e)}, status=500)


async def modbus_read(request: web.Request) -> web.Response:
    payload, auth_err = _require_jwt(request)
    if _USE_PG and auth_err is not None:
        return auth_err
    try:
        if not modbus_controller.controller.available:
            return web.json_response({
                "success": False,
                "message": "Modbus is not available in the current mode",
                "status": modbus_controller.controller.status,
            }, status=503)

        dongle_serial, resolve_err = _resolve_dongle_serial(request, payload["sub"] if payload else None)
        if resolve_err is not None:
            return resolve_err

        category = request.query.get("category")
        keys = request.query.get("keys")
        if keys:
            key_list = [k.strip() for k in keys.split(",") if k.strip()]
        elif category:
            key_list = [item["key"] for item in modbus_registers.get_items(category)]
        else:
            key_list = [item["key"] for item in modbus_registers.all_items()]
        values = await modbus_controller.controller.read_items(key_list, dongle_serial=dongle_serial)
        return web.json_response({"success": True, "values": values, "status": modbus_controller.controller.status})
    except Exception as e:
        # asyncio.wait_for (and dongle_server request_modbus) surface a BARE
        # asyncio.TimeoutError() whose str() is the empty string; the FE
        # would otherwise receive {"message": ""} with nothing to show.
        message = str(e) or "No response from Modbus dongle (timeout)"
        logger.error("Error in modbus_read: %s", message)
        return web.json_response({"success": False, "message": message}, status=500)


async def modbus_write(request: web.Request) -> web.Response:
    payload, auth_err = _require_jwt(request)
    if _USE_PG and auth_err is not None:
        return auth_err
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

        dongle_serial = None
        if _USE_PG:
            user_id = payload["sub"] if payload else None
            dongle_serial, resolve_err = _resolve_dongle_serial(request, user_id)
            if resolve_err is not None:
                return resolve_err
        else:
            dongle_serial = str(data.get("dongle_serial") or "").strip() or None

        new_value = await modbus_controller.controller.write_item(
            item, data.get("value"), confirm=bool(data.get("confirm")), dongle_serial=dongle_serial
        )
        return web.json_response({"success": True, "key": key, "value": new_value})
    except ValueError as e:
        logger.warning("Modbus write rejected: %s", e)
        return web.json_response({"success": False, "message": str(e)}, status=400)
    except Exception as e:
        message = str(e) or "Failed to write Modbus register (timeout)"
        logger.error("Error in modbus_write: %s", message)
        return web.json_response({"success": False, "message": message}, status=500)


MODBUS_ROUTES = [
    web.get("/modbus/status", modbus_status),
    web.get("/modbus/registers", modbus_registers_route),
    web.get("/modbus/read", modbus_read),
    web.post("/modbus/write", modbus_write),
]