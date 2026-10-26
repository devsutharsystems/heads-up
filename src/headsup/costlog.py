"""Real cost from the API `usage` fields: tokens x listed price, per local day, runtime and eval apart.

Cache token fields are not used (no prompt caching). Whether output_tokens includes thinking tokens
is stated on the thinking page but the Messages schema was not fully read: UNVERIFIED field detail.
Failed calls carry no usage and log nothing.
"""

from datetime import datetime, timezone

from headsup.haiku import PRICE_IN, PRICE_OUT
from headsup.localtime import day_bounds

PURPOSES = ("runtime", "eval")


class CostLog:
    def __init__(self, store, now=None):
        self.store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    def record(self, purpose, model, usage):
        if purpose not in PURPOSES:
            raise ValueError(f"purpose must be one of {PURPOSES}")
        cost = usage.input_tokens * PRICE_IN + usage.output_tokens * PRICE_OUT
        self.store.log_cost(purpose, model, usage.input_tokens, usage.output_tokens, cost, at=self._now())
        return cost

    def day_totals(self, when=None):
        """{'runtime': usd, 'eval': usd} for the local day containing `when` (default now)."""
        start, end = day_bounds(when or self._now())
        totals = {p: 0.0 for p in PURPOSES}
        for purpose, cost in self.store.costs_between(start, end):
            totals[purpose] = totals.get(purpose, 0.0) + cost
        return totals

    def day_total(self, when=None, purpose=None):
        totals = self.day_totals(when)
        return sum(totals.values()) if purpose is None else totals[purpose]
