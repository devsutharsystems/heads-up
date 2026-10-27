import csv
import json

import pytest

from eval import score_my_labels as sm

ROWS = [
    {"decider": "rules", "error": None, "format": None, "id": "a", "score": None, "verdict": True},
    {"decider": "decider", "error": None, "format": "F2", "id": "a", "score": 0.40, "verdict": None},
    {"decider": "haiku", "error": None, "format": None, "id": "a", "score": None, "verdict": False},
    {"decider": "rules", "error": None, "format": None, "id": "b", "score": None, "verdict": False},
    {"decider": "decider", "error": None, "format": "F2", "id": "b", "score": 0.39, "verdict": None},
    {"decider": "haiku", "error": None, "format": None, "id": "b", "score": None, "verdict": True},
]


def test_learner_sheet_is_the_pilot_sheet_with_an_empty_label_column():
    with open(sm.DEV_SHEET_PATH, newline="", encoding="utf-8") as f:
        dev = list(csv.DictReader(f))
    with open(sm.EVAL / "learner_label_sheet.csv", newline="", encoding="utf-8") as f:
        mine = list(csv.DictReader(f))
    assert len(mine) == 30 and list(mine[0]) == list(dev[0])
    assert all(m["label"] == "" for m in mine)
    assert [{k: v for k, v in m.items() if k != "label"} for m in mine] == [{k: v for k, v in d.items() if k != "label"} for d in dev]


def test_blank_or_bad_label_is_refused(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("id,label\na,\n")
    with pytest.raises(ValueError, match="a: label must be 0 or 1"):
        sm.load_labels(p)


def test_threshold_is_inclusive_and_errors_never_alert(tmp_path):
    results = {(r["id"], r["decider"], r["format"]): r for r in ROWS}
    assert sm.verdicts(results, ["a", "b"], "decider", "F2") == {"a": True, "b": False}
    results[("a", "decider", "F2")] = dict(ROWS[1], error="timeout", score=0.9)
    assert sm.verdicts(results, ["a", "zz"], "decider", "F2") == {"a": False, "zz": False}


def test_metrics_and_differences():
    c = sm.confusion({"a": True, "b": True, "c": False, "d": False}, {"a": 1, "b": 0, "c": 1, "d": 0})
    assert c == {"tp": 1, "fp": 1, "fn": 1, "tn": 1} and sm.metrics(c) == (0.5, 0.5, 0.5)
    assert sm.metrics({"tp": 0, "fp": 0, "fn": 0, "tn": 3}) == (None, None, 0.0)
    assert sm.differences({"a": 1, "b": 0}, {"a": 1, "b": 1}) == 1


def test_main_prints_a_table_from_saved_results_without_network(tmp_path, monkeypatch, capsys):
    results = tmp_path / "r.jsonl"
    results.write_text("\n".join(json.dumps(r) for r in ROWS))
    dev = tmp_path / "dev.csv"
    dev.write_text("id,label\na,1\nb,0\n")
    mine = tmp_path / "mine.csv"
    mine.write_text("id,label\na,1\nb,1\n")
    monkeypatch.setattr(sm, "RESULTS_PATH", results)
    monkeypatch.setattr(sm, "DEV_SHEET_PATH", dev)
    monkeypatch.setattr("socket.socket", lambda *a, **k: pytest.fail("network used"))
    assert sm.main([str(mine)]) == 0
    out = capsys.readouterr().out
    assert "differ from Dev's on 1 of 2" in out
    assert "| rules | 1 | 0 | 1 | 1.000 | 0.500 | 0.667 |" in out
    assert "| Haiku | 1 | 0 | 1 | 1.000 | 0.500 | 0.667 |" in out


def test_main_needs_one_argument_and_reports_a_bad_sheet(tmp_path, capsys):
    assert sm.main([]) == 2
    bad = tmp_path / "b.csv"
    bad.write_text("id,label\na,\n")
    assert sm.main([str(bad)]) == 2 and "label must be 0 or 1" in capsys.readouterr().err


def test_real_results_run_on_dev_labels_matches_zero_differences(capsys):
    assert sm.main([str(sm.DEV_SHEET_PATH)]) == 0
    out = capsys.readouterr().out
    assert "differ from Dev's on 0 of 30" in out and "| Decider F2 |" in out
