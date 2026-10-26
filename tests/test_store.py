from datetime import datetime, timezone

from headsup.schema import Decision, Event
from headsup.store import Store

T = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def ev(h="h1", id="e1", start=None):
    return Event(id=id, source="github", kind="issue_opened", title="t", body="b", url="https://example.invalid/x",
                 occurred_at=T, fetched_at=T, content_hash=h, start_time=start)


def test_dedupe_by_content_hash_and_survives_reopen(tmp_path):
    path = tmp_path / "d" / "headsup.db"  # parent dir is created
    s = Store(path)
    assert s.add_if_new(ev("h1", "e1")) is True
    assert s.add_if_new(ev("h1", "other-id")) is False  # same hash = same content
    assert s.add_if_new(ev("h2", "e1")) is True
    s.close()
    s2 = Store(path)
    assert s2.add_if_new(ev("h1")) is False and s2.seen_count() == 2


def test_stored_event_round_trips_datetimes(tmp_path):
    s = Store(tmp_path / "t.db")
    s.add_if_new(ev(start=T))
    raw = s._db.execute("SELECT event_json FROM seen_events").fetchone()[0]
    assert "2026-11-02T12:00:00+00:00" in raw


def test_decisions(tmp_path):
    s = Store(tmp_path / "t.db")
    s.record_decision(Decision("e1", "log_only", "below threshold", T, probability=0.2, threshold=0.5))
    s.record_decision(Decision("e2", "interrupt", "above threshold", T))
    assert s.decisions("e1") == [("e1", "log_only", "below threshold", 0.2, 0.5)]
    assert len(s.decisions()) == 2


def test_button_tap_is_idempotent_on_callback_id(tmp_path):
    s = Store(tmp_path / "t.db")
    assert s.record_tap("cb1", "e1", "snooze_1h", T) is True
    assert s.record_tap("cb1", "e1", "snooze_1h", T) is False
    assert s.taps("e1") == [("cb1", "snooze_1h")]


def test_cost_log_sums_per_day_and_purpose(tmp_path):
    s = Store(tmp_path / "t.db")
    s.log_cost("runtime", "claude-haiku-5-5", 100, 20, 0.0002, at=T)
    s.log_cost("eval", "claude-haiku-5-5", 100, 20, 0.0003, at=T)
    s.log_cost("runtime", "claude-haiku-5-5", 10, 2, 0.5, at=datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc))
    assert abs(s.cost_for_day("2026-11-02") - 0.0005) < 1e-12
    assert abs(s.cost_for_day("2026-11-02", "runtime") - 0.0002) < 1e-12


def test_calendar_starts_etags_and_ci_state(tmp_path):
    s = Store(tmp_path / "t.db")
    assert s.get_calendar_start("k") is None and s.get_etag("u") is None and s.get_ci_state("r", "main") is None
    s.set_calendar_start("k", T)
    s.set_etag("u", '"abc"')
    s.set_ci_state("r", "main", "failure")
    assert s.get_calendar_start("k") == T and s.get_etag("u") == '"abc"' and s.get_ci_state("r", "main") == "failure"
    s.set_calendar_start("k", datetime(2026, 11, 3, tzinfo=timezone.utc))
    assert s.get_calendar_start("k") == datetime(2026, 11, 3, tzinfo=timezone.utc)


def test_sql_is_parameterised(tmp_path):
    s = Store(tmp_path / "t.db")
    s.set_etag("u'; DROP TABLE etags;--", '"x"')
    assert s.get_etag("u'; DROP TABLE etags;--") == '"x"'


def test_reminded_marks_are_idempotent_and_persist(tmp_path):
    s = Store(tmp_path / "t.db")
    assert s.was_reminded("k|2026-11-03T16:00:00+00:00") is False
    s.mark_reminded("k|2026-11-03T16:00:00+00:00")
    s.mark_reminded("k|2026-11-03T16:00:00+00:00")
    s.close()
    assert Store(tmp_path / "t.db").was_reminded("k|2026-11-03T16:00:00+00:00") is True
