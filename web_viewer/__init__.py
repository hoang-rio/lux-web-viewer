import asyncio
from datetime import datetime, timedelta
import sqlite3
import ssl
import threading
import ipaddress
import uuid
from aiohttp.aiohttp import web
from aiohttp import aiohttp
from os import environ, path
import json
from logging import Logger, getLogger
from dotenv import dotenv_values
from typing import List, Optional
from base64 import b64decode
from html import escape
from time import perf_counter
import jwt as pyjwt

from api_storage import read_grid_state, register_device_token
from web_viewer.routes_auth import AUTH_ROUTES
from web_viewer.routes_inverters import INVERTER_ROUTES
from multi_tenant.auth import decode_access_token
from multi_tenant.db import get_db_session
from multi_tenant import repository as mt_repo
from multi_tenant.i18n import get_locale_from_accept_language, translate
from sleep_cache import ALLOWED_SLEEP_TIME_SETTING_VALUES, clear_sleep_time_cache, set_enabled as _sleep_cache_set_enabled

# Load config from .env and environment
config: dict = {**dotenv_values(".env"), **environ}
USE_PG = bool(config.get("POSTGRES_DB_URL") or config.get("DATABASE_URL"))
# Configure centralized sleep cache to follow PG availability
try:
    _sleep_cache_set_enabled(USE_PG)
except Exception:
    pass

# Comma-separated CIDR list that are allowed to access admin features (settings + notification modification).
# Configured via `ADMIN_ALLOWED_CIDR` environment variable.
ADMIN_ALLOWED_CIDR = config.get("ADMIN_ALLOWED_CIDR", "")

logger: Logger = getLogger(__file__)

_db_conn: Optional[sqlite3.Connection] = None

def get_db_connection() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        db_name = config.get("DB_NAME")
        if not db_name:
            raise RuntimeError("DB_NAME not set in config")
        _db_conn = sqlite3.connect(db_name, check_same_thread=False)
    return _db_conn


def get_ssl_context() -> Optional[ssl.SSLContext]:
    """Create an SSLContext for HTTPS if configured.

    Expects the following env vars:
      - HTTPS_ENABLED (true/false)
      - HTTPS_CERT_FILE (path to PEM cert file)
      - HTTPS_KEY_FILE (path to PEM key file)
      - HTTPS_CERT_PASSWORD (optional password for key)
    """
    if config.get("HTTPS_ENABLED", "false").lower() != "true":
        return None

    cert_file = config.get("HTTPS_CERT_FILE")
    key_file = config.get("HTTPS_KEY_FILE")
    key_password = config.get("HTTPS_CERT_PASSWORD")

    if not cert_file or not key_file:
        logger.warning("HTTPS_ENABLED=true but HTTPS_CERT_FILE or HTTPS_KEY_FILE is not set; HTTPS will not start")
        return None

    if not path.exists(cert_file):
        logger.warning("HTTPS certificate file does not exist: %s; HTTPS will not start", cert_file)
        return None

    if not path.exists(key_file):
        logger.warning("HTTPS key file does not exist: %s; HTTPS will not start", key_file)
        return None

    try:
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(certfile=cert_file, keyfile=key_file, password=key_password)
        logger.info("HTTPS enabled using cert=%s key=%s", cert_file, key_file)
        return ctx
    except Exception as e:
        logger.error("Failed to create SSL context for HTTPS: %s", e)
        return None


def dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _peer_ip_from_transport(transport) -> Optional[str]:
    if not transport:
        return None
    peer = transport.get_extra_info("peername")
    if not peer:
        return None
    # peer may be (ip, port) or (ip, port, flowinfo, scopeid)
    try:
        return peer[0]
    except Exception:
        return None


def _ip_in_cidrs(ip: str | None, cidr_list: str) -> bool:
    """Return True if ip is contained in any CIDR in cidr_list (comma-separated)."""
    if not ip:
        return False
    if not cidr_list:
        return False
    import ipaddress

    try:
        ip_addr = ipaddress.ip_address(ip)
    except Exception:
        return False

    for cidr in [c.strip() for c in str(cidr_list).split(",") if c.strip()]:
        try:
            if ip_addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except Exception:
            logger.warning("Invalid ADMIN_ALLOWED_CIDR entry: %s", cidr)
    return False


