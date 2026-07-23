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
    "CREATE TABLE IF NOT EXISTS trigger_history (id INTEGER PRIMARY KEY AUTOINCREMENT, trigger_id INTEGER NOT NULL, triggered_at TEXT NOT NULL, status TEXT DEFAULT 'success', message TEXT, actions_detail TEXT, FOREIGN KEY (trigger_id) REFERENCES automation_triggers(id) ON DELETE CASCADE)"
]
def execute_migration_sql(id: int, sql: str, cursor: sqlite3.Cursor) -> None:
    global logger
    logger.info(f"Executing sql: \"{sql}\"")
    cursor.execute(sql)
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
        logger.info("Start migration")
        for id, sql in enumerate(MIGRATIONS_SQL, start=1):
            execute_migration_sql(id, sql, cursor)
        conn.commit()
    else:
        last_migration = cursor.execute(
            "SELECT id, applied_at FROM migration ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last_migration is not None:
            last_id, _ = last_migration
            if len(MIGRATIONS_SQL) <= last_id:
                logger.info("Nothing to migrate")
                return
            next_id = last_id + 1
            logger.info(f"Migrating since {next_id}")
            pending_migrations = MIGRATIONS_SQL[last_id:]
            for id, sql in enumerate(pending_migrations, start=next_id):
                execute_migration_sql(id, sql, cursor)
            conn.commit()
    cursor.close()

if __name__ == '__main__':
    run_migration()
