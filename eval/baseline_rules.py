"""Frozen rules baseline (PLAN.md section 9).

FROZEN: do not edit after the pilot. A change = a new version, reported
alongside the old one.

decide(event) -> True means "interrupt". Pure: no I/O, no clock, no network.
"Now" is event.occurred_at (the simulated time in the evaluation).

Rules, exactly these and nothing else:
  R1 GitHub: CI failed on the default branch (main).
  R2 GitHub: a new release was published.
  R3 Calendar: an event starts within the next 30 minutes and is not all-day.
  R4 Calendar: an event was cancelled or moved, and starts within the next 24 hours.
  Else: do not interrupt (CI recovered, new issues, new PRs, anything unknown).

Event attributes read: kind, branch, occurred_at, start_time, all_day.
Time windows are inclusive; an event that already started does not match.
"""

from datetime import timedelta

_SOON = timedelta(minutes=30)
_DAY = timedelta(hours=24)


def _starts_within(event, window):
    if event.start_time is None:
        return False
    return timedelta(0) <= event.start_time - event.occurred_at <= window


def decide(event) -> bool:
    if event.kind == "ci_failed" and event.branch == "main":  # R1
        return True
    if event.kind == "release_published":  # R2
        return True
    if event.kind == "event_upcoming" and not event.all_day and _starts_within(event, _SOON):  # R3
        return True
    if event.kind in ("event_cancelled", "event_moved") and _starts_within(event, _DAY):  # R4
        return True
    return False
