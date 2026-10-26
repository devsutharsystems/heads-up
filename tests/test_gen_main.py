import builtins
import csv
import dataclasses
import hashlib
import inspect
import json
import shutil
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from eval import gen_synthetic as g
from eval.main_pools import HANDLES, MODULES, PEOPLE, POOLS, ROOMS
from headsup.schema import Event

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
PLACEHOLDERS = dict(name="x", mod="x", ver="1.2.3", n=3, hash="abcdef0", hh="10:00", room="x")


@pytest.fixture(scope="module")
def events():
    return g.generate_main()


@pytest.fixture(scope="module")
def split(events):
    return g.make_split(events)


def norm(text):
    return " ".join(text.lower().split())


def pilot_rows():
    return [json.loads(line) for line in (EVAL / "pilot_events.jsonl").read_text().splitlines()]


def written(tmp_path, seed=g.MAIN_SEED):
    evs = g.generate_main(seed)
    g.write_main_outputs(tmp_path, evs, g.make_split(evs))
    return {n: (tmp_path / n).read_bytes() for n in ("main_events.jsonl", "main_label_sheet.csv", "main_split.json")}


# ---------------------------------------------------------------- determinism

def test_main_generation_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert written(a) == written(b)


def test_a_different_seed_gives_different_events(tmp_path):
    assert written(tmp_path / "a")["main_events.jsonl"] != written(tmp_path / "b", seed=g.MAIN_SEED + 1)["main_events.jsonl"]


def test_generation_works_for_many_seeds_and_keeps_every_rule(tmp_path):
    for seed in range(7300, 7340):
        evs = g.generate_main(seed)
        assert len(evs) == 100 and len({norm(e.title) for e in evs}) == 100
        for e in evs:
            if e.kind in ("event_moved", "event_cancelled") and not e.all_day:
                gap = e.start_time - e.occurred_at
                assert timedelta(hours=2) <= gap <= timedelta(hours=48) and not timedelta(hours=24) <= gap <= timedelta(hours=28)
        assert len(g.make_split(evs)["tune"]) == 30


def test_seeds_differ_from_the_pilot_and_from_each_other():
    assert len({g.PILOT_SEED, g.MAIN_SEED, g.SPLIT_SEED}) == 3 and (g.MAIN_SEED, g.SPLIT_SEED) == (7301, 7302)


def test_the_pilot_files_are_untouched_by_main(tmp_path):
    for name in ("pilot_events.jsonl", "pilot_label_sheet.csv"):
        shutil.copy(EVAL / name, tmp_path / name)
    before = {n: hashlib.sha256((tmp_path / n).read_bytes()).hexdigest() for n in ("pilot_events.jsonl", "pilot_label_sheet.csv")}
    written(tmp_path)
    assert before == {n: hashlib.sha256((tmp_path / n).read_bytes()).hexdigest() for n in before}


# ---------------------------------------------------------------- shape

def test_exactly_100_events_50_50_over_ten_weekdays_five_plus_five_a_day(events):
    assert len(events) == 100
    assert sum(e.source == "github" for e in events) == 50 and sum(e.source == "calendar" for e in events) == 50
    days = {}
    for e in events:
        days.setdefault(e.occurred_at.date(), []).append(e.source)
    assert len(days) == 10 and all(d.weekday() < 5 for d in days)
    assert min(days) == datetime(2026, 11, 9).date() and max(days) == datetime(2026, 11, 20).date()
    assert all(len(v) == 10 and v.count("github") == 5 and v.count("calendar") == 5 for v in days.values())


def test_ids_are_main_001_to_100_in_time_order(events):
    assert [e.id for e in events] == [f"main-{i:03d}" for i in range(1, 101)]
    assert [e.occurred_at for e in events] == sorted(e.occurred_at for e in events)


def test_quotas_and_templates_are_consistent():
    assert sum(p.count for p in POOLS if p.source == "github") == 50 and sum(p.count for p in POOLS if p.source == "calendar") == 50
    assert len({p.key for p in POOLS}) == len(POOLS)
    for p in POOLS:
        assert len(p.items) == p.count, p.key
        for item in p.items:
            for text in item[:2]:
                assert {f for _, f, _, _ in string.Formatter().parse(text) if f} <= set(PLACEHOLDERS), text
                text.format_map(PLACEHOLDERS)  # every placeholder is one the generator fills


# Direction of a title-sensitive event, measured against the kind-only rules baseline (R1-R4). Kept here, not in the generator.
RAISES = {"g08"}  # the kind-only rules would not interrupt; the title describes a real problem that should
LOWERS = {"g03", "g05", "c02", "c05", "c06", "c10"}  # the kind-only rules would interrupt; the title shows it is trivial, optional, personal or all-day


