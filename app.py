import sqlite3
from dotenv import dotenv_values
import logging.handlers
import logging
import time
from os import path, environ
from urllib.parse import quote_plus
import dongle_handler
import http_handler
from datetime import datetime, timedelta
from fcm import FCM
import json
from play_audio import PlayAudio
import asyncio
from web_socket_client import WebSocketClient
import settings
import database
from sleep_cache import (
    get_cached_sleep_time,
    normalize_sleep_time as _normalize_sleep_time,
    set_cached_sleep_time,
    set_enabled as _sleep_cache_set_enabled,
)

DONGLE_MODE = "DONGLE"
SERVER_MODE = "SERVER"

# SLEEP_TIME should be minimum audio duration + 2 seconds
# Example: 4 seconds for has-grid.mp3 + 2 seconds = 6 seconds to sleep
AUDIO_SLEEP_MAP = {
    "has-grid.mp3": 6,
    "lost-grid.mp3": 9,
    "warning.mp3": 7,
    "warning_power_off_grid.mp3": 6
}

config: dict = {
    **dotenv_values(".env"),
    **environ
}


def _build_postgres_url_from_env(cfg: dict) -> str | None:
    if cfg.get("POSTGRES_DB_URL") or cfg.get("DATABASE_URL"):
        return cfg.get("POSTGRES_DB_URL") or cfg.get("DATABASE_URL")
    user = cfg.get("POSTGRES_USER")
    password = cfg.get("POSTGRES_PASSWORD")
    db_name = cfg.get("POSTGRES_DB")
    if not (user and password and db_name):
        return None
    host = cfg.get("POSTGRES_HOST", "postgres")
    port = cfg.get("POSTGRES_PORT", "5432")
    return (
        f"postgresql+psycopg://{quote_plus(str(user))}:{quote_plus(str(password))}"
        f"@{host}:{port}/{db_name}"
    )


_postgres_db_url = _build_postgres_url_from_env(config)
if _postgres_db_url:
    config["POSTGRES_DB_URL"] = _postgres_db_url
    environ.setdefault("POSTGRES_DB_URL", _postgres_db_url)

# Initialize settings module with config for fallback
settings.init_config(config)
USE_PG = bool(config.get("POSTGRES_DB_URL") or config.get("DATABASE_URL"))
# Configure centralized sleep cache to be enabled only when PostgreSQL is configured
try:
    _sleep_cache_set_enabled(USE_PG)
except Exception:
    pass

log_level = logging.DEBUG if config["IS_DEBUG"] == 'True' else logging.INFO

logger = logging.getLogger(__file__)
log_file_handler = logging.handlers.RotatingFileHandler(
    config["LOG_FILE"],
    mode='a',
    maxBytes=int(config["LOG_FILE_SIZE"])*1024,
    backupCount=int(config["LOG_FILE_COUNT"])
)
log_file_handler.setFormatter(logging.Formatter(config["LOG_FORMAT"]))
log_file_handler.setLevel(log_level)
log_handlers: list[logging.Handler] = [
    log_file_handler
]
if log_level == logging.DEBUG:
    log_handlers.append(logging.StreamHandler())

logging.basicConfig(
    level=log_level,
    format=config["LOG_FORMAT"],
    handlers=log_handlers
)

play_audio_thread: PlayAudio | None = None

def play_audio(audio_file: str, repeat=3):
    if USE_PG:
        return
    global play_audio_thread
    if play_audio_thread is not None:
        play_audio_thread.stop()
    play_audio_thread = PlayAudio(
        audio_file=audio_file, repeat=repeat, sleep=AUDIO_SLEEP_MAP[audio_file], config=config, logger=logger)
    play_audio_thread.start()

