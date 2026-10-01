"""Canonical timestamp formatting for every timestamp column in the database.

Timestamp columns are stored as TEXT and compared/ordered with plain string
comparisons in SQL, so every writer must use one fixed-width, lexicographically
sortable representation. Diverging formats silently break `ORDER BY <ts>`
because the date/time separators differ in ASCII value (`'T'` > `' '`), which
silently interleaves rows out of chronological order.

Use `format_datetime()` on the way in and `parse_datetime()` on the way out
instead of calling `strftime`/`isoformat` directly.
"""

from datetime import datetime

# Server-local wall clock, second precision, no timezone suffix.
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_datetime(moment: datetime | None = None) -> str:
    """Format a timestamp for storage (defaults to now, server-local)."""
    return (moment or datetime.now()).strftime(DATETIME_FORMAT)


def parse_datetime(raw: str | None) -> datetime | None:
    """Parse a stored timestamp, returning None when absent or malformed.

    Tolerates the legacy `datetime.isoformat()` form ("YYYY-MM-DDTHH:MM:SS.ffffff")
    that earlier writers persisted, so rows predating DATETIME_FORMAT stay readable.
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
