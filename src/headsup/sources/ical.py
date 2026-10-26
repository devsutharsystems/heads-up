"""iCal watcher: fetch one https calendar feed, expand recurring events over the next 48 h,
and report:
  event_upcoming   once per occurrence, on the first poll where 0 < minutes-to-start <= lead
                   (REMINDER_LEAD_MINUTES, default 30). Further-away events are not reported.
  event_moved      as soon as the start differs from the stored start (any time inside 48 h).
  event_cancelled  as soon as STATUS:CANCELLED is seen (any time inside 48 h).
Time-to-start is start_time minus occurred_at (the poll time). An all-day event starts at 00:00 UTC.

Fetch rules: https only, no userinfo, redirects are NOT followed (a 3xx is an error),
10 s timeout, body capped at 2 MB. Times are UTC inside; a floating time (no zone) is taken as UTC.
The feed URL is a secret address, so it is never put into an Event or an error message.
A moved event is only seen while it is inside the 48 h window both before and after the move.
"""

import hashlib
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

import recurring_ical_events
from icalendar import Calendar

from headsup.schema import Event

WINDOW = timedelta(hours=48)
DEFAULT_LEAD_MINUTES = 30  # the rubric's "soon"
MAX_LEAD_MINUTES = 48 * 60  # a longer lead than the window could never fire
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 10.0
BODY_MAX = 2000


class IcalError(Exception):
    pass


def _utc(value):
    """date -> midnight UTC (all-day); naive datetime -> UTC; aware datetime -> converted to UTC."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def _hash(event_id, title, kind, start):
    """Includes the occurrence start, so each occurrence (and each new start after a move) is its own event."""
    return hashlib.sha256(f"ical|{event_id}|{title}|{kind}|{start.isoformat()}".encode()).hexdigest()


class IcalSource:
    def __init__(self, url, client, store, now=None, lead_minutes=DEFAULT_LEAD_MINUTES):
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise IcalError("calendar URL must be https without credentials")
        if not isinstance(lead_minutes, int) or isinstance(lead_minutes, bool) or not 0 < lead_minutes <= MAX_LEAD_MINUTES:
            raise IcalError(f"reminder lead must be a whole number of minutes from 1 to {MAX_LEAD_MINUTES}")
        self.lead = timedelta(minutes=lead_minutes)
        self._url = url
        self.client = client
        self.store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _fetch(self):
        with self.client.stream("GET", self._url, follow_redirects=False, timeout=TIMEOUT) as resp:
            if resp.status_code != 200:
                raise IcalError(f"calendar fetch failed: HTTP {resp.status_code}")
            data = bytearray()
            for chunk in resp.iter_bytes():
                data += chunk
                if len(data) > MAX_BYTES:
                    raise IcalError("calendar feed is larger than 2 MB")
        return bytes(data)

    def poll(self):
        now = self._now()
        cal = Calendar.from_ical(self._fetch())
        recurring = {str(c.get("UID")) for c in cal.walk("VEVENT") if c.get("RRULE") or c.get("RDATE")}
        out = []
        for ev in recurring_ical_events.of(cal).between(now, WINDOW):
            uid = str(ev.get("UID"))
            start_raw = ev.get("DTSTART").dt
            start = _utc(start_raw)
            rid = ev.get("RECURRENCE-ID")
            key = f"{uid}|{_utc(rid.dt).isoformat() if rid else start.isoformat()}" if uid in recurring else uid
            title = str(ev.get("SUMMARY") or "(no title)")
            all_day = isinstance(start_raw, date) and not isinstance(start_raw, datetime)
            until = start - now
            if str(ev.get("STATUS") or "").upper() == "CANCELLED":
                kind = "event_cancelled"
            else:
                stored = self.store.get_calendar_start(key)
                self.store.set_calendar_start(key, start)  # tracked for the whole window so moves are seen early
                reminder = f"{key}|{start.isoformat()}"
                if stored is not None and stored != start:
                    kind = "event_moved"
                    if timedelta(0) < until <= self.lead:
                        self.store.mark_reminded(reminder)  # the move message already says it starts soon
                elif timedelta(0) < until <= self.lead and not self.store.was_reminded(reminder):
                    kind = "event_upcoming"
                    self.store.mark_reminded(reminder)
                else:
                    continue
            event_id = f"ical:{key}"
            out.append(Event(id=event_id, source="calendar", kind=kind, title=title,
                             body=str(ev.get("DESCRIPTION") or "")[:BODY_MAX], url=str(ev.get("URL") or ""),
                             occurred_at=now, fetched_at=now, content_hash=_hash(event_id, title, kind, start),
                             start_time=start, all_day=all_day))
        return sorted(out, key=lambda e: e.start_time)
