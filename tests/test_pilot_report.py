import csv
from datetime import datetime, timedelta, timezone

import pytest

from eval import pilot_report as pr
from eval.gen_synthetic import generate

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def make(spec):
    """spec: list of (day, label). Returns ids in time order, labels, days."""
    ids, labels, days = [], {}, {}
    for n, (day, label) in enumerate(spec):
        i = f"e{n}"
        ids.append(i)
        labels[i] = label
        days[i] = day
    return ids, labels, days


def test_confusion_precision_recall():
    labels = {"a": 1, "b": 1, "c": 0, "d": 0}
    c = pr.confusion({"a": True, "b": False, "c": True, "d": False}, labels)
    assert c == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}
    assert pr.precision(c) == 0.5 and pr.recall(c) == 0.5


def test_zero_denominators_are_none():
    c = pr.confusion({"a": False}, {"a": 0})
    assert pr.precision(c) is None and pr.recall(c) is None


def test_threshold_grid():
    assert pr.THRESHOLDS[0] == 0.05 and pr.THRESHOLDS[-1] == 0.95 and len(pr.THRESHOLDS) == 19


def test_sweep_alerts_per_day_and_recall():
    ids, labels, days = make([(1, 1), (1, 0), (2, 1), (2, 0)])
    scores = {"e0": 0.9, "e1": 0.5, "e2": 0.3, "e3": 0.1}
    rows = {r["t"]: r for r in pr.sweep(scores, labels, ids, days)}
    assert rows[0.05]["alerts_mean"] == 2 and rows[0.05]["recall"] == 1.0
    assert rows[0.5]["alerts_mean"] == 1 and rows[0.5]["alerts_max"] == 2 and rows[0.5]["recall"] == 0.5
    assert rows[0.55]["alerts_mean"] == 0.5 and rows[0.55]["alerts_max"] == 1
    assert rows[0.95]["recall"] == 0.0 and rows[0.95]["precision"] is None


def test_missing_score_is_never_an_alert():
    ids, labels, days = make([(1, 1)])
    rows = pr.sweep({"e0": None}, labels, ids, days)
    assert all(r["tp"] == 0 and r["fn"] == 1 for r in rows)


def test_limit_of_five_per_day_in_time_order():
    spec = [(1, 0)] * 5 + [(1, 1)] + [(2, 1)]  # day 1: five wrong alerts first, then an important one
    ids, labels, days = make(spec)
    m = pr.limited_stream({i: True for i in ids}, labels, ids, days)
    assert m["sent"] == 6 and m["wanted"] == 1          # day 1 sends 5, day 2 sends 1
    assert m["missed_limit"] == 1 and m["missed_below"] == 0 and m["missed_total"] == 1
    assert m["wanted_share"] == pytest.approx(1 / 6)


def test_missed_below_threshold_and_empty_share():
    ids, labels, days = make([(1, 1), (1, 0)])
    m = pr.limited_stream({"e0": False, "e1": False}, labels, ids, days)
    assert m["sent"] == 0 and m["wanted_share"] is None and m["missed_below"] == 1 and m["missed_limit"] == 0


def sweeps_with(rows_by_fmt):
    return {f: [dict(t=t, qualifies=q, recall=r) for t, q, r in rows] for f, rows in rows_by_fmt.items()}


def test_go_when_a_qualifying_threshold_reaches_half():
    g = pr.go_no_go(sweeps_with({"F1": [(0.3, True, 0.5)], "F2": [(0.3, True, 0.2)], "F3": []}))
    assert g["verdict"] == "GO" and g["best"] == ("F1", 0.3, 0.5)


def test_stop_when_no_qualifying_threshold_reaches_half():
    g = pr.go_no_go(sweeps_with({"F1": [(0.3, True, 0.4), (0.1, False, 0.9)], "F2": [(0.3, True, 0.0)], "F3": [(0.5, True, None)]}))
    assert g["verdict"] == "STOP" and g["best"] is None


def test_threshold_with_a_six_alert_day_does_not_qualify():
    ids, labels, days = make([(1, 1)] * 6)
    rows = pr.sweep({i: 0.9 for i in ids}, labels, ids, days)
    assert rows[0]["alerts_max"] == 6 and rows[0]["qualifies"] is False
    assert pr.go_no_go({"F1": rows})["verdict"] == "STOP"


def test_load_labels_requires_zero_or_one(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("id,label\na,1\nb,\n")
    with pytest.raises(ValueError):
        pr.load_labels(p)
    p.write_text("id,label\na,1\nb,0\n")
    assert pr.load_labels(p) == {"a": 1, "b": 0}


def test_render_end_to_end_on_pilot_events():
    events = generate()
    labels = {e.id: (1 if n % 3 == 0 else 0) for n, e in enumerate(events)}
    rows = []
    for e in events:
        rows.append({"id": e.id, "decider": "rules", "format": None, "verdict": labels[e.id] == 1, "error": None})
        rows.append({"id": e.id, "decider": "haiku", "format": None, "verdict": False, "error": None, "invalid": False,
                     "input_tokens": 60, "output_tokens": 2, "cost_usd": 0.000007})
        for fmt in pr.FORMATS:
            rows.append({"id": e.id, "decider": "decider", "format": fmt, "score": 0.9 if labels[e.id] else 0.1,
                         "verdict": None, "error": None})
    text = pr.render(events, labels, rows, "VERBATIM LIMITATION TEXT", True, 30)
    assert "Verdict: GO" in text or "Verdict: STOP" in text
    assert "VERBATIM LIMITATION TEXT" in text and "smoke check" in text and "labeller saw" in text
    assert "third-party" in text and text.count("threshold sweep") == 3
