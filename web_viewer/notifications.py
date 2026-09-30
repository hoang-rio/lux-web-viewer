import json
import re
from datetime import date

from aiohttp.aiohttp import web

from . import config
from . import streaming
from .db import get_db_connection

DATE_PARAM_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date_param(raw):
    """Return a normalized YYYY-MM-DD string, or None when absent/malformed."""
    if raw is None or raw == "":
        return None
    if not DATE_PARAM_PATTERN.match(raw):
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


async def _broadcast_unread_count(unread_count=0):
    try:
        await streaming.broadcast_sse(
            json.dumps({"event": "update_unread_count", "data": {"unread_count": unread_count}})
        )
    except Exception as e:
        config.logger.error(f"Error broadcasting update_unread_count: {e}")


async def notification_history(request: web.Request):
    raw_date = request.query.get("date")
    day = _parse_date_param(raw_date)
    if raw_date and not day:
        config.logger.warning(f"Ignoring malformed notification history date: {raw_date!r}")
        return web.json_response({"notifications": []})
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if day:
            # notified_at is stored as either "%Y-%m-%d %H:%M:%S" or ISO-8601,
            # so compare the leading date component instead of the whole value.
            notifications = cursor.execute(
                "SELECT id, title, body, notified_at, read FROM notification_history "
                "WHERE substr(notified_at, 1, 10) = ? ORDER BY notified_at DESC",
                (day,),
            ).fetchall()
        else:
            notifications = cursor.execute(
                "SELECT id, title, body, notified_at, read FROM notification_history ORDER BY notified_at DESC"
            ).fetchall()
        data = [
            {"id": row[0], "title": row[1], "body": row[2], "notified_at": row[3], "read": row[4]}
            for row in notifications
        ]
        return web.json_response({"notifications": data})
    except Exception as e:
        config.logger.error(f"Error in notification_history: {e}")
        return web.json_response({"notifications": []})


async def mark_notifications_read(_: web.Request):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE notification_history SET read = 1 WHERE read = 0")
        conn.commit()
        unread_count = cursor.execute(
            "SELECT COUNT(*) FROM notification_history WHERE read = 0"
        ).fetchone()[0]
        await _broadcast_unread_count(unread_count)
        return web.json_response({"success": True})
    except Exception as e:
        config.logger.error(f"Error in mark_notifications_read: {e}")
        return web.json_response({"success": False})


async def notification_unread_count(_: web.Request):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        unread_count = cursor.execute("SELECT COUNT(*) FROM notification_history WHERE read = 0").fetchone()[0]
        return web.json_response({"unread_count": unread_count})
    except Exception as e:
        config.logger.error(f"Error in notification_unread_count: {e}")
        return web.json_response({"unread_count": 0})


async def delete_notification(request: web.Request):
    try:
        notification_id = request.match_info['id']
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM notification_history WHERE id = ?", (notification_id,))
        conn.commit()
        if cursor.rowcount == 0:
            return web.json_response({"error": "Notification not found"}, status=404)
        unread_count = cursor.execute(
            "SELECT COUNT(*) FROM notification_history WHERE read = 0"
        ).fetchone()[0]
        await _broadcast_unread_count(unread_count)
        return web.json_response({"success": True})
    except Exception as e:
        config.logger.error(f"Error in delete_notification: {e}")
        return web.json_response({"error": str(e)}, status=500)