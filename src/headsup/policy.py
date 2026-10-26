"""Interrupt policy: LEARNER PART 1 (threshold) and LEARNER PART 2 (daily limit).

Part 1 turns the Decider's probability into a candidate verdict; part 2 caps interrupts per local
day. Every event gets a stored Decision with the reason, so the decision log is complete.
"""

from datetime import datetime, timezone

from headsup.localtime import LOCAL_TZ, day_bounds
from headsup.schema import Decision

DAILY_LIMIT = 5  # Dev's decision (PLAN.md): interrupts per local day (Asia/Kolkata)


def passes_threshold(probability, threshold):
    """LEARNER PART 1: the interrupt decision.

    True only when the Decider answered and probability >= threshold. No answer (None) is never
    an interrupt: the Decider being down means log only, not a blind alert.
    """
    return probability is not None and probability >= threshold


class Policy:
    """Applies part 1, then part 2, and records the resulting Decision in the store."""

    def __init__(self, store, threshold, now=None, limit=DAILY_LIMIT, note=None):
        self.store = store
        self.threshold = threshold
        self.limit = limit
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._note = f"; {note}" if note else ""  # where the threshold came from, shown in the decision reason

    def interrupts_today(self, now=None):
        """LEARNER PART 2 helper: interrupts already stored for the local day, counted from the store."""
        start, end = day_bounds(now or self._now())
        return self.store.interrupts_between(start, end)

    def decide(self, event, probability):
        now = self._now()
        if probability is None:
            verdict, reason = "log_only", "decider unavailable: log only"
        elif not passes_threshold(probability, self.threshold):
            verdict, reason = "log_only", f"probability {probability:.2f} < threshold {self.threshold:.2f}{self._note}"
        elif (snooze := self.store.snooze_until()) is not None and now < snooze:  # snooze_1h button (learner part 3)
            verdict, reason = "log_only", f"snoozed until {snooze.astimezone(LOCAL_TZ):%H:%M} local"
        elif self.interrupts_today(now) >= self.limit:  # LEARNER PART 2: the daily limit
            verdict, reason = "log_only", f"daily limit of {self.limit} interrupts reached"
        else:
            verdict, reason = "interrupt", f"probability {probability:.2f} >= threshold {self.threshold:.2f}{self._note}"
        decision = Decision(event_id=event.id, verdict=verdict, reason=reason, decided_at=now,
                            probability=probability, threshold=self.threshold)
        self.store.record_decision(decision)
        return decision