def _deny_if_not_allowed_cidr(request: web.Request, path_prefix: str, allowed_methods: tuple = ("OPTIONS",), web_only: bool = False, response_body=None):
    """Return a web.Response denying access (403) when request matches path_prefix and method is not allowed and remote IP not in ADMIN_ALLOWED_CIDR.

    If web_only is True, enforcement only runs when request likely originates from a browser (has Origin or Referer header).
    """
    if not request.path.startswith(path_prefix):
        return None
    # Allow allowed methods through
    if request.method.upper() in (m.upper() for m in allowed_methods):
        return None
    # If enforcement only for web clients, only apply when Origin or Referer header present
    if web_only:
        if not (request.headers.get("Origin") or request.headers.get("Referer")):
            return None
    peer = request.transport
    remote_ip = _peer_ip_from_transport(peer)
    if _ip_in_cidrs(remote_ip, ADMIN_ALLOWED_CIDR):
        return None
    body = response_body
    if body is None:
        body = {"success": False, "message": _lmsg(request, "Forbidden")}
    return web.json_response(body, status=403)


def _extract_jwt_user_id(request: web.Request) -> Optional[uuid.UUID]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return _extract_jwt_user_id_from_token(auth[7:])


def _extract_jwt_user_id_from_token(token: str) -> Optional[uuid.UUID]:
    if not token:
        return None
    try:
        payload = decode_access_token(token)
        return uuid.UUID(str(payload.get("sub")))
    except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError, ValueError, TypeError):
        return None
    except Exception:
        return None


def _lmsg(request: web.Request, message: str) -> str:
    locale = get_locale_from_accept_language(request.headers.get("Accept-Language"))
    return translate(message, locale)


def _jwt_error_response(request: web.Request, message: str) -> web.Response:
    return web.json_response(
        {"success": False, "message": _lmsg(request, message)},
        status=401,
    )


def _require_jwt_user_id(request: web.Request) -> tuple[Optional[uuid.UUID], Optional[web.Response]]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, _jwt_error_response(request, "Unauthorized")

    token = auth[7:]
    try:
        payload = decode_access_token(token)
        return uuid.UUID(str(payload.get("sub"))), None
    except pyjwt.ExpiredSignatureError:
        return None, _jwt_error_response(request, "Token expired")
    except (pyjwt.InvalidTokenError, ValueError, TypeError):
        return None, _jwt_error_response(request, "Invalid token")
    except Exception:
        return None, _jwt_error_response(request, "Invalid token")


def _resolve_request_inverter(session, user_id: uuid.UUID, request: web.Request):
    inverter_id_str = request.rel_url.query.get("inverter_id", "").strip()
    if inverter_id_str:
        try:
            inverter_id = uuid.UUID(inverter_id_str)
        except ValueError:
            return None
        return mt_repo.get_inverter_by_id_and_user(session, inverter_id, user_id)

    user_inverters = mt_repo.get_inverters_by_user(session, user_id)
    if not user_inverters:
        return None
    return user_inverters[0]

# SSE/state cache
sse_clients: List[dict] = []
last_inverter_data: str = '{"inverter_data": {}}'
last_inverter_data_by_id: dict[str, dict] = {}

async def http_handler(_: web.Request):
    index_file_path = path.join(path.dirname(__file__), 'build', 'index.html')
    return web.FileResponse(index_file_path, headers={"expires": "0", "cache-control": "no-cache"})