def test_title_sensitive_events_are_balanced_between_the_two_directions():
    raises = sum(p.count for p in POOLS if p.key in RAISES)
    lowers = sum(p.count for p in POOLS if p.key in LOWERS)
    assert (raises, lowers) == (17, 18)
    assert abs(raises - lowers) <= 2
    assert 33 <= raises + lowers <= 37  # about a third of the 100 events


def test_the_two_directions_really_do_what_they_say_against_the_rules_baseline(monkeypatch):
    from eval.baseline_rules import decide
    pool_of = {}
    real = g._main_event

    def recording(rng, pool, item, day0, n, used):
        event = real(rng, pool, item, day0, n, used)
        pool_of[id(event)] = pool.key  # the generator output itself carries no category
        return event

    monkeypatch.setattr(g, "_main_event", recording)
    evs = g.generate_main()
    raises = [bool(decide(e)) for e in evs if pool_of[id(e)] in RAISES]
    lowers = [bool(decide(e)) for e in evs if pool_of[id(e)] in LOWERS]
    assert len(raises) == 17 and len(lowers) == 18
    assert not any(raises)  # the kind-only rules stay silent on every "raises" event
    assert all(lowers)  # and interrupt on every "lowers" event


def test_events_are_valid_event_records(events, tmp_path):
    names = {f.name for f in dataclasses.fields(Event)}
    written(tmp_path)
    rows = [json.loads(l) for l in (tmp_path / "main_events.jsonl").read_text().splitlines()]
    assert len(rows) == 100
    for row in rows:
        assert set(row) == names
        for k in ("occurred_at", "fetched_at", "start_time"):
            if row[k] is not None:
                row[k] = datetime.fromisoformat(row[k])
        Event(**row)
    assert all(e.occurred_at.tzinfo is not None and e.fetched_at > e.occurred_at for e in events)


def test_time_rules(events):
    for e in events:
        minutes = e.occurred_at.hour * 60 + e.occurred_at.minute
        assert g.DAY_START_MIN <= minutes <= g.DAY_END_MIN  # a UTC day and an Asia/Kolkata day group the same way
        if e.source == "github":
            assert e.start_time is None and not e.all_day
            continue
        assert e.start_time is not None and e.start_time.tzinfo is not None
        gap = e.start_time - e.occurred_at
        if e.all_day:
            assert e.kind in ("event_upcoming", "event_moved") and (e.start_time.hour, e.start_time.minute) == (0, 0)
            if e.kind == "event_moved":
                assert gap <= timedelta(hours=24)  # an all-day item moved within 24 hours
        elif e.kind == "event_upcoming":
            assert timedelta(minutes=6) <= gap <= timedelta(minutes=29, seconds=59)  # inside the 30-minute reminder window
        else:
            assert gap <= timedelta(hours=48)
            assert not timedelta(hours=24) <= gap <= timedelta(hours=28)  # away from the rubric's 24-hour boundary
    assert sum(e.all_day for e in events) == 10 and sum(e.all_day and e.kind == "event_moved" for e in events) == 2


def test_ci_events_carry_a_branch(events):
    ci = [e for e in events if e.kind in ("ci_failed", "ci_recovered")]
    assert len(ci) == 13 and all(e.branch and e.is_default_branch == (e.branch == "main") for e in ci)
    assert all(e.branch is None for e in events if e not in ci)


# ---------------------------------------------------------------- no overlap with the pilot

def test_no_event_title_or_id_repeats_a_pilot_event(events):
    pilot = pilot_rows()
    assert len(pilot) == 30
    pilot_titles = {norm(r["title"]) for r in pilot}
    pilot_triples = {(r["source"], r["kind"], norm(r["title"])) for r in pilot}
    pilot_bodies = {norm(r["body"]) for r in pilot}
    pilot_hashes = {r["content_hash"] for r in pilot}
    pilot_urls = {r["url"] for r in pilot}
    assert not {e.id for e in events} & {r["id"] for r in pilot}
    for e in events:
        assert norm(e.title) not in pilot_titles
        assert (e.source, e.kind, norm(e.title)) not in pilot_triples
        assert norm(e.body) not in pilot_bodies and e.content_hash not in pilot_hashes and e.url not in pilot_urls


def test_titles_are_unique_inside_the_main_set(events):
    titles = [norm(e.title) for e in events]
    assert len(set(titles)) == 100
    assert len({e.content_hash for e in events}) == 100 and len({e.url for e in events}) == 100


def test_the_pool_vocabulary_does_not_reuse_pilot_people_or_repo():
    pilot_text = (EVAL / "pilot_events.jsonl").read_text()
    for word in HANDLES + PEOPLE:
        assert word not in pilot_text
    assert g.MAIN_REPO != g.REPO and g.MAIN_REPO not in pilot_text


# ---------------------------------------------------------------- split

