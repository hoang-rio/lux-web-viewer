import asyncio
import ssl
import threading
from logging import Logger
from os import path
from typing import Optional

from aiohttp.aiohttp import web

from multi_tenant.auth import decode_access_token
from web_viewer.routes_auth import AUTH_ROUTES
from web_viewer.routes_inverters import INVERTER_ROUTES
from web_viewer.routes_modbus import MODBUS_ROUTES

from . import charts
from . import config
from . import db
from . import mobile
from . import notifications
from . import security
from . import settings_routes
from . import streaming
from .charts import daily_chart, hourly_chart, monthly_chart, total, yearly_chart
from .config import USE_PG
from .db import dict_factory
from .mobile import mobile_state, register_fcm
from .notifications import (
    delete_notification,
    mark_notifications_read,
    notification_history,
    notification_unread_count,
)
from .settings_routes import get_settings, update_settings
from .streaming import (
    broadcast_sse,
    http_handler,
    last_inverter_data,
    last_inverter_data_by_id,
    sse_clients,
    sse_handler,
    state,
    websocket_handler,
)


def create_runner():
    app = web.Application(middlewares=[security.basic_auth_middleware])
    app.add_routes([
        web.get("/", streaming.http_handler),
        web.get("/ws", streaming.websocket_handler),
        web.get("/events", streaming.sse_handler),
        web.get("/state", streaming.state),
        web.get("/mobile/state", mobile.mobile_state),
        web.post("/fcm/register", mobile.register_fcm),
        web.get("/hourly-chart", charts.hourly_chart),
        web.get("/daily-chart", charts.daily_chart),
        web.get("/monthly-chart", charts.monthly_chart),
        web.get("/yearly-chart", charts.yearly_chart),
        web.get("/total", charts.total),
        web.get("/notification-history", notifications.notification_history),
        web.post("/notification-mark-read", notifications.mark_notifications_read),
        web.get("/notification-unread-count", notifications.notification_unread_count),
        web.delete("/notification/{id}", notifications.delete_notification),
        web.get("/settings", settings_routes.get_settings),
        web.post("/settings", settings_routes.update_settings),
        *AUTH_ROUTES,
        *INVERTER_ROUTES,
        *MODBUS_ROUTES,
        web.static("/", path.join(path.dirname(__file__), "build"))
    ])
    return web.AppRunner(app, access_log=None)


async def start_server(host="127.0.0.1", port=1337, ssl_context: Optional[ssl.SSLContext] = None):
    runner = create_runner()
    config.logger.info(f"Start server on {host}:{port}")
    await runner.setup()
    site = web.TCPSite(runner, host, port, ssl_context=ssl_context)
    await site.start()


loop: Optional[asyncio.AbstractEventLoop] = None


def run_http_server():
    global loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    host = config.config.get("HOST", "127.0.0.1")
    http_port = int(config.config.get("PORT", 1337))
    ssl_context = security.get_ssl_context()

    # Start HTTP server (always on the configured PORT)
    loop.run_until_complete(start_server(host=host, port=http_port, ssl_context=None))

    # If HTTPS is enabled and we successfully created an SSL context, start an HTTPS listener on a configured port.
    https_port = None
    https_port_config = config.config.get("HTTPS_PORT")
    if ssl_context is not None:
        if https_port_config:
            try:
                https_port = int(https_port_config)
            except Exception:
                config.logger.error("Invalid HTTPS_PORT value '%s'; HTTPS listener will not start.", https_port_config)
                https_port = None

        if https_port is None:
            config.logger.info("HTTPS_ENABLED set but HTTPS_PORT is not configured; HTTPS listener will not start.")
        elif https_port == http_port:
            config.logger.warning("HTTPS_PORT is the same as PORT (%s); HTTPS will not start to avoid port conflict.", http_port)
            https_port = None
        else:
            loop.run_until_complete(start_server(host=host, port=https_port, ssl_context=ssl_context))

    # If HOST_IPV6 is configured, also bind to that address for whichever ports are in use.
    host_ipv6 = config.config.get("HOST_IPV6")
    if host_ipv6:
        try:
            ipv6_http_port = http_port
            loop.run_until_complete(start_server(host=host_ipv6, port=ipv6_http_port, ssl_context=None))

            if ssl_context is not None and https_port is not None:
                loop.run_until_complete(start_server(host=host_ipv6, port=https_port, ssl_context=ssl_context))
        except Exception as e:
            config.logger.error(f"Failed to start IPv6 server on {host_ipv6}:{http_port} - {e}")
    loop.run_forever()


class WebViewer(threading.Thread):
    def __init__(self, _logger: Logger):
        super().__init__()
        config.set_logger(_logger)

    def run(self) -> None:
        run_http_server()

    def stop(self) -> None:
        global loop
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)


if __name__ == "__main__":
    run_http_server()