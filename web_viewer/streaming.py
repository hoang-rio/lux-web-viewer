import asyncio
import json
import uuid
from os import path
from time import perf_counter
from typing import List

from aiohttp import aiohttp
from aiohttp.aiohttp import web

from multi_tenant import repository as mt_repo
from multi_tenant.db import get_db_session

from . import config
from .security import _lmsg, _require_jwt_user_id, _resolve_request_inverter

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
    initial_event_data = None
    config.logger.debug(
        "SSE start path=%s inverter_id=%s use_pg=%s",
        request.path_qs,
        requested_inverter_id,
        config.USE_PG,
    )

    user_id = None
    if config.USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error

    if config.USE_PG and user_id is not None and not requested_inverter_id:
        return web.json_response(
            {"success": False, "message": _lmsg(request, "inverter_id is required")},
            status=400,
        )

    allowed_inverter_ids = None
    if config.USE_PG and user_id is not None:
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
            config.logger.debug(
                "SSE scope resolved inverter_count=%s has_initial_snapshot=%s db_ms=%.1f total_ms=%.1f",
                len(allowed_inverter_ids),
                bool(initial_event_data),
                (perf_counter() - db_started_at) * 1000,
                (perf_counter() - sse_started_at) * 1000,
            )
        except Exception as exc:
            config.logger.error("Failed to resolve SSE user inverter scope: %s", exc)
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
    config.logger.debug("SSE prepared total_ms=%.1f", (perf_counter() - sse_started_at) * 1000)

    if not initial_event_data:
        if requested_inverter_id:
            cached_payload = last_inverter_data_by_id.get(requested_inverter_id)
            if isinstance(cached_payload, dict) and cached_payload:
                initial_event_data = json.dumps({"inverter_data": cached_payload})
        elif not config.USE_PG:
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
    config.logger.debug(f"SSE_CLIENTS count: {len(sse_clients)}")
    try:
        if initial_event_data:
            await response.write(f"data: {initial_event_data}\n\n".encode('utf-8'))
            config.logger.debug(
                "SSE sent initial snapshot bytes=%s total_ms=%.1f",
                len(initial_event_data),
                (perf_counter() - sse_started_at) * 1000,
            )
        # Keep the connection open with keep-alive
        await response.write(b': keep-alive\n\n')
        config.logger.debug("SSE first keep-alive sent total_ms=%.1f", (perf_counter() - sse_started_at) * 1000)
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
        config.logger.error(f"SSE connection error: {e}")
    finally:
        for client in sse_clients[:]:
            if client.get("response") is response:
                sse_clients.remove(client)
        config.logger.debug(
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
            config.logger.error(f"Error sending to SSE client: {e}")
            if client in sse_clients:
                sse_clients.remove(client)


async def websocket_handler(request):
    global last_inverter_data
    global last_inverter_data_by_id
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    try:
        async for msg in ws:
            config.logger.debug("[WS Server] received message")
            config.logger.debug(msg)
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
                config.logger.error(f"WS connection closed with exception {ws.exception()}")
    finally:
        pass
    return ws


async def state(request: web.Request):
    global last_inverter_data
    global last_inverter_data_by_id
    user_id = None
    if config.USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error

    requested_inverter_id = request.rel_url.query.get("inverter_id", "").strip()

    if config.USE_PG and user_id is not None and not requested_inverter_id:
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
            config.logger.error("Error in state (multi-tenant): %s", error)

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