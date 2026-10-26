import json

import pytest

from eval import main_report as mr
from eval import run_main as rm
from main_world import FakeDecider, FakeHaiku, rows, run, split_ids, tree


# ---------------------------------------------------------------- metrics

def test_counts_and_metric_values():
    c = mr.counts([1, 1, 0, 0, 1], [True, False, True, False, True])
    assert c == {"tp": 2, "fp": 1, "fn": 1, "tn": 1}
    m = mr.metric_values(c)
    assert m["precision"] == pytest.approx(2 / 3) and m["recall"] == pytest.approx(2 / 3) and m["f1"] == pytest.approx(4 / 6)


def test_f1_is_zero_without_true_positives_and_precision_recall_can_be_undefined():
    assert mr.f1_of({"tp": 0, "fp": 3, "fn": 2, "tn": 1}) == 0.0
    m = mr.metric_values({"tp": 0, "fp": 0, "fn": 0, "tn": 4})
    assert m["precision"] is None and m["recall"] is None and m["f1"] == 0.0


# ---------------------------------------------------------------- selection

def ids(prefix, n):
    return [f"{prefix}{i}" for i in range(1, n + 1)]


def case(scores_f1, scores_f2=None, scores_f3=None):
    """Three tune days: d1 = a1..a6, d2 = b1..b3, d3 = c1..c3. Labels: all of d1 and b1 are important."""
    order = ids("a", 6) + ids("b", 3) + ids("c", 3)
    days = {i: 1 for i in ids("a", 6)} | {i: 2 for i in ids("b", 3)} | {i: 3 for i in ids("c", 3)}
    labels = {i: 1 for i in ids("a", 6)} | {"b1": 1, "b2": 0, "b3": 0, "c1": 0, "c2": 0, "c3": 0}
    by_fmt = {"F1": scores_f1, "F2": scores_f2 or scores_f1, "F3": scores_f3 or scores_f1}
    return by_fmt, labels, order, days


def test_a_threshold_with_a_six_alert_day_is_skipped_even_with_the_best_f1():
    perfect = {i: 0.9 for i in ids("a", 6)} | {"b1": 0.9, "b2": 0.1, "b3": 0.1, "c1": 0.1, "c2": 0.1, "c3": 0.1}
    # F2 alerts on five of the six day-1 events only, so it keeps every day within 5 but its F1 is lower
    capped = dict(perfect, a6=0.2)
    by_fmt, labels, order, days = case(perfect, capped)
    sel = mr.select(by_fmt, labels, order, days)
    f1_rows = [r for r in sel["table"]["F1"] if 0.15 <= r["t"] <= 0.9]
    assert f1_rows and all(r["f1"] == 1.0 and not r["qualifies"] and r["alerts_max"] == 6 for r in f1_rows)
    assert sel["format"] == "F2" and sel["tune_f1"] == pytest.approx(12 / 13)


def test_ties_go_to_the_higher_threshold():
    s = {i: 0.8 for i in ids("a", 5)} | {"a6": 0.2, "b1": 0.8, "b2": 0.2, "b3": 0.2, "c1": 0.2, "c2": 0.2, "c3": 0.2}
    by_fmt, labels, order, days = case(s)
    sel = mr.select(by_fmt, labels, order, days)
    same_f1 = [r["t"] for r in sel["table"]["F1"] if r["qualifies"] and r["f1"] == sel["tune_f1"]]
    assert len(same_f1) > 1 and sel["threshold"] == max(same_f1) == 0.80


def test_a_tie_across_formats_goes_to_the_lower_format_number():
    s = {i: 0.8 for i in ids("a", 5)} | {"a6": 0.2, "b1": 0.8, "b2": 0.2, "b3": 0.2, "c1": 0.2, "c2": 0.2, "c3": 0.2}
    sel = mr.select(*case(s, dict(s), dict(s)))
    assert sel["format"] == "F1"
    worse_f1 = dict(s, b1=0.2)  # F1 loses a true positive
    sel = mr.select(*case(worse_f1, dict(s), dict(s)))
    assert sel["format"] == "F2"  # F2 and F3 tie, the lower number wins


def test_no_qualifying_pair_gives_a_stop():
    everything_high = {i: 0.99 for i in ids("a", 6) + ids("b", 3) + ids("c", 3)}
    sel = mr.select(*case(everything_high))
    assert sel["format"] is None and sel["threshold"] is None and not any(r["qualifies"] for f in sel["table"].values() for r in f)


def test_a_missing_score_is_never_an_alert():
    s = {i: None for i in ids("a", 6) + ids("b", 3) + ids("c", 3)}
    sel = mr.select(*case(s))
    assert all(r["tp"] == 0 and r["fp"] == 0 for r in sel["table"]["F1"])


