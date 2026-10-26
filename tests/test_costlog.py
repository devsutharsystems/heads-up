from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from headsup.costlog import CostLog
from headsup.haiku import HAIKU_MODEL, PRICE_IN, PRICE_OUT
from headsup.store import Store

NOON_IST = datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc)


def usage(i, o):
    return SimpleNamespace(input_tokens=i, output_tokens=o)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def test_costlog_records_usage_and_sums_per_day(tmp_path):
    clock = Clock(NOON_IST)
    log = CostLog(Store(tmp_path / "t.db"), now=clock)
    c1 = log.record("runtime", HAIKU_MODEL, usage(1000, 200))
    assert c1 == pytest.approx(1000 * 0.10 / 1e6 + 200 * 0.50 / 1e6) == pytest.approx(0.0002)  # PLAN.md prices
    log.record("runtime", HAIKU_MODEL, usage(500, 100))
    log.record("eval", HAIKU_MODEL, usage(2000, 10))
    totals = log.day_totals()
    assert totals["runtime"] == pytest.approx(0.0002 + 500 * PRICE_IN + 100 * PRICE_OUT)
    assert totals["eval"] == pytest.approx(2000 * PRICE_IN + 10 * PRICE_OUT)  # kept separate from runtime
    assert log.day_total(purpose="eval") == pytest.approx(totals["eval"])
    assert log.day_total() == pytest.approx(totals["runtime"] + totals["eval"])
    # the next local day starts at zero, and the totals survive a reopen of the database
    clock.now = datetime(2026, 11, 3, 6, 30, tzinfo=timezone.utc)
    assert log.day_totals() == {"runtime": 0.0, "eval": 0.0}
    assert CostLog(Store(tmp_path / "t.db")).day_totals(NOON_IST)["runtime"] == pytest.approx(totals["runtime"])


def test_per_day_totals_use_the_local_day_not_utc(tmp_path):
    clock = Clock(datetime(2026, 11, 2, 18, 29, tzinfo=timezone.utc))  # 23:59 local on 2 Nov
    log = CostLog(Store(tmp_path / "t.db"), now=clock)
    log.record("runtime", HAIKU_MODEL, usage(1000, 0))
    clock.now = datetime(2026, 11, 2, 18, 31, tzinfo=timezone.utc)  # 00:01 local on 3 Nov
    log.record("runtime", HAIKU_MODEL, usage(1000, 0))
    assert log.day_total(datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)) == pytest.approx(1000 * PRICE_IN)
    assert log.day_total(datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)) == pytest.approx(1000 * PRICE_IN)


def test_unknown_purpose_is_refused(tmp_path):
    with pytest.raises(ValueError):
        CostLog(Store(tmp_path / "t.db")).record("other", HAIKU_MODEL, usage(1, 1))