async def sse_handler(request):
    global sse_clients
    sse_started_at = perf_counter()
    requested_inverter_id = request.rel_url.query.get("inverter_id", "").strip() or None
    initial_event_data: Optional[str] = None
    logger.debug(
        "SSE start path=%s inverter_id=%s use_pg=%s",
        request.path_qs,
        requested_inverter_id,
        USE_PG,
    )

    user_id = None
    if USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error

    if USE_PG and user_id is not None and not requested_inverter_id:
        return web.json_response(
            {"success": False, "message": _lmsg(request, "inverter_id is required")},
            status=400,
        )

    allowed_inverter_ids: set[str] | None = None
    if USE_PG and user_id is not None:
        db_started_at = perf_counter()
        try:
            session = next(get_db_session())
            try:
                user_inverters = mt_repo.get_inverters_by_user(session, user_id)
                allowed_inverter_ids = {str(inv.id) for inv in user_inverters}
                if requested_inverter_id:
                    try:
                        latest = mt_repo.get_inverter_latest_state(session, uuid.UUID(requested_inverter_id))
                        if latest and isinstance(latest.payload, dict) and latest.payload:
                            # Ensure consistent structure with cached data - always wrap in inverter_data root key
                            if "inverter_data" in latest.payload:
                                initial_event_data = json.dumps(latest.payload)
                            else:
                                initial_event_data = json.dumps({"inverter_data": latest.payload})
                    except Exception:
                        # Snapshot is optional for SSE bootstrap; ignore parse/query errors.
                        pass
            finally:
                session.close()
            logger.debug(
                "SSE scope resolved inverter_count=%s has_initial_snapshot=%s db_ms=%.1f total_ms=%.1f",
                len(allowed_inverter_ids),
                bool(initial_event_data),
                (perf_counter() - db_started_at) * 1000,
                (perf_counter() - sse_started_at) * 1000,
            )
        except Exception as exc:
            logger.error("Failed to resolve SSE user inverter scope: %s", exc)
            return web.json_response(
                {"success": False, "message": _lmsg(request, "Failed to resolve inverter scope")},
                status=500,
            )

    if requested_inverter_id and allowed_inverter_ids is not None and requested_inverter_id not in allowed_inverter_ids:
        return web.json_response(
            {"success": False, "message": _lmsg(request, "Forbidden inverter scope")},
            status=403,
        )

    response = web.StreamResponse()
    response.headers['Content-Type'] = 'text/event-stream'
    response.headers['Cache-Control'] = 'no-cache'
    # Disable reverse-proxy buffering so first SSE bytes are flushed immediately.
    response.headers['X-Accel-Buffering'] = 'no'
    await response.prepare(request)
    logger.debug("SSE prepared total_ms=%.1f", (perf_counter() - sse_started_at) * 1000)

    if not initial_event_data:
        if requested_inverter_id:
            cached_payload = last_inverter_data_by_id.get(requested_inverter_id)
            if isinstance(cached_payload, dict) and cached_payload:
                initial_event_data = json.dumps({"inverter_data": cached_payload})
        elif not USE_PG:
            # For non-SaaS/non-PG, bootstrap UI with all currently cached states
            for payload in last_inverter_data_by_id.values():
                try:
                    await response.write(f"data: {json.dumps({'inverter_data': payload})}\n\n".encode('utf-8'))
                except Exception:
                    pass
            # Also check the legacy single-item cache
            if last_inverter_data and last_inverter_data != '{"inverter_data": {}}':
                initial_event_data = last_inverter_data

    sse_client = {
        "response": response,
        "inverter_id": requested_inverter_id,
        "allowed_inverter_ids": allowed_inverter_ids,
    }
    sse_clients.append(sse_client)
    logger.debug(f"SSE_CLIENTS count: {len(sse_clients)}")
    try:
        if initial_event_data:
            await response.write(f"data: {initial_event_data}\n\n".encode('utf-8'))
            logger.debug(
                "SSE sent initial snapshot bytes=%s total_ms=%.1f",
                len(initial_event_data),
                (perf_counter() - sse_started_at) * 1000,
            )
        # Keep the connection open with keep-alive
        await response.write(b': keep-alive\n\n')
        logger.debug("SSE first keep-alive sent total_ms=%.1f", (perf_counter() - sse_started_at) * 1000)
        # Keep the connection alive indefinitely
        # The connection will be closed when client disconnects or server shuts down
        while True:
            await asyncio.sleep(30)  # Send keep-alive every 30 seconds
            try:
                await response.write(b': keep-alive\n\n')
            except Exception:
                # Client disconnected
                break
    except Exception as e:
        logger.error(f"SSE connection error: {e}")
    finally:
        for client in sse_clients[:]:
            if client.get("response") is response:
                sse_clients.remove(client)
        logger.debug(
            "SSE closed inverter_id=%s lifetime_ms=%.1f remaining_clients=%s",
            requested_inverter_id,
            (perf_counter() - sse_started_at) * 1000,
            len(sse_clients),
        )
    return response

async def broadcast_sse(data: str):
    global sse_clients
    inverter_id = None
    try:
        payload = json.loads(data)
        if "inverter_data" in payload:
            inverter_payload = payload.get("inverter_data") or {}
            inverter_id = inverter_payload.get("_inverter_id")
            if not inverter_id:
                inverter_id = inverter_payload.get("serial") or inverter_payload.get("dongle_serial")
        elif payload.get("event") == "new_notification":
            inverter_id = payload.get("data", {}).get("inverter_id")

        if inverter_id:
            inverter_id = str(inverter_id)
    except Exception:
        inverter_id = None

    for client in sse_clients[:]:
        response = client.get("response")
        if response is None:
            continue
        scoped_inverter_id = client.get("inverter_id")
        allowed_inverter_ids = client.get("allowed_inverter_ids")

        if scoped_inverter_id and scoped_inverter_id != inverter_id:
            continue
        if allowed_inverter_ids is not None and inverter_id not in allowed_inverter_ids:
            continue

        try:
            await response.write(f"data: {data}\n\n".encode('utf-8'))
        except Exception as e:
            logger.error(f"Error sending to SSE client: {e}")
            if client in sse_clients:
                sse_clients.remove(client)

