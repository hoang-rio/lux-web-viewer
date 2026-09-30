import json
import re
from datetime import date

from aiohttp.aiohttp import web

from multi_tenant import repository as mt_repo
from multi_tenant.db import get_db_session

from . import config
from . import streaming
from .db import get_db_connection
from .security import _require_jwt_user_id, _resolve_request_inverter

DATE_PARAM_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date_param(raw):
    """Return a parsed date, or None when absent/malformed."""
    if raw is None or raw == "":
        return None
    if not DATE_PARAM_PATTERN.match(raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


async def _broadcast_unread_count(user_id=None, unread_count=0):
    try:
        data = {"event": "update_unread_count", "data": {"unread_count": unread_count}}
        if user_id is not None:
            data["data"]["user_id"] = str(user_id)
        await streaming.broadcast_sse(json.dumps(data))
    except Exception as e:
        config.logger.error(f"Error broadcasting update_unread_count: {e}")


async def notification_history(request: web.Request):
    raw_date = request.rel_url.query.get("date")
    day = _parse_date_param(raw_date)
    malformed = bool(raw_date) and day is None
    if malformed:
        config.logger.warning(f"Ignoring malformed notification history date: {raw_date!r}")

    if config.USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error
        # Checked after auth so an unauthenticated caller always gets a 401.
        if malformed:
            return web.json_response({"notifications": []})
        try:
            session = next(get_db_session())
            try:
                inverter = _resolve_request_inverter(session, user_id, request)
                if inverter is None:
                    return web.json_response({"notifications": []})
                notifications = mt_repo.get_notification_history(
                    session, user_id, day=day, inverter_id=inverter.id
                )
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
            config.logger.error(f"Error in notification_history (multi-tenant): {e}")
            return web.json_response({"notifications": []})

    if malformed:
        return web.json_response({"notifications": []})

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if day:
            # Single-backend mode stores either "%Y-%m-%d %H:%M:%S" or ISO-8601,
            # so compare the leading date component instead of the whole value.
            notifications = cursor.execute(
                "SELECT id, title, body, notified_at, read FROM notification_history "
                "WHERE substr(notified_at, 1, 10) = ? ORDER BY notified_at DESC",
                (day.isoformat(),),
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


async def mark_notifications_read(request: web.Request):
    if config.USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error
        try:
            session = next(get_db_session())
            try:
                mt_repo.mark_notifications_read(session, user_id)
                unread_count = mt_repo.get_unread_notification_count(session, user_id)
                session.commit()
                await _broadcast_unread_count(str(user_id), unread_count)
                return web.json_response({"success": True})
            finally:
                session.close()
        except Exception as e:
            config.logger.error(f"Error in mark_notifications_read (multi-tenant): {e}")
            return web.json_response({"success": False})

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE notification_history SET read = 1 WHERE read = 0")
        conn.commit()
        unread_count = cursor.execute(
            "SELECT COUNT(*) FROM notification_history WHERE read = 0"
        ).fetchone()[0]
        await _broadcast_unread_count(None, unread_count)
        return web.json_response({"success": True})
    except Exception as e:
        config.logger.error(f"Error in mark_notifications_read: {e}")
        return web.json_response({"success": False})


async def notification_unread_count(request: web.Request):
    if config.USE_PG:
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
            config.logger.error(f"Error in notification_unread_count (multi-tenant): {e}")
            return web.json_response({"unread_count": 0})

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        unread_count = cursor.execute("SELECT COUNT(*) FROM notification_history WHERE read = 0").fetchone()[0]
        return web.json_response({"unread_count": unread_count})
    except Exception as e:
        config.logger.error(f"Error in notification_unread_count: {e}")
        return web.json_response({"unread_count": 0})


async def delete_notification(request: web.Request):
    if config.USE_PG:
        user_id, auth_error = _require_jwt_user_id(request)
        if auth_error is not None:
            return auth_error
        try:
            notification_id = int(request.match_info['id'])
            session = next(get_db_session())
            try:
                deleted = mt_repo.delete_notification(session, user_id, notification_id)
                session.commit()
                if not deleted:
                    return web.json_response({"error": "Notification not found"}, status=404)
                unread_count = mt_repo.get_unread_notification_count(session, user_id)
                await _broadcast_unread_count(str(user_id), unread_count)
                return web.json_response({"success": True})
            finally:
                session.close()
        except Exception as e:
            config.logger.error(f"Error in delete_notification (multi-tenant): {e}")
            return web.json_response({"error": str(e)}, status=500)

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
        await _broadcast_unread_count(None, unread_count)
        return web.json_response({"success": True})
    except Exception as e:
        config.logger.error(f"Error in delete_notification: {e}")
        return web.json_response({"error": str(e)}, status=500)