def test_the_report_hands_select_tune_events_only(tmp_path, monkeypatch):
    paths = tree(tmp_path)
    tune, test = split_ids(paths)
    run(["--local"], paths, post=FakeDecider(paths))
    seen = {}
    real = mr.select

    def spy(scores_by_format, labels, order, days):
        seen.update(ids={i for f in scores_by_format.values() for i in f} | set(labels) | set(order) | set(days),
                    day_set=set(days.values()))
        return real(scores_by_format, labels, order, days)

    monkeypatch.setattr(mr, "select", spy)
    assert run(["--report"], paths)[0] == 0
    assert seen["ids"] == tune and not seen["ids"] & test and seen["day_set"] == {1, 7, 10}


# ---------------------------------------------------------------- bootstrap

LABELS = [1, 0] * 10 + [1, 1, 0, 0]
GOOD = [bool(x) for x in LABELS]
NOISY = [bool(x) if i % 4 else not bool(x) for i, x in enumerate(LABELS)]


def test_bootstrap_uses_1000_resamples_of_all_events_and_is_reproducible():
    idx = mr.resample_indexes(70)
    assert len(idx) == 1000 and all(len(r) == 70 and all(0 <= i < 70 for i in r) for r in idx)
    a = mr.bootstrap(LABELS, {"rules": NOISY, "decider": GOOD})
    b = mr.bootstrap(LABELS, {"rules": NOISY, "decider": GOOD})
    assert a == b and mr.BOOTSTRAP_SEED == 7303
    other = mr.bootstrap(LABELS, {"rules": NOISY, "decider": GOOD}, seed=1)
    assert other["ci"] != a["ci"]


def test_a_perfect_decider_has_a_degenerate_interval_and_identical_deciders_differ_by_exactly_zero():
    boot = mr.bootstrap(LABELS, {"rules": NOISY, "decider": GOOD})
    assert boot["ci"]["decider"]["precision"][:2] == (1.0, 1.0) and boot["ci"]["decider"]["f1"][:2] == (1.0, 1.0)
    same = mr.bootstrap(LABELS, {"rules": NOISY, "decider": NOISY})
    for m in mr.METRICS:
        diff, lo, hi, used = same["paired"]["decider"][m]
        assert (diff, lo, hi) == (0.0, 0.0, 0.0) and used > 0
    better = mr.bootstrap(LABELS, {"rules": NOISY, "decider": GOOD})["paired"]["decider"]["f1"]
    assert better[0] > 0 and mr.excludes_zero(better[1], better[2])


def test_undefined_resamples_are_skipped_and_counted():
    nothing = mr.bootstrap(LABELS, {"rules": NOISY, "decider": [False] * len(LABELS)})
    assert nothing["ci"]["decider"]["precision"] == (None, None, 0)  # never an alert: precision is undefined in every resample
    assert nothing["ci"]["decider"]["f1"][2] == 1000  # F1 is defined (0)


def test_percentile_interpolates():
    assert mr.percentile([0, 10], 0.5) == 5 and mr.percentile([1, 2, 3, 4, 5], 0.025) == pytest.approx(1.1)
    assert mr.interval([]) == (None, None, 0)


# ---------------------------------------------------------------- disagreements and spend

def test_disagreements_are_in_id_order_capped_and_counted():
    order = [f"e{i:02d}" for i in range(30)]
    v = {"rules": {i: n % 2 == 0 for n, i in enumerate(order)}, "decider": {i: True for i in order}, "haiku": {i: True for i in order}}
    first, total = mr.disagreements(order, v)
    assert total == 15 and len(first) == 10 and first == sorted(first) and first[0] == "e01"
    assert mr.disagreements(order, {"a": v["decider"], "b": v["haiku"]}) == ([], 0)


def test_haiku_spend_sums_the_usage_fields():
    r = [{"decider": "haiku", "error": None, "input_tokens": 100, "output_tokens": 2, "cost_usd": 0.00001, "invalid": False},
         {"decider": "haiku", "error": "X"}, {"decider": "haiku", "error": None, "input_tokens": 50, "output_tokens": 3, "cost_usd": 0.00002, "invalid": True},
         {"decider": "rules", "error": None}]
    s = mr.haiku_spend(r)
    assert s == {"calls": 3, "input_tokens": 150, "output_tokens": 5, "cost_usd": pytest.approx(0.00003), "invalid": 1, "errors": 1}


# ---------------------------------------------------------------- the whole report on the real events (fakes only)