async def websocket_handler(request):
    global last_inverter_data
    global last_inverter_data_by_id
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    try:
        async for msg in ws:
            logger.debug("[WS Server] received message")
            logger.debug(msg)
            if msg.type == aiohttp.WSMsgType.TEXT:
                if "inverter_data" in msg.data:
                    last_inverter_data = msg.data
                    try:
                        parsed = json.loads(msg.data)
                        inverter_payload = parsed.get("inverter_data") or {}
                        inverter_id = inverter_payload.get("_inverter_id")
                        if not inverter_id:
                            # Fallback to serial or dongle_serial for non-SaaS or identification
                            inverter_id = inverter_payload.get("serial") or inverter_payload.get("dongle_serial")
                        
                        if inverter_id:
                            last_inverter_data_by_id[str(inverter_id)] = inverter_payload
                    except Exception:
                        pass
                await broadcast_sse(msg.data)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error(f"WS connection closed with exception {ws.exception()}")
    finally:
        pass
    return ws

async def state(request: web.Request):
    global last_inverter_data
    global last_inverter_data_by_id
    user_id = None
    if USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error

    requested_inverter_id = request.rel_url.query.get("inverter_id", "").strip()

    if USE_PG and user_id is not None and not requested_inverter_id:
        return web.json_response(
            {"success": False, "message": _lmsg(request, "inverter_id is required")},
            status=400,
        )

    if user_id is not None:
        try:
            session = next(get_db_session())
            try:
                inverter = _resolve_request_inverter(session, user_id, request)
                if inverter is None:
                    return web.json_response({})
                
                # Try cache first
                inverter_id_str = str(inverter.id)
                if inverter_id_str in last_inverter_data_by_id:
                    return web.json_response(last_inverter_data_by_id[inverter_id_str])
                
                latest = mt_repo.get_inverter_latest_state(session, inverter.id)
                payload = latest.payload if latest and latest.payload else {}
                return web.json_response(payload)
            finally:
                session.close()
        except Exception as error:
            logger.error("Error in state (multi-tenant): %s", error)

    # Fallback for non-SaaS/non-PG mode
    serial = request.rel_url.query.get('serial')
    try:
        if requested_inverter_id:
            data = last_inverter_data_by_id.get(requested_inverter_id, {})
        elif serial and serial in last_inverter_data_by_id:
            data = last_inverter_data_by_id.get(serial, {})
        elif last_inverter_data_by_id and not requested_inverter_id:
            # Return first available as default for legacy dashboard
            data = next(iter(last_inverter_data_by_id.values()))
        else:
            data = json.loads(last_inverter_data)["inverter_data"]
    except Exception:
        data = {}
    return web.json_response(data)


async def mobile_state(request: web.Request):
    try:
        if USE_PG:
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
            state_file = config.get("STATE_FILE", "grid_connect_state.ini")
            history_file = config.get("HISTORY_FILE", "history.json")
            return web.json_response(
                read_grid_state(state_file, history_file),
            )
    except Exception as error:
        logger.error(f"Error in mobile_state: {error}")
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

        if USE_PG:
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
            config.get("DEVICE_IDS_JSON_FILE", "devices.json"),
            token,
        )
        status = 200 if result["is_success"] else 400
        return web.json_response(result, status=status)
    except Exception as error:
        logger.error(f"Error in register_fcm: {error}")
        return web.json_response(
            {
                "is_success": False,
                "message": f"Got exception {error}",
                "device_count": 0,
            },
            status=500,
        )



async def hourly_chart(request: web.Request):
    user_id = None
    if USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error

    if user_id is not None:
        try:
            date_str = request.rel_url.query.get('date')
            if date_str:
                try:
                    query_date = datetime.strptime(date_str, "%Y-%m-%d")
                except Exception:
                    query_date = datetime.now()
            else:
                query_date = datetime.now()

            session = next(get_db_session())
            try:
                inverter = _resolve_request_inverter(session, user_id, request)
                if inverter is None:
                    return web.json_response([])
                rows = mt_repo.get_hourly_chart(session, inverter.id, query_date.date())
                chart = [
                    [
                        f"{inverter.id}:{r.datetime.strftime('%Y%m%d%H')}",
                        r.datetime.strftime("%Y-%m-%d %H:%M:%S"),
                        r.pv,
                        r.battery,
                        r.grid,
                        r.consumption,
                        r.soc,
                    ]
                    for r in rows
                ]
                return web.json_response(chart)
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error in hourly_chart (multi-tenant): {e}")

    if USE_PG:
        return web.json_response(
            {"success": False, "message": _lmsg(request, "Failed to load hourly chart")},
            status=500,
        )

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        date_str = request.rel_url.query.get('date')
        if date_str:
            try:
                query_date = datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                query_date = datetime.now()
        else:
            query_date = datetime.now()
        start_of_day = query_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)
        hourly_chart = cursor.execute(
            "SELECT * FROM hourly_chart WHERE datetime >= ? AND datetime < ?",
            (start_of_day.strftime("%Y-%m-%d %H:%M:%S"), end_of_day.strftime("%Y-%m-%d %H:%M:%S"))
        ).fetchall()
        return web.json_response(hourly_chart)
    except Exception as e:
        logger.error(f"Error in hourly_chart: {e}")
        return web.json_response([])

