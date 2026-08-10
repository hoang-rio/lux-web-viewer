import logging
from os import path
import requests
from google.oauth2 import service_account
import google.auth.transport.requests
from threading import Thread
import sqlite3
import uuid

from api_storage import load_device_tokens, save_device_tokens
from web_socket_client import WebSocketClient

SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]

CHANNEL_GENERAL = "000"
CHANNEL_ON_GRID = "111"
CHANNEL_OFF_GRID = "222"
CHANNEL_WARN = "333"


class FCMThread(Thread):
    __config: dict
    __logger: logging.Logger
    __title: str
    __body: str
    __device: str
    __is_grid_connected: bool
    __channel_id: str
    __sound: str
    __inverter_id: str | None
    valid_device: bool = True

    def __init__(self, logger: logging.Logger, config: dict, title: str, body: str, device: str, is_grid_connected: bool, channel_id: str = CHANNEL_ON_GRID, inverter_id: str | None = None):
        super(FCMThread, self).__init__()
        self.__config = config
        self.__logger = logger
        self.__title = title
        self.__body = body
        self.__device = device
        self.__is_grid_connected = is_grid_connected
        self.__channel_id = channel_id
        self.__inverter_id = inverter_id
        if channel_id == CHANNEL_GENERAL:
            self.__sound = ""
        else:
            self.__sound = "warning" if channel_id == CHANNEL_WARN else "has_grid" if is_grid_connected else "lost_grid"

    def _get_access_token(self):
        """Retrieve a valid access token that can be used to authorize requests.
        :return: Access token.
        """
        credentials = service_account.Credentials.from_service_account_file(
            self.__config["FCM_ADMIN_KEY_FILE"], scopes=SCOPES)
        request = google.auth.transport.requests.Request()
        credentials.refresh(request)
        return credentials.token

    def __send_notify(self, title: str, body: str, device: str, is_grid_connected: bool):
        if not path.exists(self.__config["FCM_ADMIN_KEY_FILE"]):
            self.__logger.warning("Missing FCM_ADMIN_KEY_FILE")
            return
        if 'FCM_PROJECT' not in self.__config or self.__config["FCM_PROJECT"] == "":
            self.__logger.warning("Missing FCM_PROJECT")
            return
        self.__logger.info(
            "Send notify with title: %s and body: %s to %s", title, body, device)
        access_token = self._get_access_token()
        req = requests.post(
            f"https://fcm.googleapis.com/v1/projects/{self.__config['FCM_PROJECT']}/messages:send",
            json={
                "message": {
                    "notification": {
                        "title": title,
                        "body": body
                    },
                    "data": {
                        "title": title,
                        "body": body,
                        "channel_id": self.__channel_id,
                        "is_grid_connected": "1" if is_grid_connected else "0",
                        "is_warning": "1" if self.__channel_id == CHANNEL_WARN else "0",
                        "inverter_id": self.__inverter_id or "",
                    },
                    "android": {
                        "priority": "HIGH",
                        "direct_boot_ok": True,
                        "notification": {
                            "channel_id": self.__channel_id,
                            "notification_priority": "PRIORITY_MAX",
                            "visibility": "PUBLIC",
                            "default_sound": self.__channel_id == CHANNEL_GENERAL,
                            "default_vibrate_timings": False,
                            "vibrate_timings": ["0.1s", "1s", "0.1s", "1s", "0.1s"],
                            "default_light_settings": True,
                            "sound": f"{self.__sound}.mp3" if self.__sound != "" else "default",
                        },
                    },
                    "apns": {
                        "payload": {
                            "aps": {
                                "sound": f"{self.__sound}.aiff" if self.__sound != "" else "default",
                                "badge": 0
                            }
                        },
                    },
                    "token": device,
                },
            },
            headers={
                'Authorization': 'Bearer ' + access_token,
                'Content-Type': 'application/json; UTF-8',
            }
        )
        self.valid_device = req.status_code == 200
        self.__logger.info("FCM send to device: %s", device)
        self.__logger.info("FCM Status code: %s", req.status_code)
        self.__logger.info("FCM Result: %s", req.text)

    def run(self):
        log_prefix = "WARN" if self.__channel_id == CHANNEL_WARN else "ON" if self.__is_grid_connected else "OFF"
        self.__logger.info(
            f"{log_prefix} [{self.__device}]: Start send notify")
        self.__send_notify(self.__title, self.__body,
                           self.__device, self.__is_grid_connected)
        self.__logger.info(
            f"{log_prefix} [{self.__device}]: Finish send notify")


