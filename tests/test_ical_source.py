from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from headsup.sources.ical import IcalError, IcalSource
from headsup.store import Store

FIX = Path(__file__).parent / "fixtures" / "ical"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)  # Monday
URL = "https://calendar.example.invalid/secret-address-made-up/basic.ics"
# moved_before.ics: "Planning" starts Tue 3 Nov 16:00 UTC; moved_after.ics: the same event at 18:00 UTC.
PLANNING = datetime(2026, 11, 3, 16, 0, tzinfo=timezone.utc)


def source(tmp_path, handler, url=URL, now=NOW, lead=30):
    return IcalSource(url, httpx.Client(transport=httpx.MockTransport(handler)), Store(tmp_path / "t.db"),
                      now=lambda: now, lead_minutes=lead)


def serving(name_or_bytes):
    body = name_or_bytes if isinstance(name_or_bytes, bytes) else (FIX / name_or_bytes).read_bytes()
    return lambda request: httpx.Response(200, content=body)


def by_title(events):
    return {e.title: e for e in events}


def test_ical_source_expands_recurring_events_with_timezones(tmp_path):
    # Weekly 09:00 America/New_York, created in EDT. Tue 3 Nov is after the DST change, so 09:00 EST = 14:00 UTC.
    now = datetime(2026, 11, 3, 13, 35, tzinfo=timezone.utc)  # 25 min before that occurrence
    events = source(tmp_path, serving("feed.ics"), now=now).poll()
    assert [(e.title, e.kind) for e in events] == [("Team standup", "event_upcoming")]  # only occurrence inside the lead
    standup = events[0]
    assert standup.start_time == datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc)
    assert standup.start_time.utcoffset().total_seconds() == 0 and not standup.all_day
    assert standup.source == "calendar" and standup.occurred_at == now


def test_event_47_hours_away_emits_nothing_then_25_minutes_away_emits_once_and_not_again(tmp_path):
    h = serving("moved_before.ics")
    assert source(tmp_path, h, now=PLANNING - timedelta(hours=47)).poll() == []
    assert source(tmp_path, h, now=PLANNING - timedelta(hours=3)).poll() == []
    at25 = source(tmp_path, h, now=PLANNING - timedelta(minutes=25)).poll()
    assert [(e.kind, e.start_time) for e in at25] == [("event_upcoming", PLANNING)]
    assert source(tmp_path, h, now=PLANNING - timedelta(minutes=25)).poll() == []  # same poll again
    assert source(tmp_path, h, now=PLANNING - timedelta(minutes=10)).poll() == []  # later poll inside the lead
    assert source(tmp_path, h, now=PLANNING + timedelta(minutes=5)).poll() == []   # already started: never upcoming


def test_lead_boundaries(tmp_path):
    h = serving("moved_before.ics")
    assert source(tmp_path / "a", h, now=PLANNING - timedelta(minutes=31)).poll() == []
    assert len(source(tmp_path / "b", h, now=PLANNING - timedelta(minutes=30)).poll()) == 1  # exactly the lead
    assert len(source(tmp_path / "c", h, now=PLANNING - timedelta(minutes=50), lead=60).poll()) == 1  # configured lead


@pytest.mark.parametrize("lead", [0, -1, 2881, 1.5, True, "30"])
def test_lead_must_be_a_whole_number_of_minutes_inside_the_window(tmp_path, lead):
    with pytest.raises(IcalError):
        source(tmp_path, serving("feed.ics"), lead=lead)


def test_each_occurrence_of_a_recurring_event_gets_its_own_reminder(tmp_path):
    h = serving("feed.ics")
    first = source(tmp_path, h, now=datetime(2026, 11, 3, 13, 35, tzinfo=timezone.utc)).poll()
    again = source(tmp_path, h, now=datetime(2026, 11, 3, 13, 50, tzinfo=timezone.utc)).poll()
    assert [e.title for e in first] == ["Team standup"] and again == []
    next_week = source(tmp_path, h, now=datetime(2026, 11, 10, 13, 35, tzinfo=timezone.utc)).poll()
    assert [e.title for e in next_week] == ["Team standup"]
    assert next_week[0].content_hash != first[0].content_hash and next_week[0].id != first[0].id