async def total(request: web.Request):
    user_id = None
    if USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error

    if user_id is not None:
        try:
            session = next(get_db_session())
            try:
                inverter = _resolve_request_inverter(session, user_id, request)
                if inverter is None:
                    return web.json_response({})
                totals = mt_repo.get_total(session, inverter.id)
                return web.json_response(totals)
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error in total (multi-tenant): {e}")

    if USE_PG:
        return web.json_response(
            {"success": False, "message": _lmsg(request, "Failed to load total")},
            status=500,
        )

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.row_factory = dict_factory
        total = cursor.execute(
            "SELECT SUM(pv) as pv, SUM(battery_charged) as battery_charged, SUM(battery_discharged) as battery_discharged, SUM(grid_import) as grid_import, SUM(grid_export) as grid_export, SUM(consumption) as consumption FROM daily_chart"
        ).fetchone()
        return web.json_response(total)
    except Exception as e:
        logger.error(f"Error in total: {e}")
        return web.json_response({})

async def daily_chart(request: web.Request):
    user_id = None
    if USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error

    if user_id is not None:
        try:
            request = request
            month_str = request.rel_url.query.get('month')
            if month_str:
                try:
                    year, month = map(int, month_str.split('-'))
                    query_date = datetime(year, month, 1)
                except Exception:
                    query_date = datetime.now()
                    year = query_date.year
                    month = query_date.month
            else:
                query_date = datetime.now()
                year = query_date.year
                month = query_date.month

            session = next(get_db_session())
            try:
                inverter = _resolve_request_inverter(session, user_id, request)
                if inverter is None:
                    return web.json_response([])

                rows = mt_repo.get_daily_chart(session, inverter.id, year, month)
                daily_chart = [
                    (
                        f"{inverter.id}:{r.date.strftime('%Y%m%d')}",
                        r.year,
                        r.month,
                        r.date.strftime("%Y-%m-%d"),
                        float(r.pv) if r.pv is not None else 0,
                        float(r.battery_charged) if r.battery_charged is not None else 0,
                        float(r.battery_discharged) if r.battery_discharged is not None else 0,
                        float(r.grid_import) if r.grid_import is not None else 0,
                        float(r.grid_export) if r.grid_export is not None else 0,
                        float(r.consumption) if r.consumption is not None else 0,
                        ""
                    )
                    for r in rows
                ]

                if daily_chart:
                    last_day_of_month = (query_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
                    last_item_date = datetime.strptime(daily_chart[-1][3], "%Y-%m-%d")
                    while last_item_date < last_day_of_month:
                        last_item_date += timedelta(days=1)
                        daily_chart.append((
                            f"{inverter.id}:{last_item_date.strftime('%Y%m%d')}",
                            last_item_date.year,
                            last_item_date.month,
                            last_item_date.strftime("%Y-%m-%d"),
                            0, 0, 0, 0, 0, 0, ""
                        ))
                return web.json_response(daily_chart)
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error in daily_chart (multi-tenant): {e}")

    if USE_PG:
        return web.json_response(
            {"success": False, "message": _lmsg(request, "Failed to load daily chart")},
            status=500,
        )

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        request = request
        month_str = request.rel_url.query.get('month')
        if month_str:
            # Expect format YYYY-MM
            try:
                year, month = map(int, month_str.split('-'))
                query_date = datetime(year, month, 1)
            except Exception:
                query_date = datetime.now()
                year = query_date.year
                month = query_date.month
        else:
            query_date = datetime.now()
            year = query_date.year
            month = query_date.month
        daily_chart = cursor.execute(
            "SELECT * FROM daily_chart WHERE year = ? AND month = ?",
            (year, month)
        ).fetchall()
        # Fill empty data to daily_chart from last item to last day of month
        if daily_chart:
            last_day_of_month = (query_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            last_item_date = datetime.strptime(daily_chart[-1][3], "%Y-%m-%d")
            while last_item_date < last_day_of_month:
                last_item_date += timedelta(days=1)
                daily_chart.append((
                    last_item_date.strftime("%Y%m%d"),
                    last_item_date.year,
                    last_item_date.month,
                    last_item_date.strftime("%Y-%m-%d"),
                    0, 0, 0, 0, 0, 0, ""
                ))
        return web.json_response(daily_chart)
    except Exception as e:
        logger.error(f"Error in daily_chart: {e}")
        return web.json_response([])

async def monthly_chart(request: web.Request):
    user_id = None
    if USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error

    if user_id is not None:
        try:
            now = datetime.now()
            year_param = request.rel_url.query.get("year")
            try:
                year = int(year_param) if year_param else now.year
            except Exception:
                year = now.year

            session = next(get_db_session())
            try:
                inverter = _resolve_request_inverter(session, user_id, request)
                if inverter is None:
                    return web.json_response({"chart": [], "years": []})

                rows = mt_repo.get_monthly_chart(session, inverter.id, year)
                chart = [
                    (
                        f"{inverter.id}:{year}{int(r['month']):02d}",
                        f"{inverter.id}:{year}{int(r['month']):02d}",
                        f"{inverter.id}:{year}{int(r['month']):02d}",
                        f"{int(r['month'])}/{year}",
                        r.get("pv") or 0,
                        r.get("battery_charged") or 0,
                        r.get("battery_discharged") or 0,
                        r.get("grid_import") or 0,
                        r.get("grid_export") or 0,
                        r.get("consumption") or 0,
                    )
                    for r in rows
                ]
                years = mt_repo.get_available_years(session, inverter.id)
                return web.json_response({"chart": chart, "years": years})
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error in monthly_chart (multi-tenant): {e}")

    if USE_PG:
        return web.json_response(
            {"success": False, "message": _lmsg(request, "Failed to load monthly chart")},
            status=500,
        )

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        now = datetime.now()
        year_param = request.rel_url.query.get("year")
        try:
            year = int(year_param) if year_param else now.year
        except Exception:
            year = now.year

        monthly_chart = cursor.execute(
            "SELECT id, id, id, month || '/' || year as month, SUM(pv), SUM(battery_charged), SUM(battery_discharged), SUM(grid_import), SUM(grid_export), SUM(consumption) FROM daily_chart WHERE year = ? GROUP BY month",
            (year,)
        ).fetchall()

        # Provide available years for UI selection
        years_rows = cursor.execute("SELECT DISTINCT year FROM daily_chart ORDER BY year DESC").fetchall()
        years = [int(r[0]) for r in years_rows]

        return web.json_response({"chart": monthly_chart, "years": years})
    except Exception as e:
        logger.error(f"Error in monthly_chart: {e}")
        return web.json_response({"chart": [], "years": []})

async def yearly_chart(request: web.Request):
    user_id = None
    if USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error

    if user_id is not None:
        try:
            session = next(get_db_session())
            try:
                inverter = _resolve_request_inverter(session, user_id, request)
                if inverter is None:
                    return web.json_response([])

                rows = mt_repo.get_yearly_chart(session, inverter.id)
                chart = [
                    (
                        f"{inverter.id}:{int(r['year'])}",
                        f"{inverter.id}:{int(r['year'])}",
                        f"{inverter.id}:{int(r['year'])}",
                        str(int(r["year"])),
                        r.get("pv") or 0,
                        r.get("battery_charged") or 0,
                        r.get("battery_discharged") or 0,
                        r.get("grid_import") or 0,
                        r.get("grid_export") or 0,
                        r.get("consumption") or 0,
                    )
                    for r in rows
                ]
                return web.json_response(chart)
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error in yearly_chart (multi-tenant): {e}")

    if USE_PG:
        return web.json_response(
            {"success": False, "message": _lmsg(request, "Failed to load yearly chart")},
            status=500,
        )

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        yearly_chart = cursor.execute(
            "SELECT id, id, id, year || '' as year, SUM(pv), SUM(battery_charged), SUM(battery_discharged), SUM(grid_import), SUM(grid_export), SUM(consumption) FROM daily_chart GROUP BY year"
        ).fetchall()
        return web.json_response(yearly_chart)
    except Exception as e:
        logger.error(f"Error in yearly_chart: {e}")
        return web.json_response([])

async def notification_history(request: web.Request):
    if USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error
        try:
            session = next(get_db_session())
            try:
                notifications = mt_repo.get_notification_history(session, user_id)
                data = [
                    {
                        "id": row.id,
                        "title": row.title,
                        "body": row.body,
                        "notified_at": row.notified_at.strftime("%Y-%m-%d %H:%M:%S"),
                        "read": 1 if row.read else 0,
                        "inverter_id": str(row.inverter_id) if row.inverter_id else None,
                    }
                    for row in notifications
                ]
                return web.json_response({"notifications": data})
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error in notification_history (multi-tenant): {e}")
            return web.json_response({"notifications": []})

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        notifications = cursor.execute(
            "SELECT id, title, body, notified_at, read FROM notification_history ORDER BY notified_at DESC"
        ).fetchall()
        data = [
            {"id": row[0], "title": row[1], "body": row[2], "notified_at": row[3], "read": row[4]}
            for row in notifications
        ]
        return web.json_response({"notifications": data})
    except Exception as e:
        logger.error(f"Error in notification_history: {e}")
        return web.json_response({"notifications": []})

async def mark_notifications_read(request: web.Request):
    if USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error
        try:
            session = next(get_db_session())
            try:
                mt_repo.mark_notifications_read(session, user_id)
                session.commit()
                return web.json_response({"success": True})
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error in mark_notifications_read (multi-tenant): {e}")
            return web.json_response({"success": False})

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE notification_history SET read = 1 WHERE read = 0")
        conn.commit()
        return web.json_response({"success": True})
    except Exception as e:
        logger.error(f"Error in mark_notifications_read: {e}")
        return web.json_response({"success": False})

async def notification_unread_count(request: web.Request):
    if USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error
        try:
            session = next(get_db_session())
            try:
                unread_count = mt_repo.get_unread_notification_count(session, user_id)
                return web.json_response({"unread_count": unread_count})
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error in notification_unread_count (multi-tenant): {e}")
            return web.json_response({"unread_count": 0})

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        unread_count = cursor.execute("SELECT COUNT(*) FROM notification_history WHERE read = 0").fetchone()[0]
        return web.json_response({"unread_count": unread_count})
    except Exception as e:
        logger.error(f"Error in notification_unread_count: {e}")
        return web.json_response({"unread_count": 0})

async def get_settings(request: web.Request):
    if USE_PG:
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
            logger.error(f"Error in get_settings (multi-tenant): {e}")
            return web.json_response({})

    try:
        import settings
        merged = dict(settings.settings)
        merged["SLEEP_TIME"] = str(settings.get_sleep_time())
        return web.json_response(merged)
    except Exception as e:
        logger.error(f"Error in get_settings: {e}")
        return web.json_response({})

async def update_settings(request: web.Request):
    if USE_PG:
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
            logger.error(f"Error in update_settings (multi-tenant): {e}")
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
        logger.error(f"Error in update_settings: {e}")
        return web.json_response({"success": False})

def _auth_html_response(status: int, title: str, message: str, include_www_authenticate: bool = False) -> web.Response:
        """Return a small friendly HTML response for auth errors."""
        safe_title = escape(title)
        safe_message = escape(message)
        body = f"""
        <!doctype html>
        <html>
            <head>
                <meta charset="utf-8" />
                <meta name="viewport" content="width=device-width,initial-scale=1" />
                <title>{safe_title}</title>
                <style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial;line-height:1.4;margin:24px;color:#222}}h2{{margin-top:0}}.hint{{color:#666;font-size:0.9em}}</style>
            </head>
            <body>
                <h2>{safe_title}</h2>
                <p>{safe_message}</p>
                <p class="hint">If you are the device owner you can update authentication in the <strong>Settings</strong> panel of this web viewer.</p>
            </body>
        </html>
        """
        headers = {}
        if include_www_authenticate:
            headers["WWW-Authenticate"] = "Basic realm='Authenticate_Lux_Web_Viewer'"
        return web.Response(status=status, text=body, content_type='text/html', headers=headers)

# --- Basic Auth Middleware (reads auth settings dynamically) ---
@web.middleware
async def basic_auth_middleware(request, handler):
    # Allow access to /ws only if from loopback
    if request.path == "/ws":
        remote_ip = _peer_ip_from_transport(request.transport)
        if remote_ip in ("127.0.0.1", "::1"):
            return await handler(request)
        else:
            return _auth_html_response(403, "Forbidden", "Access to the websocket endpoint is restricted to localhost.")
    if USE_PG:
        # Multi-tenant mode uses JWT auth and user-scoped settings.
        return await handler(request)

    # Auth and inverter management endpoints use JWT; bypass HTTP Basic Auth for them.
    if request.path.startswith("/auth/") or request.path.startswith("/auth") or request.path.startswith("/inverters"):
        return await handler(request)
    # Read auth settings from persistent `settings` (updated by web UI).
    auth_bypass_cidr = config.get("AUTH_BYPASS_CIDR", "127.0.0.1/32,::1/128")
    try:
        import settings as app_settings
        auth_enabled = app_settings.get_setting("AUTH_ENABLED", config.get("AUTH_ENABLED", "false")).lower() == "true"
        auth_username = app_settings.get_setting("AUTH_USERNAME", config.get("AUTH_USERNAME", "admin"))
        auth_password = app_settings.get_setting("AUTH_PASSWORD", config.get("AUTH_PASSWORD", "changeme"))
        auth_bypass_cidr = app_settings.get_setting("AUTH_BYPASS_CIDR", auth_bypass_cidr)
    except Exception:
        # Fallback to env/config if settings module not available for any reason
        auth_enabled = config.get("AUTH_ENABLED", "false").lower() == "true"
        auth_username = config.get("AUTH_USERNAME", "admin")
        auth_password = config.get("AUTH_PASSWORD", "changeme")

    if not auth_enabled:
        return await handler(request)

    # If the remote IP is in bypass CIDR list and auth is on, allow.
    remote_ip = _peer_ip_from_transport(request.transport)
    if remote_ip and _ip_in_cidrs(remote_ip, auth_bypass_cidr):
        return await handler(request)
    if request.method == "OPTIONS":
        return await handler(request)
    # Enforce ADMIN_ALLOWED_CIDR for /settings (env-only configured CIDR)
    resp = _deny_if_not_allowed_cidr(request, "/settings", allowed_methods=("OPTIONS",), web_only=False)
    if resp:
        return resp

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Basic "):
        return _auth_html_response(401, "Authentication required", "This web viewer is protected. Please provide HTTP Basic credentials.", include_www_authenticate=True)
    try:
        encoded = auth_header.split(" ", 1)[1]
        decoded = b64decode(encoded).decode()
        username, password = decoded.split(":", 1)
    except Exception:
        return _auth_html_response(401, "Invalid authentication", "Invalid Authorization header. Please provide valid HTTP Basic credentials.", include_www_authenticate=True)
    if username != auth_username or password != auth_password:
        return _auth_html_response(401, "Invalid credentials", "The username or password you provided is incorrect.", include_www_authenticate=True)
    return await handler(request)

def create_runner():
    app = web.Application(middlewares=[basic_auth_middleware])
    app.add_routes([
        web.get("/", http_handler),
        web.get("/ws", websocket_handler),
        web.get("/events", sse_handler),
        web.get("/state", state),
        web.get("/mobile/state", mobile_state),
        web.post("/fcm/register", register_fcm),
        web.get("/hourly-chart", hourly_chart),
        web.get("/daily-chart", daily_chart),
        web.get("/monthly-chart", monthly_chart),
        web.get("/yearly-chart", yearly_chart),
        web.get("/total", total),
        web.get("/notification-history", notification_history),
        web.post("/notification-mark-read", mark_notifications_read),
        web.get("/notification-unread-count", notification_unread_count),
        web.get("/settings", get_settings),
        web.post("/settings", update_settings),
        *AUTH_ROUTES,
        *INVERTER_ROUTES,
        web.static("/", path.join(path.dirname(__file__), "build"))
    ])
    return web.AppRunner(app, access_log=None)

async def start_server(host="127.0.0.1", port=1337, ssl_context: Optional[ssl.SSLContext] = None):
    runner = create_runner()
    logger.info(f"Start server on {host}:{port}")
    await runner.setup()
    site = web.TCPSite(runner, host, port, ssl_context=ssl_context)
    await site.start()

loop: Optional[asyncio.AbstractEventLoop] = None

def run_http_server():
    global loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    host = config.get("HOST", "127.0.0.1")
    http_port = int(config.get("PORT", 1337))
    ssl_context = get_ssl_context()

    # Start HTTP server (always on the configured PORT)
    loop.run_until_complete(start_server(host=host, port=http_port, ssl_context=None))

    # If HTTPS is enabled and we successfully created an SSL context, start an HTTPS listener on a configured port.
    https_port = None
    https_port_config = config.get("HTTPS_PORT")
    if ssl_context is not None:
        if https_port_config:
            try:
                https_port = int(https_port_config)
            except Exception:
                logger.error("Invalid HTTPS_PORT value '%s'; HTTPS listener will not start.", https_port_config)
                https_port = None

        if https_port is None:
            logger.info("HTTPS_ENABLED set but HTTPS_PORT is not configured; HTTPS listener will not start.")
        elif https_port == http_port:
            logger.warning("HTTPS_PORT is the same as PORT (%s); HTTPS will not start to avoid port conflict.", http_port)
            https_port = None
        else:
            loop.run_until_complete(start_server(host=host, port=https_port, ssl_context=ssl_context))

    # If HOST_IPV6 is configured, also bind to that address for whichever ports are in use.
    host_ipv6 = config.get("HOST_IPV6")
    if host_ipv6:
        try:
            ipv6_http_port = http_port
            loop.run_until_complete(start_server(host=host_ipv6, port=ipv6_http_port, ssl_context=None))

            if ssl_context is not None and https_port is not None:
                loop.run_until_complete(start_server(host=host_ipv6, port=https_port, ssl_context=ssl_context))
        except Exception as e:
            logger.error(f"Failed to start IPv6 server on {host_ipv6}:{http_port} - {e}")
    loop.run_forever()

class WebViewer(threading.Thread):
    def __init__(self, _logger: Logger):
        super().__init__()
        global logger
        logger = _logger

    def run(self) -> None:
        run_http_server()

    def stop(self) -> None:
        global loop
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)

if __name__ == "__main__":
    run_http_server()
