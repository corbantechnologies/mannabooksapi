from datetime import datetime, timezone, date
from typing import Optional, Any


def to_naive_utc(dt: Any) -> Optional[datetime]:
    """
    Safely converts strings or datetime objects to timezone-naive UTC datetimes
    for asyncpg / PostgreSQL TIMESTAMP WITHOUT TIME ZONE columns.
    Prevents 'can't subtract offset-naive and offset-aware datetimes' DataErrors.
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        s = dt.strip()
        if not s:
            return None
        try:
            cleaned = s.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(cleaned)
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except Exception:
            return None
    if isinstance(dt, datetime):
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    if isinstance(dt, date):
        return datetime(dt.year, dt.month, dt.day)
    return None


def utcnow() -> datetime:
    """
    Current UTC time as a naive datetime.

    Database columns are TIMESTAMP WITHOUT TIME ZONE and store UTC by convention, so this is the
    single replacement for the deprecated datetime.utcnow().
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