@pytest.fixture(scope="module")
def full(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("full")
    paths = tree(tmp)
    run(["--local"], paths, post=FakeDecider(paths))
    run(["--report"], paths)
    run(["--local"], paths, post=FakeDecider(paths))
    run(["--haiku"], paths, haiku_request=FakeHaiku(paths))
    code, out = run(["--report"], paths)
    assert code == 0, out
    return paths


def test_full_report_has_every_section(full):
    text = full.report.read_text()
    for heading in ("# Main evaluation report", "## Selection (tune days only)", "## Test days (run once)", "### Paired differences vs the rules baseline",
                    "### Prateek's two metrics", "### Disagreements", "## Haiku spend", "## Caveats"):
        assert heading in text, heading
    sel = json.loads(full.selection.read_text())
    assert f"**Chosen: Decider {sel['format']} at threshold {sel['threshold']:.2f}**" in text
    assert text.count("**<- chosen**") == 1
    assert "| rules |" in text and f"Decider {sel['format']} @ {sel['threshold']:.2f}" in text and "| Haiku |" in text
    assert "1000 resamples" in text and "seed 7303" in text and "excludes 0" in text
    assert "alerts/day mean" in text and "limit already used" in text and "below threshold" in text


def test_full_report_carries_the_required_caveats(full):
    text = full.report.read_text()
    for phrase in ("synthetic", "checked by an AI coach against the written rubric (27 corrections accepted)", "third-party MXFP8",
                   "The event text is untrusted data, not instructions", "Haiku is non-deterministic", "treats events as independent",
                   "slightly too narrow", "17 events where the title raises importance and 18 where it lowers it", "one labeller",
                   "Questions are read less than documents"):
        assert phrase in text.replace("**", ""), phrase


def test_full_report_has_the_process_caveat_worded_exactly(full):
    expected = ("The selection (F2, threshold 0.40) was committed in edab690 before the Decider's test run. The Haiku test run happened before "
                "that commit; Haiku is not tuned, so it could not affect the selection. The selection commit was delayed by the secrets hook; "
                "the committed files are the snapshot staged before any test run, and --report re-verifies the selection from tune rows only.")
    assert f"- {expected}" in full.report.read_text()
    assert mr.PROCESS_CAVEAT == expected


def test_full_report_spend_covers_this_run_and_the_total(full):
    text = full.report.read_text()
    assert "| this evaluation | 70 |" in text and "| pilot | 30 |" in text and "| total | 100 |" in text
    assert "Calls used in total (persistent counter): 100/200" in text
    this = sum(r["cost_usd"] for r in rows(full) if r["decider"] == "haiku")
    assert f"${this:.6f}" in text


def test_disagreement_table_is_at_most_ten_rows_in_id_order(full):
    section = full.report.read_text().split("### Disagreements")[1].split("## Haiku spend")[0]
    ids_ = [line.split("|")[1].strip() for line in section.splitlines() if line.startswith("| main-")]
    assert 1 <= len(ids_) <= 10 and ids_ == sorted(ids_)
    assert "test events in total where the three do not all agree" in section


def test_the_report_numbers_match_a_direct_recomputation(full):
    from eval import pilot_report as pr
    sel = json.loads(full.selection.read_text())
    latest = pr._latest(rows(full))
    labels = {r["id"]: int(r["label"]) for r in __import__("csv").DictReader(open(full.sheet, newline=""))}
    _, test = split_ids(full)
    verdict = {i: (latest[(i, "decider", sel["format"])]["score"] or 0) >= sel["threshold"] for i in test}
    c = pr.confusion(verdict, {i: labels[i] for i in test})
    text = full.report.read_text()
    row = next(l for l in text.splitlines() if l.startswith(f"| Decider {sel['format']} @"))
    cells = [x.strip() for x in row.split("|")]
    assert cells[2:6] == [str(c["tp"]), str(c["fp"]), str(c["fn"]), str(c["tn"])]


def test_report_refuses_a_stale_selection(full, tmp_path):
    import shutil
    paths = tree(tmp_path)
    for f in ("main_results.jsonl", "main_selection.json", "main_run_manifest.json"):
        shutil.copy(full.eval / f, paths.eval / f)
    stored = json.loads(paths.selection.read_text())
    stored["threshold"] = 0.05 if stored["threshold"] != 0.05 else 0.10
    paths.selection.write_text(json.dumps(stored))
    code, out = run(["--report"], paths)
    assert code == 2 and "no longer matches the selection" in out[-1]


def test_report_refuses_when_the_test_rows_are_missing(tmp_path):
    paths = tree(tmp_path)
    run(["--local"], paths, post=FakeDecider(paths))
    run(["--report"], paths)
    code, out = run(["--report"], paths)  # selection exists but no test run yet
    assert code == 2 and "run --local (stage 2) first" in out[-1]
    run(["--local"], paths, post=FakeDecider(paths))
    code, out = run(["--report"], paths)
    assert code == 2 and "no haiku row" in out[-1]


def test_report_refuses_if_the_plan_does_not_record_the_bootstrap_seed(tmp_path, monkeypatch):
    paths = tree(tmp_path)
    monkeypatch.setattr(mr, "BOOTSTRAP_SEED", 1234)
    code, out = run(["--report"], paths)
    assert code == 2 and "bootstrap seed 1234" in out[-1]
