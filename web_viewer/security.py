import ssl
from base64 import b64decode
from html import escape
from os import path
from typing import Optional

from aiohttp.aiohttp import web

from . import config


def get_ssl_context() -> Optional[ssl.SSLContext]:
    """Create an SSLContext for HTTPS if configured.

    Expects the following env vars:
      - HTTPS_ENABLED (true/false)
      - HTTPS_CERT_FILE (path to PEM cert file)
      - HTTPS_KEY_FILE (path to PEM key file)
      - HTTPS_CERT_PASSWORD (optional password for key)
    """
    if config.config.get("HTTPS_ENABLED", "false").lower() != "true":
        return None

    cert_file = config.config.get("HTTPS_CERT_FILE")
    key_file = config.config.get("HTTPS_KEY_FILE")
    key_password = config.config.get("HTTPS_CERT_PASSWORD")

    if not cert_file or not key_file:
        config.logger.warning("HTTPS_ENABLED=true but HTTPS_CERT_FILE or HTTPS_KEY_FILE is not set; HTTPS will not start")
        return None

    if not path.exists(cert_file):
        config.logger.warning("HTTPS certificate file does not exist: %s; HTTPS will not start", cert_file)
        return None

    if not path.exists(key_file):
        config.logger.warning("HTTPS key file does not exist: %s; HTTPS will not start", key_file)
        return None

    try:
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(certfile=cert_file, keyfile=key_file, password=key_password)
        config.logger.info("HTTPS enabled using cert=%s key=%s", cert_file, key_file)
        return ctx
    except Exception as e:
        config.logger.error("Failed to create SSL context for HTTPS: %s", e)
        return None


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
            config.logger.warning("Invalid ADMIN_ALLOWED_CIDR entry: %s", cidr)
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
    if _ip_in_cidrs(remote_ip, config.ADMIN_ALLOWED_CIDR):
        return None
    body = response_body
    if body is None:
        body = {"success": False, "message": "Forbidden"}
    return web.json_response(body, status=403)


async def has_admin_access(request: web.Request):
    """Return whether the requesting client IP is allowed to access admin features.

    Controlled only via the `ADMIN_ALLOWED_CIDR` environment variable. Returns
    JSON with boolean key `has_admin_access`.
    """
    peer = request.transport
    remote_ip = _peer_ip_from_transport(peer)
    allowed = _ip_in_cidrs(remote_ip, config.ADMIN_ALLOWED_CIDR)
    return web.json_response({"has_admin_access": allowed})


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
    # Read auth settings from persistent `settings` (updated by web UI).
    auth_bypass_cidr = config.config.get("AUTH_BYPASS_CIDR", "127.0.0.1/32,::1/128")
    try:
        import settings as app_settings
        auth_enabled = app_settings.get_setting("AUTH_ENABLED", config.config.get("AUTH_ENABLED", "false")).lower() == "true"
        auth_username = app_settings.get_setting("AUTH_USERNAME", config.config.get("AUTH_USERNAME", "admin"))
        auth_password = app_settings.get_setting("AUTH_PASSWORD", config.config.get("AUTH_PASSWORD", "changeme"))
        auth_bypass_cidr = app_settings.get_setting("AUTH_BYPASS_CIDR", auth_bypass_cidr)
    except Exception:
        # Fallback to env/config if settings module not available for any reason
        auth_enabled = config.config.get("AUTH_ENABLED", "false").lower() == "true"
        auth_username = config.config.get("AUTH_USERNAME", "admin")
        auth_password = config.config.get("AUTH_PASSWORD", "changeme")

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
    resp = _deny_if_not_allowed_cidr(request, "/tuya-devices", allowed_methods=("OPTIONS",), web_only=False)
    if resp:
        return resp
    resp = _deny_if_not_allowed_cidr(request, "/triggers", allowed_methods=("OPTIONS",), web_only=False)
    if resp:
        return resp
    resp = _deny_if_not_allowed_cidr(request, "/modbus", allowed_methods=("OPTIONS",), web_only=False)
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