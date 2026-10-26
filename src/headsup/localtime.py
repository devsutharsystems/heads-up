"""The local day used for the daily interrupt limit and the per-day cost totals."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Kolkata")


def local_date(dt):
    return dt.astimezone(LOCAL_TZ).date()


def day_bounds(dt):
    """UTC [start, end) of the local calendar day containing dt."""
    day = local_date(dt)
    start = datetime.combine(day, time(0), tzinfo=LOCAL_TZ)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=LOCAL_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
