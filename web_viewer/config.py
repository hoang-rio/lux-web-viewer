import logging
from os import environ
from typing import Optional

from dotenv import dotenv_values
from sleep_cache import set_enabled as _sleep_cache_set_enabled

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

logger: logging.Logger = logging.getLogger(__file__)


def set_logger(_logger: Optional[logging.Logger]) -> None:
    """Replace the package logger at runtime (called from WebViewer)."""
    global logger
    if _logger is not None:
        logger = _logger