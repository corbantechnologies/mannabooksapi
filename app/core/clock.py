from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from app.core.config import get_settings

settings = get_settings()

try:
    NAIROBI_TZ = ZoneInfo(settings.TZ)
except (ZoneInfoNotFoundError, Exception):
    NAIROBI_TZ = timezone(timedelta(hours=3))



def now_nairobi() -> datetime:
    """Current timestamp localized to Africa/Nairobi."""
    return datetime.now(NAIROBI_TZ)


def today_nairobi() -> date:
    """Current local date in Africa/Nairobi."""
    return now_nairobi().date()
