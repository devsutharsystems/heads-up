from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from eval.baseline_rules import decide

NOW = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)


@dataclass
class Ev:
    kind: str
    branch: str | None = None
    occurred_at: datetime = NOW
    start_time: datetime | None = None
    all_day: bool = False


def in_(**delta):
    return NOW + timedelta(**delta)


# R1
def test_r1_ci_failed_on_main_interrupts():
    assert decide(Ev("ci_failed", branch="main")) is True


def test_r1_ci_failed_on_feature_branch_does_not():
    assert decide(Ev("ci_failed", branch="feature/x")) is False


# R2
def test_r2_release_published_interrupts():
    assert decide(Ev("release_published")) is True


# R3
def test_r3_event_in_10_minutes_interrupts():
    assert decide(Ev("event_upcoming", start_time=in_(minutes=10))) is True


def test_r3_boundary_30_minutes_inclusive():
    assert decide(Ev("event_upcoming", start_time=in_(minutes=30))) is True


def test_r3_31_minutes_does_not():
    assert decide(Ev("event_upcoming", start_time=in_(minutes=31))) is False


def test_r3_all_day_does_not():
    assert decide(Ev("event_upcoming", start_time=in_(minutes=10), all_day=True)) is False


def test_r3_already_started_does_not():
    assert decide(Ev("event_upcoming", start_time=in_(minutes=-5))) is False


def test_r3_missing_start_time_does_not():
    assert decide(Ev("event_upcoming", start_time=None)) is False


# R4
def test_r4_cancelled_within_24h_interrupts():
    assert decide(Ev("event_cancelled", start_time=in_(hours=5))) is True


def test_r4_moved_within_24h_interrupts():
    assert decide(Ev("event_moved", start_time=in_(hours=23))) is True


def test_r4_boundary_24h_inclusive():
    assert decide(Ev("event_moved", start_time=in_(hours=24))) is True


def test_r4_25h_does_not():
    assert decide(Ev("event_cancelled", start_time=in_(hours=25))) is False


def test_r4_already_started_does_not():
    assert decide(Ev("event_cancelled", start_time=in_(hours=-1))) is False


# Else
def test_else_ci_recovered_issue_pr_do_not():
    for kind in ("ci_recovered", "issue_opened", "pr_opened"):
        assert decide(Ev(kind, branch="main")) is False


def test_else_unknown_kind_does_not():
    assert decide(Ev("something_new")) is False