class FCM():
    __config: dict
    __logger: logging.Logger
    __fcm_threads: list[FCMThread]
    __ws_client: WebSocketClient | None = None

    def __init__(self, logger: logging.Logger, config: dict) -> None:
        self.__config = config
        self.__logger = logger
        self.__fcm_threads = []

    def _set_ws_client(self, ws_client: WebSocketClient):
        self.__ws_client = ws_client

    def __use_pg(self) -> bool:
        return bool(self.__config.get("POSTGRES_DB_URL") or self.__config.get("DATABASE_URL"))

    def __get_devices(self, user_id: str | None = None):
        if self.__use_pg() and user_id:
            try:
                from multi_tenant.db import get_db_session
                from multi_tenant import repository as repo

                session = next(get_db_session())
                try:
                    return repo.get_device_tokens_by_user(session, uuid.UUID(user_id))
                finally:
                    session.close()
            except Exception as e:
                self.__logger.exception("Failed to load PG device tokens: %s", e)
                return []
        return load_device_tokens(self.__config["DEVICE_IDS_JSON_FILE"])

    def __save_device(self, devices: list[str]):
        save_device_tokens(self.__config["DEVICE_IDS_JSON_FILE"], devices)

    def __post_send_notify(self, devices: list[str], user_id: str | None = None, threads: list | None = None):
        """Join FCM threads and prune dead device tokens. Runs on a background thread."""
        threads = threads if threads is not None else self.__fcm_threads
        valid_devices: list[str] = []
        for idx, t in enumerate(threads):
            t.join()
            if t.valid_device:
                valid_devices.append(devices[idx])

        if self.__use_pg() and user_id:
            try:
                from multi_tenant.db import get_db_session
                from multi_tenant import repository as repo

                session = next(get_db_session())
                try:
                    # Remove invalid tokens by re-binding only valid tokens for this user.
                    current = set(repo.get_device_tokens_by_user(session, uuid.UUID(user_id)))
                    valid = set(valid_devices)
                    invalid = current - valid
                    if invalid:
                        from multi_tenant.models import UserDeviceToken
                        from sqlalchemy import delete
                        session.execute(
                            delete(UserDeviceToken).where(
                                UserDeviceToken.user_id == uuid.UUID(user_id),
                                UserDeviceToken.token.in_(list(invalid)),
                            )
                        )
                        session.commit()
                finally:
                    session.close()
            except Exception as e:
                self.__logger.exception("Failed to clean invalid PG tokens: %s", e)
            return

        if len(valid_devices) != len(devices):
            self.__save_device(valid_devices)

    def __notify_async(self, devices: list[str], user_id: str | None = None):
        """Send notifications without blocking the caller: join/save run on a background thread."""
        Thread(target=self.__post_send_notify, args=(devices, user_id, self.__fcm_threads[:]), daemon=True).start()

    def __log_notification(self, title: str, body: str, inverter_ctx: dict | None = None):
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            from os import environ

            tz_name = environ.get("TZ", "UTC")
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = ZoneInfo("UTC")
            now_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

            inverter_id = (inverter_ctx or {}).get("id")
            user_id = (inverter_ctx or {}).get("user_id")

            if self.__use_pg() and user_id:
                from multi_tenant.db import get_db_session
                from multi_tenant import repository as repo

                session = next(get_db_session())
                try:
                    repo.insert_notification(
                        session,
                        user_id=uuid.UUID(user_id),
                        title=title,
                        body=body,
                        inverter_id=uuid.UUID(inverter_id) if inverter_id else None,
                    )
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
                finally:
                    session.close()
            else:
                conn = sqlite3.connect(self.__config["DB_NAME"])
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO notification_history (notified_at, title, body) VALUES (?, ?, ?)",
                    (now_str, title, body)
                )
                conn.commit()
                cursor.execute(
                    "DELETE FROM notification_history WHERE rowid NOT IN (SELECT rowid FROM notification_history ORDER BY notified_at DESC LIMIT 30)"
                )
                conn.commit()
                cursor.close()
                conn.close()

            if self.__ws_client is not None:
                import asyncio
                def send_ws():
                    asyncio.run(self.__ws_client.send_json({
                        "event": "new_notification",
                        "data": {
                            "title": title,
                            "body": body,
                            "notified_at": now_str,
                            "read": 0,
                            "inverter_id": inverter_id,
                        }
                    }))
                Thread(target=send_ws).start()
            else:
                self.__logger.warning("__ws_client not found")
        except Exception as e:
            self.__logger.exception("Failed to log notification history: %s", e)

    def ongrid_notify(self, inverter_ctx: dict | None = None):
        self.__fcm_threads = []
        user_id = (inverter_ctx or {}).get("user_id")
        inverter_id = (inverter_ctx or {}).get("id")
        inverter_name = (inverter_ctx or {}).get("name")
        devices = self.__get_devices(user_id)
        notify_title = "Đã có điện lưới"
        notify_title = f"[{inverter_name}] {notify_title}" if inverter_name else notify_title
        notify_body = "Nhà đã có điện lưới có thể sử dụng điện không giới hạn."
        if len(devices) == 0:
            self.__logger.info("ON: No device to notify")
        else:
            self.__logger.info(
                f"ON: Start send notifcation to {len(devices)} devices"
            )
            for device in devices:
                t = FCMThread(
                    self.__logger,
                    self.__config,
                    notify_title,
                    notify_body,
                    device,
                    True,
                    CHANNEL_ON_GRID,
                    inverter_id,
                    )
                self.__fcm_threads.append(t)
                t.start()
            self.__notify_async(devices, user_id)
        self.__log_notification(notify_title, notify_body, inverter_ctx)

    def battery_full_notify(self, body="Pin đã sạc đầy 100%. Có thể bật bình nóng lạnh để tối ưu sử dụng.", inverter_ctx: dict | None = None):
        self.__fcm_threads = []
        user_id = (inverter_ctx or {}).get("user_id")
        inverter_id = (inverter_ctx or {}).get("id")
        inverter_name = (inverter_ctx or {}).get("name")
        devices = self.__get_devices(user_id)
        notify_title = "Pin đã đầy"
        notify_title = f"[{inverter_name}] {notify_title}" if inverter_name else notify_title
        notify_body = body
        if len(devices) == 0:
            self.__logger.info("BATTERY_FULL: No device to notify")
        else:
            self.__logger.info(
                f"BATTERY_FULL: Start send notifcation to {len(devices)} devices"
            )
            for device in devices:
                t = FCMThread(
                    self.__logger,
                    self.__config,
                    notify_title,
                    notify_body,
                    device,
                    True,
                    CHANNEL_GENERAL,
                    inverter_id,
                )
                self.__fcm_threads.append(t)
                t.start()
            self.__notify_async(devices, user_id)
        self.__log_notification(notify_title, notify_body, inverter_ctx)

    def offgrid_notify(self, inverter_ctx: dict | None = None):
        self.__fcm_threads = []
        user_id = (inverter_ctx or {}).get("user_id")
        inverter_id = (inverter_ctx or {}).get("id")
        inverter_name = (inverter_ctx or {}).get("name")
        devices = self.__get_devices(user_id)
        notify_title = "Mất điện lưới"
        notify_title = f"[{inverter_name}] {notify_title}" if inverter_name else notify_title
        notify_body = "Nhà đã mất điện lưới cần hạn chế sử dụng thiết bị điện công suất lớn như bếp từ, bình nóng lạnh."
        if len(devices) == 0:
            self.__logger.info("OFF: No device to notify")
        else:
            self.__logger.info(
                f"OFF: Start send notifcation to {len(devices)} devices"
            )
            for device in devices:
                t = FCMThread(
                    self.__logger,
                    self.__config,
                    notify_title,
                    notify_body,
                    device,
                    False,
                    CHANNEL_OFF_GRID,
                    inverter_id,
                )
                self.__fcm_threads.append(t)
                t.start()
            self.__notify_async(devices, user_id)
        self.__log_notification(notify_title, notify_body, inverter_ctx)

    def abnormal_notify(self, body: str = "Tiêu thụ điện bất thường, vui lòng kiểm tra xem vòi nước đã khóa chưa.", inverter_ctx: dict | None = None):
        self.__fcm_threads = []
        user_id = (inverter_ctx or {}).get("user_id")
        inverter_id = (inverter_ctx or {}).get("id")
        inverter_name = (inverter_ctx or {}).get("name")
        devices = self.__get_devices(user_id)
        notify_title = "Cảnh báo: Tiêu thụ điện bất thường"
        notify_title = f"[{inverter_name}] {notify_title}" if inverter_name else notify_title
        notify_body = body
        if len(devices) == 0:
            self.__logger.info("WARN: No device to notify")
        else:
            self.__logger.info(
                f"WARN: Start send notifcation to {len(devices)} devices"
            )
            for device in devices:
                t = FCMThread(
                    self.__logger,
                    self.__config,
                    notify_title,
                    notify_body,
                    device,
                    True,
                    CHANNEL_WARN,
                    inverter_id,
                )
                self.__fcm_threads.append(t)
                t.start()
            self.__notify_async(devices, user_id)
        self.__log_notification(notify_title, notify_body, inverter_ctx)

    def offgrid_warning_notify(self, warning_power: float = 1500, warning_soc: int | None = None, inverter_ctx: dict | None = None):
        self.__fcm_threads = []
        user_id = (inverter_ctx or {}).get("user_id")
        inverter_id = (inverter_ctx or {}).get("id")
        inverter_name = (inverter_ctx or {}).get("name")
        devices = self.__get_devices(user_id)
        notify_title = "Cảnh báo: Tiêu thụ điện cao"
        notify_title = f"[{inverter_name}] {notify_title}" if inverter_name else notify_title
        if warning_soc is None:
            notify_body = f"Tiêu thụ điện cao hơn {warning_power}W khi đang mất điện lưới và nắng yếu. Vui lòng chú ý!"
        else:
            notify_body = f"Tiêu thụ điện cao hơn {warning_power}W khi đang mất điện lưới, nắng yếu và pin còn ít hơn {warning_soc}%. Vui lòng chú ý!"
        if len(devices) == 0:
            self.__logger.info("WARN: No device to notify")
        else:
            self.__logger.info(
                f"WARN: Start send notifcation to {len(devices)} devices"
            )
            for device in devices:
                t = FCMThread(
                    self.__logger,
                    self.__config,
                    notify_title,
                    notify_body,
                    device,
                    False,
                    CHANNEL_WARN,
                    inverter_id,
                )
                self.__fcm_threads.append(t)
                t.start()
            self.__notify_async(devices, user_id)
        self.__log_notification(notify_title, notify_body, inverter_ctx)
