"""Shared runtime state injectable from app.py."""

import threading

_fcm_service = None
_config = {}
_player = None
_eval_lock = threading.Lock()


def set_fcm_service(fcm):
    """Set the shared FCM service instance (called from app.py)."""
    global _fcm_service
    _fcm_service = fcm


def set_config(config):
    """Set the shared config instance (called from app.py)."""
    global _config
    _config = config


def set_player(player):
    """Set the shared player instance (called from app.py)."""
    global _player
    _player = player