def test_split_sizes_stratification_and_whole_days(events, split):
    assert split["counts"] == {"tune": 30, "test": 70, "tune_by_source": {"calendar": 15, "github": 15},
                               "test_by_source": {"calendar": 35, "github": 35}}
    assert len(split["tune_days"]) == 3 and len(split["test_days"]) == 7
    assert set(split["tune_days"]) | set(split["test_days"]) == set(range(1, 11)) and not set(split["tune_days"]) & set(split["test_days"])
    tune, test = set(split["tune"]), set(split["test"])
    assert not tune & test and tune | test == {e.id for e in events}
    ordinal = g.day_ordinals(events)
    for e in events:  # every event of a tune day is tune: the 5-per-day cap is checked on whole days
        assert (e.id in tune) == (ordinal[e.occurred_at.date()] in split["tune_days"])
    assert split["days"]["1"] == "2026-11-09" and split["days"]["10"] == "2026-11-20"


def test_split_is_deterministic_and_changes_with_the_seed(events, split):
    assert g.make_split(events) == split and split["seed"] == g.SPLIT_SEED
    others = {tuple(g.make_split(events, seed=s)["tune_days"]) for s in range(7000, 7040)}
    assert len(others) > 5


def test_split_uses_only_events_and_a_seed():
    params = inspect.signature(g.make_split).parameters
    assert list(params) == ["events", "seed", "n_tune_days"]


# ---------------------------------------------------------------- no labels anywhere

def test_no_label_anywhere(events, split, tmp_path):
    assert not [f.name for f in dataclasses.fields(Event) if "label" in f.name.lower()]
    files = written(tmp_path)
    for line in files["main_events.jsonl"].decode().splitlines():
        assert not [k for k in json.loads(line) if "label" in k.lower()]

    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from keys(v)

    assert not [k for k in keys(json.loads(files["main_split.json"])) if "label" in k.lower()]
    rows = list(csv.reader(files["main_label_sheet.csv"].decode().splitlines()))
    pilot_header = next(csv.reader((EVAL / "pilot_label_sheet.csv").read_text().splitlines()))
    assert rows[0] == pilot_header == g.SHEET_HEADER
    assert len(rows) == 101 and all(r[-1] == "" for r in rows[1:])
    assert not any(w in files["main_split.json"].decode().lower() for w in ("important", "interrupt"))


def test_generator_and_split_never_open_a_file(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("generation must not read any file (no label sheet)")

    monkeypatch.setattr(builtins, "open", refuse)
    evs = g.generate_main()
    g.make_split(evs)
    src = inspect.getsource(g.make_split) + inspect.getsource(g.generate_main)
    assert "open(" not in src and "csv" not in src and "label_sheet" not in src and "read_text" not in src


# ---------------------------------------------------------------- write protection and the committed files

def test_main_outputs_are_not_overwritten_without_force(tmp_path):
    written(tmp_path)
    (tmp_path / "main_label_sheet.csv").write_text("hand-entered work")
    evs = g.generate_main()
    with pytest.raises(FileExistsError):
        g.write_main_outputs(tmp_path, evs, g.make_split(evs))
    assert (tmp_path / "main_label_sheet.csv").read_text() == "hand-entered work"
    g.write_main_outputs(tmp_path, evs, g.make_split(evs), force=True)


def test_committed_main_files_match_the_generator(tmp_path):
    fresh = written(tmp_path)
    assert (EVAL / "main_events.jsonl").read_bytes() == fresh["main_events.jsonl"]
    assert (EVAL / "main_split.json").read_bytes() == fresh["main_split.json"]
    committed = list(csv.reader((EVAL / "main_label_sheet.csv").read_text().splitlines()))
    expected = list(csv.reader(fresh["main_label_sheet.csv"].decode().splitlines()))
    assert [r[:-1] for r in committed] == [r[:-1] for r in expected]  # everything but the label column (filled by hand later)


# ---------------------------------------------------------------- the pre-registration document

PLAN = EVAL / "MAIN_EVAL_PLAN.md"


def test_plan_states_seeds_selection_rule_and_one_test_run():
    text = PLAN.read_text()
    for needle in ("7301", "7302", "7303", "tune", "highest", "F1", "higher threshold", "more than 5", "once",
                   "1000 resamples", "bootstrap", "claude-haiku-5-5", "no label"):
        assert needle in text, needle


def test_plan_records_the_hashes_of_the_frozen_and_generated_files():
    text = PLAN.read_text()
    for rel in ("eval/baseline_rules.py", "eval/rubric.md", "eval/prompts/prompts.sha256", "eval/main_events.jsonl",
                "eval/main_split.json", "eval/main_pools.py"):
        digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        assert digest in text, f"{rel}: its sha256 is not in the plan (the file changed after registration?)"
