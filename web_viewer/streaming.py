import asyncio
import json
from os import path
from typing import List

from aiohttp import aiohttp
from aiohttp.aiohttp import web

from . import config

# SSE state
sse_clients: List[web.StreamResponse] = []
last_inverter_data: str = '{"inverter_data": {}}'


async def http_handler(_: web.Request):
    index_file_path = path.join(path.dirname(__file__), 'build', 'index.html')
    return web.FileResponse(index_file_path, headers={"expires": "0", "cache-control": "no-cache"})


async def sse_handler(request):
    global sse_clients
    response = web.StreamResponse()
    response.headers['Content-Type'] = 'text/event-stream'
    response.headers['Cache-Control'] = 'no-cache'
    await response.prepare(request)
    sse_clients.append(response)
    config.logger.debug(f"SSE_CLIENTS count: {len(sse_clients)}")
    try:
        # Keep the connection open with keep-alive
        await response.write(b': keep-alive\n\n')
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
        if response in sse_clients:
            sse_clients.remove(response)
    return response


async def broadcast_sse(data: str):
    global sse_clients
    for client in sse_clients[:]:
        try:
            await client.write(f"data: {data}\n\n".encode('utf-8'))
        except Exception as e:
            config.logger.error(f"Error sending to SSE client: {e}")
            if client in sse_clients:
                sse_clients.remove(client)


async def websocket_handler(request):
    global last_inverter_data
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    try:
        async for msg in ws:
            config.logger.debug("[WS Server] received message")
            config.logger.debug(msg)
            if msg.type == aiohttp.WSMsgType.TEXT:
                if "inverter_data" in msg.data:
                    last_inverter_data = msg.data
                await broadcast_sse(msg.data)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                config.logger.error(f"WS connection closed with exception {ws.exception()}")
    finally:
        pass
    return ws


async def state(_: web.Request):
    global last_inverter_data
    try:
        data = json.loads(last_inverter_data)["inverter_data"]
    except Exception:
        data = {}
    return web.json_response(data)