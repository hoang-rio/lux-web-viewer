import sqlite3
import time
import logging

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)

MIGRATIONS_SQL = [
    "CREATE TABLE IF NOT EXISTS migration (id INTEGER PRIMARY KEY, applied_at TEXT)",
    "CREATE TABLE IF NOT EXISTS hourly_chart (id VARCHAR PRIMARY KEY, datetime TEXT, pv INTEGER, battery INTEGER, grid INTEGER, consumption INTEGER, soc INTERGER)",
    "CREATE TABLE IF NOT EXISTS daily_chart (id VARCHAR PRIMARY KEY, year INTEGER, month INTEGER, date TEXT, pv INTEGER, battery_charged INTEGER, battery_discharged INTEGER, grid_import INTEGER, grid_export INTEGER, consumption INTEGER)",
    "ALTER TABLE daily_chart ADD COLUMN updated TEXT",
    "CREATE TABLE IF NOT EXISTS notification_history (id INTEGER PRIMARY KEY AUTOINCREMENT, notified_at TEXT, title TEXT, body TEXT)",
    "ALTER TABLE notification_history ADD COLUMN read INTEGER DEFAULT 0",
    "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)",
    "UPDATE settings SET key = 'ABNORMAL_CHECK_COOLDOWN_HOURS' WHERE key = 'ABNORMAL_SKIP_CHECK_HOURS'",
    "CREATE TABLE IF NOT EXISTS tuya_devices (id TEXT PRIMARY KEY, name TEXT NOT NULL, ip TEXT NOT NULL, local_key TEXT NOT NULL, protocol_version TEXT NOT NULL DEFAULT '3.3', device_type TEXT NOT NULL DEFAULT 'outlet', created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS automation_triggers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, when_start_time TEXT, when_end_time TEXT, when_days TEXT, conditions TEXT NOT NULL DEFAULT '[]', action_type TEXT NOT NULL, action_device_id TEXT, action_params TEXT, cooldown_seconds INTEGER NOT NULL DEFAULT 300, last_triggered_at TEXT, created_at TEXT, FOREIGN KEY (action_device_id) REFERENCES tuya_devices(id))",
    "CREATE TABLE IF NOT EXISTS trigger_history (id INTEGER PRIMARY KEY AUTOINCREMENT, trigger_id INTEGER NOT NULL, triggered_at TEXT NOT NULL, status TEXT DEFAULT 'success', message TEXT, FOREIGN KEY (trigger_id) REFERENCES automation_triggers(id) ON DELETE CASCADE)",
    "ALTER TABLE trigger_history ADD COLUMN actions_detail TEXT",
    # trigger_engine/actions.py used to write notified_at as datetime.now().isoformat()
    # ("YYYY-MM-DDTHH:MM:SS.ffffff") while fcm.py wrote "%Y-%m-%d %H:%M:%S". The two
    # formats do not sort against each other as strings ('T' > ' '), so ORDER BY
    # notified_at mixed old and new rows incorrectly. Rewrite the ISO rows in place.
    "UPDATE notification_history SET notified_at = replace(substr(notified_at, 1, 19), 'T', ' ') WHERE notified_at LIKE '%T%'",
    # Same problem in trigger_engine: storage.py wrote these as
    # datetime.now().isoformat() ("YYYY-MM-DDTHH:MM:SS.ffffff") while the rest of
    # the database uses "%Y-%m-%d %H:%M:%S". Rewrite the ISO rows in place so
    # "ORDER BY triggered_at DESC" and the per-trigger history trim stay correct.
    "UPDATE trigger_history SET triggered_at = replace(substr(triggered_at, 1, 19), 'T', ' ') WHERE triggered_at LIKE '%T%'",
    "UPDATE automation_triggers SET last_triggered_at = replace(substr(last_triggered_at, 1, 19), 'T', ' ') WHERE last_triggered_at LIKE '%T%'",
    "UPDATE automation_triggers SET created_at = replace(substr(created_at, 1, 19), 'T', ' ') WHERE created_at LIKE '%T%'",
    "UPDATE tuya_devices SET created_at = replace(substr(created_at, 1, 19), 'T', ' ') WHERE created_at LIKE '%T%'"
]
def execute_migration_sql(id: int, sql: str, cursor: sqlite3.Cursor) -> None:
    global logger
    logger.info(f"Executing sql: \"{sql}\"")
    try:
        cursor.execute(sql)
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            logger.info(f"Column already exists, skipping migration {id}")
        else:
            raise
    cursor.execute(
        "INSERT INTO migration (id, applied_at) VALUES (?, ?)",
        (id, time.time()),
    )

def run_migration(db_connection: sqlite3.Connection | None = None, _logger: logging.Logger | None = None) -> None:
    global logger
    if _logger is not None:
        logger = _logger
    if db_connection is None:
        from dotenv import dotenv_values
        from os import environ
        config: dict = {
            **dotenv_values(".env"),
            **environ
        }
        db_name = config["DB_NAME"]
        conn = sqlite3.connect(db_name)
    else:
        conn = db_connection
    logger.info("Running migration")
    cursor = conn.cursor()
    has_migration_table = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='migration'"
    ).fetchone()
    if has_migration_table is None:
        next_id = 1
    else:
        last_migration = cursor.execute(
            "SELECT id, applied_at FROM migration ORDER BY id DESC LIMIT 1"
        ).fetchone()
        # An existing but empty migration table means the tracking rows were
        # never committed (DDL auto-commits, so a crash between the first
        # CREATE TABLE and the first INSERT leaves exactly this state).
        # Treat it as "nothing applied" instead of silently doing nothing.
        next_id = last_migration[0] + 1 if last_migration is not None else 1
        if last_migration is None:
            logger.warning(
                "Migration table exists but is empty; "
                "rebuilding schema from the first migration"
            )
    if len(MIGRATIONS_SQL) < next_id:
        logger.info("Nothing to migrate")
        cursor.close()
        return
    logger.info(f"Migrating since {next_id}")
    pending_migrations = MIGRATIONS_SQL[next_id - 1:]
    for id, sql in enumerate(pending_migrations, start=next_id):
        execute_migration_sql(id, sql, cursor)
    conn.commit()
    cursor.close()

if __name__ == '__main__':
    run_migration()