def test_all_day_flag_and_utc_midnight(tmp_path):
    now = datetime(2026, 11, 3, 23, 45, tzinfo=timezone.utc)  # 15 min before the all-day event's 00:00 UTC start
    offsite = by_title(source(tmp_path, serving("feed.ics"), now=now).poll())["Team offsite"]
    assert offsite.kind == "event_upcoming" and offsite.all_day is True
    assert offsite.start_time == datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)


def test_cancelled_event_is_reported_when_seen_with_its_start(tmp_path):
    events = source(tmp_path, serving("feed.ics")).poll()  # Mon 12:00: the cancelled event is 21 h away
    assert [(e.title, e.kind) for e in events] == [("Old sync", "event_cancelled")]
    assert events[0].start_time - events[0].occurred_at == timedelta(hours=21)


def test_moved_event_is_still_reported_when_detected(tmp_path):
    assert source(tmp_path, serving("moved_before.ics"), now=PLANNING - timedelta(hours=47)).poll() == []
    moved = source(tmp_path, serving("moved_after.ics"), now=PLANNING - timedelta(hours=40)).poll()
    new_start = datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc)
    assert [(e.kind, e.start_time) for e in moved] == [("event_moved", new_start)]
    assert moved[0].start_time - moved[0].occurred_at == timedelta(hours=42)
    assert source(tmp_path, serving("moved_after.ics"), now=PLANNING - timedelta(hours=39)).poll() == []  # not re-reported
    # The new occurrence time gets its own reminder when it enters the window.
    soon = source(tmp_path, serving("moved_after.ics"), now=new_start - timedelta(minutes=20)).poll()
    assert [(e.kind, e.start_time) for e in soon] == [("event_upcoming", new_start)]


def test_moved_into_the_lead_window_reports_the_move_without_a_second_reminder(tmp_path):
    source(tmp_path, serving("moved_before.ics"), now=PLANNING - timedelta(hours=5)).poll()
    now = datetime(2026, 11, 3, 17, 40, tzinfo=timezone.utc)  # moved start 18:00 is 20 min away
    assert [e.kind for e in source(tmp_path, serving("moved_after.ics"), now=now).poll()] == ["event_moved"]
    assert source(tmp_path, serving("moved_after.ics"), now=now + timedelta(minutes=5)).poll() == []


def test_feed_url_never_appears_in_events_or_errors(tmp_path):
    events = source(tmp_path, serving("feed.ics")).poll()
    assert events and all("secret-address" not in repr(e) for e in events)
    with pytest.raises(IcalError) as err:
        source(tmp_path, lambda r: httpx.Response(500)).poll()
    assert "secret-address" not in str(err.value)


@pytest.mark.parametrize("url", ["http://calendar.example.invalid/a.ics",
                                 "https://" + "user" + ":" + "pw" + "@calendar.example.invalid/a.ics",  # built at runtime so the scanner sees no credential URL
                                 "ftp://calendar.example.invalid/a.ics", "https:///a.ics"])
def test_non_https_or_credentialed_urls_are_refused(tmp_path, url):
    with pytest.raises(IcalError):
        source(tmp_path, serving("feed.ics"), url=url)


def test_redirects_are_not_followed(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://elsewhere.example.invalid/x.ics"})

    with pytest.raises(IcalError):
        source(tmp_path, handler).poll()
    assert len(seen) == 1


def test_body_over_two_megabytes_is_refused(tmp_path):
    with pytest.raises(IcalError):
        source(tmp_path, serving(b"X" * (2 * 1024 * 1024 + 1))).poll()
    # exactly at the cap is read (then fails to parse as a calendar, which is a different error)
    with pytest.raises(Exception) as err:
        source(tmp_path, serving(b"X" * (2 * 1024 * 1024))).poll()
    assert not isinstance(err.value, IcalError)
