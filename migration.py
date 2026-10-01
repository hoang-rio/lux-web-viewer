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
    "UPDATE settings SET key = 'ABNORMAL_CHECK_COOLDOWN_HOURS' WHERE key = 'ABNORMAL_SKIP_CHECK_HOURS'"
]
def execute_migration_sql(id: int, sql: str, cursor: sqlite3.Cursor) -> None:
    global logger
    logger.info(f"Executing sql: \"{sql}\"")
    try:
        cursor.execute(sql)
    except sqlite3.OperationalError as e:
        # Re-running from scratch is now reachable (see run_migration), so an
        # already-applied ADD COLUMN must not abort the whole sequence.
        if "duplicate column name" in str(e):
            logger.info("Column already exists, skipping migration %s", id)
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