# Number of hours to skip check when detect abnormal usage
abnormal_skip_check_count = 0
def dectect_abnormal_usage(db_connection: sqlite3.Connection, fcm_service: FCM, inverter_ctx: dict | None = None):
    abnormal_detection_enabled = _to_bool(
        _get_user_setting(
            inverter_ctx,
            "ABNORMAL_DETECTION_ENABLED",
            str(settings.get_abnormal_detection_enabled()),
        )
    )
    if not abnormal_detection_enabled:
        return  # Skip check if abnormal detection is disabled
    now = datetime.now()
    # now = now.replace(minute=0, second=0, hour=6)
    sleep_time = _get_sleep_time(inverter_ctx)
    abnormal_check_cooldown_hours = _to_int(
        _get_user_setting(
            inverter_ctx,
            "ABNORMAL_CHECK_COOLDOWN_HOURS",
            str(settings.get_abnormal_check_cooldown_hours()),
        ),
        settings.get_abnormal_check_cooldown_hours(),
    )
    if (sleep_time >= 60 and now.minute <= sleep_time / 60) or (now.minute == 0 and now.second <= sleep_time):
        global abnormal_skip_check_count
        if abnormal_skip_check_count > 0:
            abnormal_skip_check_count = abnormal_skip_check_count - 1
            return
        cursor = db_connection.cursor()
        abnormal_check_start_time = now - timedelta(hours=abnormal_check_cooldown_hours)
        from web_viewer import dict_factory
        cursor.row_factory = dict_factory
        all_items = cursor.execute(
            "SELECT * FROM hourly_chart WHERE datetime >= ? AND datetime < ?",
            (abnormal_check_start_time.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S"))
        ).fetchall()
        abnormnal_count = 0
        normnal_count = 0
        min_power = 0
        max_power = 0
        consumption_count: dict = {}
        abnormal_min_power = _to_int(
            _get_user_setting(
                inverter_ctx,
                "ABNORMAL_MIN_POWER",
                str(settings.get_abnormal_min_power()),
            ),
            settings.get_abnormal_min_power(),
        )
        abnormal_usage_count = 32 * abnormal_check_cooldown_hours
        normal_min_usage_count = 5 * abnormal_check_cooldown_hours
        for item in all_items:
            rounded_consumption = item["consumption"] - item["consumption"] % 200
            consumption_count[rounded_consumption] = consumption_count.get(rounded_consumption, 0) + 1
            if min_power == 0 or rounded_consumption < min_power:
                min_power = rounded_consumption
            if rounded_consumption > abnormal_min_power and (max_power == 0 or consumption_count[rounded_consumption] > consumption_count.get(max_power, 0)):
                max_power = rounded_consumption
        abnormnal_count = consumption_count.get(max_power, 0)
        normnal_count = consumption_count.get(min_power, 0)
        if max_power >= abnormal_min_power and max_power > min_power and abnormnal_count > abnormal_usage_count and normnal_count > normal_min_usage_count and normnal_count < abnormnal_count:
            logger.warning(
                "_________Abnormal usage detected from %s to %s with %s abnormal times and %s normal times (max_power: %s, min_power: %s)_________",
                abnormal_check_start_time.strftime("%Y-%m-%d %H:%M:%S"),
                now.strftime("%Y-%m-%d %H:%M:%S"),
                abnormnal_count,
                normnal_count,
                max_power,
                min_power
            )
            warning_body = _get_user_setting(
                inverter_ctx,
                "ABNORMAL_NOTIFY_BODY",
                settings.get_abnormal_notify_body(),
            )
            fcm_service.abnormal_notify(warning_body, inverter_ctx)
            play_audio("warning.mp3", 5)
            # Skip next abnormal_check_cooldown_hours hours when detect abnormal usage
            abnormal_skip_check_count = abnormal_check_cooldown_hours
        else:
            logger.info(
                "_________No abnormal usage detected from %s to %s with %s abnormal times and %s normal times (max_power: %s, min_power: %s)_________",
                abnormal_check_start_time.strftime("%Y-%m-%d %H:%M:%S"),
                now.strftime("%Y-%m-%d %H:%M:%S"),
                abnormnal_count,
                normnal_count,
                max_power,
                min_power
            )
        cursor.close()


dectect_off_grid_warning_skip_check_count = 0


def dectect_off_grid_warning(is_grid_connected: bool, pv_power: int, eps_power: int, soc: int, fcm_service: FCM, inverter_ctx: dict | None = None):
    off_grid_warning_enabled = _to_bool(
        _get_user_setting(
            inverter_ctx,
            "OFF_GRID_WARNING_ENABLED",
            str(settings.get_off_grid_warning_enabled()),
        )
    )
    if not off_grid_warning_enabled or is_grid_connected:
        return
    if pv_power > eps_power:
        # No need to check off grid warning if pv power is greater than eps power
        return
    global dectect_off_grid_warning_skip_check_count
    # Notify off grid warning if eps power >= OFF_GRID_WARNING_POWER and pv power < eps power / 2
    # and (battery percent (soc) < OFF_GRID_WARNING_SOC or battery discharge > MAX_BATTERY_POWER)
    off_grid_warning_power = _to_int(
        _get_user_setting(
            inverter_ctx,
            "OFF_GRID_WARNING_POWER",
            str(settings.get_off_grid_warning_power()),
        ),
        settings.get_off_grid_warning_power(),
    )
    off_grid_warning_soc = _to_int(
        _get_user_setting(
            inverter_ctx,
            "OFF_GRID_WARNING_SOC",
            str(settings.get_off_grid_warning_soc()),
        ),
        settings.get_off_grid_warning_soc(),
    )
    max_battery_power = _to_int(
        _get_user_setting(
            inverter_ctx,
            "MAX_BATTERY_POWER",
            str(settings.get_max_battery_power()),
        ),
        settings.get_max_battery_power(),
    )

    high_load = eps_power >= off_grid_warning_power
    insufficient_pv = pv_power < eps_power / 2
    low_battery = soc < off_grid_warning_soc
    high_battery_discharge = eps_power - pv_power > max_battery_power

    if high_load and insufficient_pv and (low_battery or high_battery_discharge):
        if dectect_off_grid_warning_skip_check_count > 0:
            dectect_off_grid_warning_skip_check_count = dectect_off_grid_warning_skip_check_count - 1
            return
        logger.warning(
            "_________Off grid warning detected with pv power: (%sW), eps power: (%sW) and soc: (%s%)_________",
            pv_power,
            eps_power,
            soc
        )
        fcm_service.offgrid_warning_notify(
            off_grid_warning_power,
            None if high_battery_discharge else off_grid_warning_soc,
            inverter_ctx,
        )
        play_audio("warning_power_off_grid.mp3")
        # Skip next OFF_GRID_WARNING_SKIP_CHECK_COUNT time when detect off grid warning
        sleep_time = max(_get_sleep_time(inverter_ctx), 1)
        dectect_off_grid_warning_skip_check_count = max(60 // sleep_time, 1)
    else:
        dectect_off_grid_warning_skip_check_count = 0

def detect_battery_full(soc: int, fcm_service: FCM, inverter_ctx: dict | None = None):
    battery_full_notify_enabled = _to_bool(
        _get_user_setting(
            inverter_ctx,
            "BATTERY_FULL_NOTIFY_ENABLED",
            str(settings.get_battery_full_notify_enabled()),
        )
    )
    if not battery_full_notify_enabled:
        return

    now = datetime.now()
    today_str = now.date().isoformat()
    last_notify_date = _get_user_setting(inverter_ctx, "LAST_BATTERY_FULL_NOTIFY_DATE", "")

    if soc == 100 and last_notify_date != today_str:
        logger.info("_________Battery full detected with soc: %s%%_________", soc)
        body = _get_user_setting(
            inverter_ctx,
            "BATTERY_FULL_NOTIFY_BODY",
            settings.get_battery_full_notify_body(),
        )
        fcm_service.battery_full_notify(body, inverter_ctx)
        _save_user_setting(inverter_ctx, "LAST_BATTERY_FULL_NOTIFY_DATE", today_str)


def has_input1_data(json_data: dict) -> bool:
    """Return True when payload has minimum fields from ReadInput1."""
    return isinstance(json_data, dict) and ("fac" in json_data)


def _build_hourly_chart_item(inverter_data: dict) -> list:

    device_time = datetime.strptime(inverter_data["deviceTime"], "%Y-%m-%d %H:%M:%S")
    item_id = device_time.strftime("%Y%m%d%H%M")
    grid = inverter_data["p_to_grid"] - inverter_data["p_to_user"]
    consumption = (
        inverter_data["p_inv"]
        + inverter_data["p_to_user"]
        - inverter_data["p_rec"]
        + inverter_data["p_eps"]
    )
    return [
        item_id,
        inverter_data["deviceTime"],
        inverter_data["p_pv"],
        inverter_data["p_discharge"] - inverter_data["p_charge"],
        grid,
        consumption,
        inverter_data["soc"],
    ]


def _pg_upsert_inverter_data(inverter_data: dict, sleep_time: int) -> None:
    """Write inverter runtime/energy data to PostgreSQL when inverter is mapped."""
    inverter_id_str = inverter_data.get("_inverter_id")
    if not inverter_id_str:
        return
    try:
        import uuid
        from datetime import datetime as _dt
        from multi_tenant.db import get_db_session
        from multi_tenant import repository as repo

        inverter_id = uuid.UUID(inverter_id_str)
        session = next(get_db_session())
        try:
            device_time_str = inverter_data.get("deviceTime", "")
            try:
                device_time = _dt.strptime(device_time_str, "%Y-%m-%d %H:%M:%S")
            except Exception:
                device_time = _dt.now()

            repo.upsert_inverter_latest_state(session, inverter_id, device_time, inverter_data)

            pv = int(inverter_data.get("p_pv", 0))
            battery = int(inverter_data.get("p_discharge", 0)) - int(inverter_data.get("p_charge", 0))
            grid = int(inverter_data.get("p_to_grid", 0)) - int(inverter_data.get("p_to_user", 0))
            consumption = (
                int(inverter_data.get("p_inv", 0))
                + int(inverter_data.get("p_to_user", 0))
                - int(inverter_data.get("p_rec", 0))
                + int(inverter_data.get("p_eps", 0))
            )
            soc = int(inverter_data.get("soc", 0))
            repo.upsert_hourly_chart(
                session,
                inverter_id,
                device_time,
                sleep_time,
                pv,
                battery,
                grid,
                consumption,
                soc,
                device_time.second,
            )

            # Handle daily chart - skip at midnight (00:00-00:59) to match SQLite behavior
            if device_time.hour == 0 and device_time.minute == 0:
                logger.debug("Skipping daily_chart insert at midnight for inverter_id=%s", inverter_id_str)
                session.commit()
                return

            today = device_time.date()
            
            # Check if energy day fields are present in inverter_data
            has_energy_fields = any(field in inverter_data for field in [
                "e_pv_day", "e_chg_day", "e_dischg_day", 
                "e_to_user_day", "e_to_grid_day", "e_inv_day", "e_eps_day", "e_rec_day"
            ])
            
            if not has_energy_fields:
                logger.warning(
                    "Inverter data missing energy day fields. Cannot update daily_chart for inverter_id=%s. "
                    "Available fields: %s", 
                    inverter_id_str, 
                    sorted(inverter_data.keys())
                )
                session.commit()
                return
            
            e_pv = round(float(inverter_data.get("e_pv_day", 0)), 1)
            e_bat_charge = round(float(inverter_data.get("e_chg_day", 0)), 1)
            e_bat_discharge = round(float(inverter_data.get("e_dischg_day", 0)), 1)
            e_grid_import = round(float(inverter_data.get("e_to_user_day", 0)), 1)
            e_grid_export = round(float(inverter_data.get("e_to_grid_day", 0)), 1)
            e_consumption = (
                float(inverter_data.get("e_inv_day", 0))
                + float(inverter_data.get("e_to_user_day", 0))
                + float(inverter_data.get("e_eps_day", 0))
                - float(inverter_data.get("e_rec_day", 0))
            )
            
            logger.debug(
                "Upserting daily_chart for inverter_id=%s, date=%s: pv=%.1f, battery_charged=%.1f, battery_discharged=%.1f, "
                "grid_import=%.1f, grid_export=%.1f, consumption=%.1f",
                inverter_id_str, today, e_pv, e_bat_charge, e_bat_discharge,
                e_grid_import, e_grid_export, e_consumption
            )
            
            repo.upsert_daily_chart(
                session,
                inverter_id,
                today,
                today.year,
                today.month,
                e_pv,
                e_bat_charge,
                e_bat_discharge,
                e_grid_import,
                e_grid_export,
                e_consumption,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    except Exception as exc:
        logger.warning("PostgreSQL upsert failed for inverter_id=%s: %s", inverter_id_str, exc)


def _resolve_inverter_context(inverter_data: dict) -> dict | None:
    """Resolve inverter/user context for multi-tenant operations.

    Returns dict with keys: id, user_id, name
    """
    if not USE_PG:
        return None
    try:
        import uuid
        from multi_tenant.db import get_db_session
        from multi_tenant import repository as repo

        inverter_id_str = inverter_data.get("_inverter_id")
        session = next(get_db_session())
        try:
            inverter = None
            should_commit = False
            if inverter_id_str:
                try:
                    inverter = repo.get_inverter_by_id(session, uuid.UUID(str(inverter_id_str)))
                    if inverter is not None and not getattr(inverter, "is_active", True):
                        inverter = None
                except Exception:
                    inverter = None
            if inverter is None:
                dongle_serial = inverter_data.get("dongle_serial") or inverter_data.get("serial")
                if dongle_serial:
                    inverter = repo.get_inverter_by_dongle_serial(session, str(dongle_serial))
            if inverter is None:
                invert_serial = inverter_data.get("serial")
                if invert_serial:
                    inverter = repo.get_inverter_by_invert_serial(session, str(invert_serial))
            if inverter is None:
                return None

            parsed_invert_serial = str(inverter_data.get("serial") or "").strip()
            current_invert_serial = str(inverter.invert_serial or "").strip()
            if parsed_invert_serial and parsed_invert_serial != current_invert_serial:
                existed = repo.get_inverter_by_invert_serial(session, parsed_invert_serial)
                if existed is None or existed.id == inverter.id:
                    repo.update_inverter_invert_serial(session, inverter, parsed_invert_serial)
                    should_commit = True
                else:
                    logger.warning(
                        "Skip inverter serial auto-update for inverter_id=%s because serial %s belongs to inverter_id=%s",
                        inverter.id,
                        parsed_invert_serial,
                        existed.id,
                    )

            if should_commit:
                session.commit()

            inverter_data["_inverter_id"] = str(inverter.id)
            return {
                "id": str(inverter.id),
                "user_id": str(inverter.user_id),
                "name": inverter.name,
            }
        finally:
            session.close()
    except Exception as exc:
        logger.debug("Failed to resolve inverter context: %s", exc)
        return None


def _get_user_setting(inverter_ctx: dict | None, key: str, default: str) -> str:
    if not USE_PG or not inverter_ctx or not inverter_ctx.get("user_id"):
        return default
    try:
        import uuid
        from multi_tenant.db import get_db_session
        from multi_tenant import repository as repo

        session = next(get_db_session())
        try:
            # Try inverter scope first
            if inverter_ctx.get("id"):
                val = repo.get_scoped_setting(session, "inverter", inverter_ctx["id"], key)
                if val is not None:
                    return val

            # Fallback to user scope
            value = repo.get_user_setting(session, uuid.UUID(inverter_ctx["user_id"]), key)
            return value if value is not None else default
        finally:
            session.close()
    except Exception:
        return default


def _save_user_setting(inverter_ctx: dict | None, key: str, value: str):
    if not USE_PG or not inverter_ctx or not inverter_ctx.get("user_id"):
        return
    try:
        import uuid
        from multi_tenant.db import get_db_session
        from multi_tenant import repository as repo

        session = next(get_db_session())
        try:
            if inverter_ctx.get("id"):
                repo.upsert_scoped_setting(session, "inverter", inverter_ctx["id"], key, value)
            else:
                repo.upsert_user_setting(session, uuid.UUID(inverter_ctx["user_id"]), key, value)
            session.commit()
        finally:
            session.close()
    except Exception as exc:
        logger.error("Failed to save user setting %s: %s", key, exc)


def _to_bool(value: str) -> bool:
    return str(value).lower() == "true"


def _to_int(value: str, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


def _get_sleep_time(inverter_ctx: dict | None = None) -> int:
    configured_sleep_time = _normalize_sleep_time(config.get("SLEEP_TIME", 30))
    if not USE_PG or not inverter_ctx:
        return configured_sleep_time
    
    user_id = inverter_ctx.get("user_id")
    if not user_id:
        return configured_sleep_time
    
    # Check cache first
    cached_value = get_cached_sleep_time(str(user_id))
    if cached_value is not None:
        return cached_value
    
    # Query and cache
    return set_cached_sleep_time(
        str(user_id),
        _get_user_setting(inverter_ctx, "SLEEP_TIME", str(configured_sleep_time)),
        configured_sleep_time,
    )


def _migrate_sqlite_to_pg_if_needed() -> None:
    """One-time migration of legacy SQLite runtime data into PostgreSQL.

    Migration is best-effort and keyed to inverter resolved from DONGLE_SERIAL.
    """
    if not USE_PG:
        return
    sqlite_db = config.get("DB_NAME")
    if not sqlite_db or not path.exists(sqlite_db):
        return
    marker = f"{sqlite_db}.migrated_to_pg"
    if path.exists(marker):
        return

    try:
        from multi_tenant.db import get_db_session
        from multi_tenant import repository as repo

        session = next(get_db_session())
        try:
            dongle_serial = config.get("DONGLE_SERIAL", "")
            inverter = None
            if dongle_serial:
                inverter = repo.get_inverter_by_dongle_serial(session, dongle_serial)
            if inverter is None:
                logger.warning("Skip SQLite→PG migration: no inverter matched DONGLE_SERIAL")
                return

            conn = sqlite3.connect(sqlite_db)
            cur = conn.cursor()
            try:
                # hourly_chart -> hourly_chart_v2
                try:
                    rows = cur.execute(
                        "SELECT datetime, pv, battery, grid, consumption, soc FROM hourly_chart ORDER BY datetime"
                    ).fetchall()
                    for r in rows:
                        dt = datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
                        repo.upsert_hourly_chart(
                            session,
                            inverter.id,
                            dt,
                            int(config["SLEEP_TIME"]),
                            int(r[1] or 0),
                            int(r[2] or 0),
                            int(r[3] or 0),
                            int(r[4] or 0),
                            int(r[5] or 0),
                        )
                except Exception as exc:
                    logger.warning("Skip hourly migration: %s", exc)

                # daily_chart -> daily_chart_v2
                try:
                    rows = cur.execute(
                        "SELECT date, year, month, pv, battery_charged, battery_discharged, grid_import, grid_export, consumption FROM daily_chart ORDER BY date"
                    ).fetchall()
                    for r in rows:
                        d = datetime.strptime(r[0], "%Y-%m-%d").date()
                        repo.upsert_daily_chart(
                            session,
                            inverter.id,
                            d,
                            int(r[1]),
                            int(r[2]),
                            float(r[3] or 0),
                            float(r[4] or 0),
                            float(r[5] or 0),
                            float(r[6] or 0),
                            float(r[7] or 0),
                            float(r[8] or 0),
                        )
                except Exception as exc:
                    logger.warning("Skip daily migration: %s", exc)

                # notification_history -> notification_history_v2
                try:
                    rows = cur.execute(
                        "SELECT title, body, notified_at, read FROM notification_history ORDER BY notified_at"
                    ).fetchall()
                    for r in rows:
                        n = repo.insert_notification(
                            session,
                            user_id=inverter.user_id,
                            title=str(r[0] or ""),
                            body=str(r[1] or ""),
                            inverter_id=inverter.id,
                        )
                        if r[2]:
                            try:
                                n.notified_at = datetime.strptime(r[2], "%Y-%m-%d %H:%M:%S")
                            except Exception:
                                pass
                        n.read = bool(r[3])
                except Exception as exc:
                    logger.warning("Skip notification migration: %s", exc)

                # settings -> scoped_settings (user)
                try:
                    rows = cur.execute("SELECT key, value FROM settings").fetchall()
                    for r in rows:
                        key = str(r[0])
                        if key in {"AUTH_ENABLED", "AUTH_USERNAME", "AUTH_PASSWORD", "AUTH_BYPASS_CIDR"}:
                            continue
                        repo.upsert_user_setting(session, inverter.user_id, key, str(r[1]))
                except Exception as exc:
                    logger.warning("Skip settings migration: %s", exc)

            finally:
                cur.close()
                conn.close()

            # device ids json -> user_device_tokens
            try:
                device_file = config.get("DEVICE_IDS_JSON_FILE", "devices.json")
                if path.exists(device_file):
                    with open(device_file, "r") as fh:
                        tokens = json.loads(fh.read())
                    if isinstance(tokens, list):
                        for token in tokens:
                            if isinstance(token, str) and token.strip():
                                repo.upsert_device_token(session, inverter.user_id, token.strip())
            except Exception as exc:
                logger.warning("Skip device token migration: %s", exc)

            session.commit()
            with open(marker, "w") as mf:
                mf.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            logger.info("SQLite→PostgreSQL migration completed")
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    except Exception as exc:
        logger.exception("SQLite→PostgreSQL migration failed: %s", exc)

async def handle_grid_status(json_data: dict, fcm_service: FCM, inverter_ctx: dict | None = None):
    if not has_input1_data(json_data):
        logger.debug(
            "Skip handle_grid_status because payload does not contain ReadInput1 fields (missing fac). Keys: %s",
            sorted(json_data.keys()) if isinstance(json_data, dict) else type(json_data),
        )
        return

    # is_grid_connected = True
    inverter_ctx = inverter_ctx or _resolve_inverter_context(json_data)
    is_grid_connected = json_data["fac"] > 0
    last_grid_connected = True
    disconnected_time = json_data["deviceTime"]
    if USE_PG:
        # In PG mode, read previous state from PG and update it atomically so that
        # duplicate notifications are not sent when the state did not change.
        if inverter_ctx and inverter_ctx.get("id"):
            from multi_tenant.db import get_db_session
            from multi_tenant import repository as repo

            session = next(get_db_session())
            try:
                inverter_id = inverter_ctx["id"]
                last_connected_str = repo.get_scoped_setting(session, "inverter", inverter_id, "mobile_is_connected")
                if last_connected_str is not None:
                    last_grid_connected = last_connected_str == "True"
                if not last_grid_connected:
                    # Grid was already disconnected — read the stored disconnection time
                    stored_disconnected_time = repo.get_scoped_setting(session, "inverter", inverter_id, "mobile_disconnected_time")
                    if stored_disconnected_time:
                        disconnected_time = stored_disconnected_time
                repo.upsert_scoped_setting(
                    session,
                    "inverter",
                    inverter_id,
                    "mobile_is_connected",
                    str(is_grid_connected),
                )
                session.commit()
            except Exception as e:
                session.rollback()
                logger.error(f"Error updating mobile connection state in PG: {e}")
            finally:
                session.close()
    else:
        if path.exists(config["STATE_FILE"]):
            with open(config["STATE_FILE"], 'r') as f:
                last_grid_connected = f.read() == "True"
            if not last_grid_connected:
                # Only get disconnected time from state file if disconnected from previous
                disconnected_time = datetime.fromtimestamp(
                    path.getmtime(config['STATE_FILE'])
                ).strftime("%Y-%m-%d %H:%M:%S")
    if not USE_PG:
        status_text = json_data["status_text"] if "status_text" in json_data else json_data["status"]
        if not is_grid_connected:
            logger.warning(
                "_________Inverter disconnected from GRID since: %s with status: \"%s\" (%s)_________",
                disconnected_time,
                status_text,
                json_data["status"],
            )
        else:
            logger.info(
                """_________Inverter currently connected to GRID with________
    ________Status: \"%s\" (%s) at deviceTime: %s with fac: %s Hz and vacr: %s V_________""",
                status_text,
                json_data["status"],
                json_data['deviceTime'],
                int(json_data['fac']) / 100,
                int(json_data['vacr']) / 10,
            )

    if last_grid_connected != is_grid_connected:
        current_history = []
        if USE_PG:
            # In PG mode, read current history from PG
            from multi_tenant.db import get_db_session
            from multi_tenant import repository as repo
            session = next(get_db_session())
            try:
                if inverter_ctx and inverter_ctx.get("id"):
                    inverter_id = inverter_ctx["id"]
                    history_str = repo.get_scoped_setting(session, "inverter", inverter_id, "mobile_history")
                    if history_str:
                        current_history = json.loads(history_str)
            except Exception as e:
                logger.error(f"Error reading mobile history from PG: {e}")
            finally:
                session.close()
        else:
            if path.exists(config['HISTORY_FILE']):
                with open(config['HISTORY_FILE'], 'r') as f_history:
                    current_history = json.loads(f_history.read())
        
        if len(current_history) == int(config["HISTORY_COUNT"]):
            del current_history[len(current_history) - 1]
        current_history.insert(0, {
            "type": "ON_GRID" if is_grid_connected else "OFF_GRID",
            "time": json_data["deviceTime"],
        })
        
        if USE_PG:
            # Write to PG
            from multi_tenant.db import get_db_session
            from multi_tenant import repository as repo
            session = next(get_db_session())
            try:
                if inverter_ctx and inverter_ctx.get("id"):
                    inverter_id = inverter_ctx["id"]
                    repo.upsert_scoped_setting(session, "inverter", inverter_id, "mobile_history", json.dumps(current_history))
                    if not is_grid_connected:
                        # Persist the disconnection timestamp so subsequent cycles can show it
                        repo.upsert_scoped_setting(session, "inverter", inverter_id, "mobile_disconnected_time", json_data["deviceTime"])
                    else:
                        # Clear the stored disconnection time when reconnected
                        repo.upsert_scoped_setting(session, "inverter", inverter_id, "mobile_disconnected_time", "")
                    session.commit()
            except Exception as e:
                session.rollback()
                logger.error(f"Error updating mobile state in PG: {e}")
            finally:
                session.close()
        else:
            # Write to file
            with open(config['HISTORY_FILE'], 'w') as f_history_w:
                f_history_w.write(json.dumps(current_history))
            with open(config["STATE_FILE"], "w") as fw:
                fw.write(str(is_grid_connected))
        
        if is_grid_connected:
            grid_state_notify_enabled = _to_bool(
                _get_user_setting(
                    inverter_ctx,
                    "GRID_STATE_NOTIFY_ENABLED",
                    "true",
                )
            )
            if grid_state_notify_enabled:
                fcm_service.ongrid_notify(inverter_ctx)
            play_audio("has-grid.mp3")
        else:
            logger.warning("All json data: %s", json_data)
            grid_state_notify_enabled = _to_bool(
                _get_user_setting(
                    inverter_ctx,
                    "GRID_STATE_NOTIFY_ENABLED",
                    "true",
                )
            )
            if grid_state_notify_enabled:
                fcm_service.offgrid_notify(inverter_ctx)
            play_audio("lost-grid.mp3", 5)
    else:
        logger.info("State did not change. Skip play notify audio")
    dectect_off_grid_warning(
        is_grid_connected, json_data["p_pv"], json_data["p_eps"], json_data["soc"], fcm_service, inverter_ctx)
    detect_battery_full(json_data["soc"], fcm_service, inverter_ctx)


async def initialize_web_socket_client(fcm_service: FCM, old_ws_client: WebSocketClient | None = None):
    if old_ws_client is not None:
        # Reinitialize the web socket client for next iteration
        logger.info("Stopping old web socket client")
        await old_ws_client.stop()
        logger.info("Reinitializing web socket client for next iteration")
    ws_client = WebSocketClient(
        logger=logger, host=config["HOST"], port=int(config["PORT"]))
    fcm_service._set_ws_client(ws_client)
    ws_client.start()
    return ws_client

async def process_inverter_data(
    inverter_data: dict,
    fcm_service: FCM,
    run_web_view: bool,
    db_connection: sqlite3.Connection | None = None,
    ws_client: WebSocketClient | None = None,
) -> WebSocketClient | None:
    inverter_ctx = None
    if USE_PG:
        inverter_ctx = _resolve_inverter_context(inverter_data)
        if inverter_ctx is None:
            logger.warning(
                "Skip data from unregistered inverter (dongle_serial=%s, invert_serial=%s)",
                inverter_data.get("dongle_serial"),
                inverter_data.get("serial"),
            )
            return ws_client
        inverter_data["_inverter_id"] = inverter_ctx["id"]

    await handle_grid_status(inverter_data, fcm_service, inverter_ctx)
    
    sleep_time = _get_sleep_time(inverter_ctx)

    # Store data in database regardless of web viewer status
    hourly_chart_item = None
    if USE_PG:
        _pg_upsert_inverter_data(inverter_data, sleep_time)
        hourly_chart_item = _build_hourly_chart_item(inverter_data)
    elif db_connection is not None:
        # SQLite mode: insert hourly and daily chart data
        hourly_chart_item = database.insert_hourly_chart(
            db_connection,
            inverter_data,
            sleep_time,
        )
        database.insert_daily_chart(db_connection, inverter_data)
        dectect_abnormal_usage(db_connection, fcm_service, inverter_ctx)
    
    # Only send to web viewer if enabled
    if not run_web_view or ws_client is None:
        return ws_client

    timeout_duration = sleep_time * 3

    try:
        sent = await asyncio.wait_for(
            ws_client.send_json({
                "inverter_data": inverter_data,
                "hourly_chart_item": hourly_chart_item
            }),
            timeout=timeout_duration
        )
        if not sent:
            logger.error("Failed to send data to web socket")
            ws_client = await initialize_web_socket_client(fcm_service, ws_client)
    except asyncio.TimeoutError:
        logger.error("Timeout waiting for web socket to send data for %s seconds", timeout_duration)
        ws_client = await initialize_web_socket_client(fcm_service, ws_client)

    return ws_client

async def main():
    try:
        logger.info("Grid connect watch working on mode: %s",
                    config["WORKING_MODE"])

        if USE_PG:
            # Keep Alembic migration in app startup so python app.py is the only entrypoint.
            try:
                from pathlib import Path
                from alembic import command as alembic_command
                from alembic.config import Config as AlembicConfig

                alembic_cfg = AlembicConfig(str(Path(__file__).with_name("alembic.ini")))
                alembic_cfg.attributes["skip_logging_config"] = True
                logger.info("Running Alembic migrations (upgrade head)")
                alembic_command.upgrade(alembic_cfg, "head")
                logger.info("Alembic migration completed")
            except Exception as exc:
                logger.exception("Alembic migration failed: %s", exc)
                raise

        fcm_service = FCM(logger, config)
        run_web_view = config["RUN_WEB_VIEWER"] == "True"
        if USE_PG:
            _migrate_sqlite_to_pg_if_needed()
        if config["WORKING_MODE"] == DONGLE_MODE:
            if run_web_view:
                db_connection = None
                if not USE_PG:
                    db_connection = sqlite3.connect(
                        config["DB_NAME"]) if "DB_NAME" in config else None
                    from migration import run_migration
                    run_migration(db_connection, logger)
                    settings.load_settings(db_connection)
                else:
                    logger.info("PostgreSQL configured: SQLite runtime storage is disabled")
                from web_viewer import WebViewer
                webViewer = WebViewer(logger)
                webViewer.start()
                time.sleep(1)
                ws_client = await initialize_web_socket_client(fcm_service)
            dongle = dongle_handler.Dongle(logger, config)
            while True:
                try:
                    timeout_duration = _normalize_sleep_time(config.get("SLEEP_TIME", 30)) * 3
                    inverter_data = None
                    try:
                        inverter_data = await asyncio.wait_for(
                            asyncio.to_thread(dongle.get_dongle_input),
                            timeout=timeout_duration
                        )
                    except asyncio.TimeoutError:
                        logger.error("Timeout waiting for dongle input for %s seconds", timeout_duration)
                    if inverter_data is not None:
                        ws_client = await process_inverter_data(
                            inverter_data,
                            fcm_service,
                            run_web_view,
                            db_connection if run_web_view else None,
                            ws_client if run_web_view else None,
                        )
                except Exception as e:
                    logger.exception("Got error when get dongle input %s", e)
                current_sleep_time = _normalize_sleep_time(config.get("SLEEP_TIME", 30))
                if inverter_data is not None and USE_PG:
                    current_sleep_time = _get_sleep_time(_resolve_inverter_context(inverter_data))
                logger.info("Waiting for %s seconds before next check", current_sleep_time)
                await asyncio.sleep(current_sleep_time)
        elif config["WORKING_MODE"] == SERVER_MODE:
            from dongle_server import DongleServer
            if run_web_view:
                db_connection = None
                if not USE_PG:
                    db_connection = sqlite3.connect(
                        config["DB_NAME"]) if "DB_NAME" in config else None
                    from migration import run_migration
                    run_migration(db_connection, logger)
                    settings.load_settings(db_connection)
                else:
                    logger.info("PostgreSQL configured: SQLite runtime storage is disabled")
                from web_viewer import WebViewer
                webViewer = WebViewer(logger)
                webViewer.start()
                time.sleep(1)
                ws_client = await initialize_web_socket_client(fcm_service)
            dongle_server = DongleServer(logger, config)
            # Start the server in a background task
            server_task = asyncio.create_task(dongle_server.start_server())
            logger.info("Waiting for dongle connections on port %s",
                        config.get("SERVER_MODE_PORT", 4346))
            while True:
                # Always use SERVER_MODE_TIMEOUT if present; otherwise use a safe default
                # which is large enough to cover per-user sleep_time (user max 30s).
                # Default chosen: 90 seconds
                try:
                    timeout_duration = int(config.get("SERVER_MODE_TIMEOUT", 90))
                except Exception:
                    timeout_duration = 90
                try:
                    inverter_data = await dongle_server.wait_for_data(
                        timeout=timeout_duration
                    )
                    while inverter_data is not None:
                        ws_client = await process_inverter_data(
                            inverter_data,
                            fcm_service,
                            run_web_view,
                            db_connection if run_web_view else None,
                            ws_client if run_web_view else None,
                        )
                        inverter_data = dongle_server.get_pending_data()
                except Exception as e:
                    logger.exception("Got error in SERVER_MODE %s", e)
                logger.info("Waiting for next dongle data (timeout: %s seconds)", timeout_duration)
        else:
            http = http_handler.Http(logger, config)
            while True:
                try:
                    inverter_data = await asyncio.to_thread(http.get_run_time_data)
                    await handle_grid_status(inverter_data, fcm_service, None)
                except Exception as e:
                    logger.exception("Got error when get http input %s", e)
                logger.info("Waiting for %s seconds before next check", config["SLEEP_TIME"])
                await asyncio.sleep(int(config["SLEEP_TIME"]))
    except Exception as e:
        logger.exception("Got error when run main %s", e)
        try:
            if run_web_view:
                webViewer.stop()
                ws_client.stop()
        except NameError as e:
            pass
        except Exception as e:
            logger.exception(
                "Got error when stop web viewer or web socket %s", e)
        exit(1)

if __name__ == "__main__":
    asyncio.run(main())
