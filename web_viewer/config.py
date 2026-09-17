import logging
from os import environ
from typing import Optional

from dotenv import dotenv_values

# Load config from .env and environment
config: dict = {**dotenv_values(".env"), **environ}

# Comma-separated CIDR list that are allowed to access admin features (settings + notification modification).
# Configured via `ADMIN_ALLOWED_CIDR` environment variable.
ADMIN_ALLOWED_CIDR = config.get("ADMIN_ALLOWED_CIDR", "")

logger: logging.Logger = logging.getLogger(__file__)


def set_logger(_logger: Optional[logging.Logger]) -> None:
    """Replace the package logger at runtime (called from WebViewer)."""
    global logger
    if _logger is not None:
        logger = _logger