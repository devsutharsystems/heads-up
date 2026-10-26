from datetime import datetime, timedelta, timezone

from headsup.policy import DAILY_LIMIT, Policy, passes_threshold
from headsup.schema import Event
from headsup.store import Store

NOON = datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc)  # 12:00 in Asia/Kolkata (UTC+5:30)


def ev(i=1):
    return Event(id=f"e{i}", source="github", kind="issue_opened", title="t", body="b", url="https://example.invalid/x",
                 occurred_at=NOON, fetched_at=NOON, content_hash=f"h{i}")


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def make(tmp_path, now=NOON, threshold=0.35):
    clock = Clock(now)
    return Policy(Store(tmp_path / "t.db"), threshold, now=clock), clock


def test_policy_interrupts_above_threshold_else_logs_only(tmp_path):
    p, _ = make(tmp_path)
    above = p.decide(ev(1), 0.50)
    equal = p.decide(ev(2), 0.35)
    below = p.decide(ev(3), 0.20)
    none = p.decide(ev(4), None)
    assert [d.verdict for d in (above, equal, below, none)] == ["interrupt", "interrupt", "log_only", "log_only"]
    assert "0.50 >= threshold 0.35" in above.reason and "TEMPORARY" in above.reason
    assert "< threshold" in below.reason and "unavailable" in none.reason
    assert (above.probability, above.threshold, none.probability) == (0.50, 0.35, None)
    # every event got a stored decision, in order
    assert [r[:2] for r in p.store.decisions()] == [("e1", "interrupt"), ("e2", "interrupt"), ("e3", "log_only"), ("e4", "log_only")]


def test_passes_threshold_is_part_one_on_its_own():
    assert passes_threshold(0.35, 0.35) and passes_threshold(0.9, 0.35)
    assert not passes_threshold(0.349, 0.35) and not passes_threshold(None, 0.0)


def test_policy_stops_interrupting_after_five_per_day(tmp_path):
    p, clock = make(tmp_path)
    assert DAILY_LIMIT == 5
    verdicts = [p.decide(ev(i), 0.9).verdict for i in range(1, 8)]
    assert verdicts == ["interrupt"] * 5 + ["log_only"] * 2
    sixth = p.store.decisions("e6")[0]
    assert "daily limit of 5" in sixth[2]
    # log-only decisions do not use up the limit, and the next local day starts fresh
    clock.now = NOON + timedelta(days=1)
    assert p.decide(ev(20), 0.9).verdict == "interrupt"


def test_daily_limit_resets_at_local_midnight_not_utc_midnight(tmp_path):
    # Asia/Kolkata midnight is 18:30 UTC.
    before = datetime(2026, 11, 2, 18, 29, tzinfo=timezone.utc)
    p, clock = make(tmp_path, now=before)
    assert [p.decide(ev(i), 0.9).verdict for i in range(1, 7)] == ["interrupt"] * 5 + ["log_only"]
    clock.now = datetime(2026, 11, 2, 18, 31, tzinfo=timezone.utc)  # already the next local day
    assert p.decide(ev(30), 0.9).verdict == "interrupt"
    # and a UTC-midnight crossing does NOT reset: 23:30 UTC is 05:00 local, same local day as 18:31 UTC
    clock.now = datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)
    assert p.interrupts_today() == 1


def test_below_threshold_events_do_not_count_toward_the_limit(tmp_path):
    p, _ = make(tmp_path)
    for i in range(10):
        p.decide(ev(i), 0.1)
    assert p.interrupts_today() == 0
    assert p.decide(ev(99), 0.9).verdict == "interrupt"
