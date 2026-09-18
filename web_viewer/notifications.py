from aiohttp.aiohttp import web

from multi_tenant import repository as mt_repo
from multi_tenant.db import get_db_session

from . import config
from .db import get_db_connection
from .security import _require_jwt_user_id


async def notification_history(request: web.Request):
    if config.USE_PG:
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
            config.logger.error(f"Error in notification_history (multi-tenant): {e}")
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
                session.commit()
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
        return web.json_response({"success": True})
    except Exception as e:
        config.logger.error(f"Error in delete_notification: {e}")
        return web.json_response({"error": str(e)}, status=500)