"""Main evaluation runner (eval/MAIN_EVAL_PLAN.md).

  PYTHONPATH=src .venv/bin/python -m eval.run_main --local [--host 127.0.0.1:11434]
  PYTHONPATH=src .venv/bin/python -m eval.run_main --haiku [--limit N]
  PYTHONPATH=src .venv/bin/python -m eval.run_main --report

Every mode first checks that the files still match the sha256 values in the plan, and refuses if anything changed.
The stage of --local and --report is decided by what is on disk:
  no eval/main_selection.json : --local scores the rules on all 100 events and Decider F1, F2, F3 on the TUNE events;
                                --report picks the pair from tune rows, writes main_selection.json and a tune-only report.
  main_selection.json exists  : --local scores ONLY the chosen format on the TEST events (once);
                                --haiku scores the TEST events; --report writes the full report.
--local and --haiku never read a label value. Only --haiku can make an Anthropic call (run it yourself in a terminal).
"""

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from eval import main_report as mr
from eval import pilot
from eval import pilot_report as pr
from eval.baseline_rules import decide
from headsup.state import build_input

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HOST = "127.0.0.1:11434"
FORMATS = pilot.FORMATS
REQUIRED = ["eval/main_events.jsonl", "eval/main_split.json", "eval/main_label_sheet.csv", "eval/main_pools.py",
            "eval/gen_synthetic.py", "eval/baseline_rules.py", "eval/rubric.md", "eval/prompts/prompts.sha256",
            *[f"eval/prompts/{n}" for n in pilot.PROMPT_FILES]]
_ROW = re.compile(r"^\|\s*`([^`]+)`[^|]*\|\s*`([0-9a-f]{64})`\s*\|\s*$")


class RegistrationError(Exception):
    """A registered file changed, or the plan no longer lists it."""


class StageError(Exception):
    """The requested step is not allowed in the current state (a refusal, not a crash)."""


@dataclass(frozen=True)
class Paths:
    root: Path = ROOT

    def __getattr__(self, name):
        table = {"eval": "eval", "plan": "eval/MAIN_EVAL_PLAN.md", "events": "eval/main_events.jsonl", "split": "eval/main_split.json",
                 "sheet": "eval/main_label_sheet.csv", "results": "eval/main_results.jsonl", "selection": "eval/main_selection.json",
                 "manifest": "eval/main_run_manifest.json", "report": "eval/EVAL_REPORT.md", "counter": "eval/haiku_call_counter.json",
                 "f3_check": "eval/f3_check.json", "prompts_dir": "eval/prompts", "limitations": "eval/modelcard_limitations.txt",
                 "pilot_results": "eval/pilot_results.jsonl"}
        if name in table:
            return self.root / table[name]
        raise AttributeError(name)


# ---------------------------------------------------------------- registration check

def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def blanked_sheet_bytes(path):
    """The label sheet with its label column emptied, written the way the generator wrote it."""
    rows = list(csv.reader(io.StringIO(Path(path).read_text(encoding="utf-8"), newline="")))
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(rows[0])
    for r in rows[1:]:
        w.writerow(r[:-1] + [""])
    return out.getvalue().encode("utf-8")


def verify_registration(paths):
    """Refuse unless every registered file matches the plan. Returns {'files_checked', 'plan_sha256', 'labelled_sheet_sha256'}."""
    registered = {}
    for line in paths.plan.read_text(encoding="utf-8").splitlines():
        m = _ROW.match(line)
        if m:
            registered[m.group(1)] = m.group(2)
    for rel in REQUIRED:
        if rel not in registered:
            raise RegistrationError(f"the plan no longer lists {rel}")
        target = paths.root / rel
        if not target.exists():
            raise RegistrationError(f"{rel} is missing")
        digest = hashlib.sha256(blanked_sheet_bytes(target)).hexdigest() if rel.endswith("main_label_sheet.csv") else _sha(target)
        if digest != registered[rel]:
            raise RegistrationError(f"{rel} does not match the sha256 in the plan; it changed after registration")
    try:
        pilot.load_prompts(paths.prompts_dir)
    except pilot.PromptFrozenError as exc:
        raise RegistrationError(str(exc)) from None
    if json.loads(paths.f3_check.read_text()).get("accepted") is not True:
        raise RegistrationError("eval/f3_check.json does not say the choice question was accepted; the plan asks F3 as a choice question")
    return {"files_checked": len(REQUIRED), "plan_sha256": _sha(paths.plan), "labelled_sheet_sha256": _sha(paths.sheet)}


def guard(paths):
    """Registration check plus the label lock: the first call records the plan and labelled sheet hashes, later calls compare."""
    info = verify_registration(paths)
    if not paths.manifest.exists():
        paths.manifest.write_text(json.dumps({"plan_sha256": info["plan_sha256"], "labelled_sheet_sha256": info["labelled_sheet_sha256"],
                                              "first_run_at": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n")
    else:
        locked = json.loads(paths.manifest.read_text())
        for key, what in (("plan_sha256", "the plan"), ("labelled_sheet_sha256", "the labelled sheet (the labels)")):
            if locked[key] != info[key]:
                raise RegistrationError(f"{what} changed since the first run (eval/main_run_manifest.json)")
    return info


# ---------------------------------------------------------------- data

def load_main(paths):
    events = pilot.load_events(paths.events)
    split = json.loads(paths.split.read_text())
    tune, test = set(split["tune"]), set(split["test"])
    ids = {e.id for e in events}
    if tune | test != ids or tune & test:
        raise RegistrationError("the split does not partition the events")
    ordinal = {date: int(k) for k, date in split["days"].items()}
    day_of = {e.id: ordinal[e.occurred_at.date().isoformat()] for e in events}
    return events, split, day_of


def _has_selection(paths):
    return paths.selection.exists()


# ---------------------------------------------------------------- --local

def ensure_rules_rows(events, results_path):
    have = {r["id"] for r in pilot.read_rows(results_path) if r["decider"] == "rules"}
    for e in events:
        if e.id not in have:
            pilot.append_row(results_path, {"id": e.id, "decider": "rules", "format": None, "score": None,
                                            "verdict": bool(decide(e)), "error": None})


def score_events(events, formats, prompts, post, host, results_path):
    """Ask the Decider for each (event, format) that has no successful row yet. Returns the number of calls made."""
    done = {(r["id"], r["format"]) for r in pilot.read_rows(results_path) if r["decider"] == "decider" and r["error"] is None}
    calls = 0
    for e in events:
        for fmt in formats:
            if (e.id, fmt) in done:
                continue
            q = pilot.decider_question(fmt, prompts, True)
            row = {"id": e.id, "decider": "decider", "format": fmt, "qtype": q["type"], "score": None, "verdict": None,
                   "error": None, "prompt_sha256": pilot._prompt_hash(fmt, q["type"], prompts)}
            start = time.monotonic()
            try:
                row["score"] = pilot.ask_decider(post, host, build_input(e), q)
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                row["error"] = type(exc).__name__
            row["latency_s"] = round(time.monotonic() - start, 3)
            pilot.append_row(results_path, row)
            calls += 1
    return calls


def run_local(paths, post, host, out=print):
    guard(paths)
    pilot.check_local_host(host)
    events, split, _ = load_main(paths)
    prompts = pilot.load_prompts(paths.prompts_dir)
    by_id = {e.id: e for e in events}
    ensure_rules_rows(events, paths.results)
    if not _has_selection(paths):
        tune = [e for e in events if e.id in set(split["tune"])]
        calls = score_events(tune, FORMATS, prompts, post, host, paths.results)
        out(f"stage 1 (tune days): rules on {len(events)} events; Decider F1, F2, F3 on {len(tune)} tune events; {calls} Decider calls. "
            "Next: --report (writes eval/main_selection.json; commit it before the test run).")
        return {"stage": 1, "calls": calls}
    fmt = json.loads(paths.selection.read_text())["format"]
    if fmt not in FORMATS:
        raise StageError("eval/main_selection.json has no chosen format, so there is no test run")
    test = [by_id[i] for i in split["test"]]
    done = {r["id"] for r in pilot.read_rows(paths.results) if r["decider"] == "decider" and r["format"] == fmt and r["error"] is None}
    if all(e.id in done for e in test):
        raise StageError(f"the test run is once: every test event already has a Decider {fmt} score")
    calls = score_events(test, [fmt], prompts, post, host, paths.results)
    out(f"stage 2 (test days): Decider {fmt} only, on {len(test)} test events; {calls} calls. Next: --haiku, then --report.")
    return {"stage": 2, "calls": calls, "format": fmt}


# ---------------------------------------------------------------- --haiku

def run_haiku(paths, request, out=print, limit=None):
    guard(paths)
    if not _has_selection(paths):
        raise StageError("no eval/main_selection.json yet: the selection is recorded (and committed) before the test run")
    if json.loads(paths.selection.read_text())["format"] is None:
        raise StageError("the selection says STOP: no test run")
    events, split, _ = load_main(paths)
    prompts = pilot.load_prompts(paths.prompts_dir)
    test = [e for e in events if e.id in set(split["test"])]
    done = {r["id"] for r in pilot.read_rows(paths.results) if r["decider"] == "haiku" and r["error"] is None}
    needed = sum(1 for e in test if e.id not in done)
    counter = pilot.CallCounter(paths.counter)
    left = pilot.CALL_CAP - counter.total()
    out(f"Haiku on the test events: {needed} calls needed, {left} left under the cap of {pilot.CALL_CAP} ({counter.total()} used).")
    if needed > left:
        raise StageError(f"the cap would be exceeded: {needed} calls needed, {left} left")
    result = pilot.run_haiku(test, prompts, request, counter, results_path=paths.results, limit=limit)
    out(f"haiku calls this run: {result['calls']}; calls used in total: {counter.total()}/{pilot.CALL_CAP}")
    if result["stopped"]:
        out(f"STOPPED: {result['stopped']}")
    return result


# ---------------------------------------------------------------- --report

def _scores(latest, ids, fmt):
    out = {}
    for i in ids:
        row = latest.get((i, "decider", fmt))
        if row is None:
            raise StageError(f"no Decider {fmt} row for {i}: run --local first")
        out[i] = row["score"] if row["error"] is None else None
    return out


def run_report(paths, out=print):
    info = guard(paths)
    plan_text = paths.plan.read_text(encoding="utf-8")
    if str(mr.BOOTSTRAP_SEED) not in plan_text:
        raise StageError(f"the plan does not record the bootstrap seed {mr.BOOTSTRAP_SEED}")
    events, split, day_of = load_main(paths)
    labels = pr.load_labels(paths.sheet)
    latest = pr._latest(pilot.read_rows(paths.results))
    order = [e.id for e in events]  # time order
    tune_ids = [i for i in order if i in set(split["tune"])]
    test_ids = [i for i in order if i in set(split["test"])]
    tune_labels = {i: labels[i] for i in tune_ids}
    tune_days = {i: day_of[i] for i in tune_ids}
    scores = {fmt: _scores(latest, tune_ids, fmt) for fmt in FORMATS}
    sel = mr.select(scores, tune_labels, tune_ids, tune_days)
    tune_errors = {f: sum(1 for i in tune_ids if latest[(i, "decider", f)]["error"]) for f in FORMATS}
    head = mr.render_header({
        "files_checked": info["files_checked"], "events": len(events), "days": len(set(day_of.values())),
        "tune_days": len(split["tune_days"]), "tune_events": len(tune_ids), "tune_positive": sum(tune_labels.values()),
        "test_days": len(split["test_days"]), "test_events": len(test_ids), "test_positive": sum(labels[i] for i in test_ids)})

    if not _has_selection(paths):
        if sel["format"]:  # a STOP outcome writes no selection file, so no test stage can start
            paths.selection.write_text(json.dumps(sel, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        text = head + mr.render_selection(sel, tune_errors) + [
            "## Test days", "", "Not run yet. " + ("Commit `eval/main_selection.json`, then run `--local` again (the chosen format on the test events), "
                                                  "then `--haiku`, then `--report`." if sel["format"] else "No test run (STOP)."), ""]
        paths.report.write_text("\n".join(text), encoding="utf-8")
        out(f"stage 1 report written to {paths.report.name}; " + (f"chosen: {sel['format']} at {sel['threshold']:.2f}" if sel["format"]
                                                                  else "STOP: no pair qualifies"))
        return {"stage": 1, "selection": sel}

    stored = json.loads(paths.selection.read_text())
    if json.loads(json.dumps(sel, sort_keys=True)) != json.loads(json.dumps(stored, sort_keys=True)):
        raise StageError("eval/main_selection.json no longer matches the selection recomputed from the tune rows")
    fmt = stored["format"]
    names = ["rules", "decider", "haiku"]
    verdicts = {n: {} for n in names}
    for i in test_ids:
        rules_row = latest.get((i, "rules", None))
        if rules_row is None:
            raise StageError(f"no rules row for test event {i}: run --local first")
        verdicts["rules"][i] = bool(rules_row["verdict"])
        row = latest.get((i, "decider", fmt))
        if row is None:
            raise StageError(f"no Decider {fmt} row for test event {i}: run --local (stage 2) first")
        score = row["score"] if row["error"] is None else None
        verdicts["decider"][i] = score is not None and score >= stored["threshold"]
        haiku_row = latest.get((i, "haiku", None))
        if haiku_row is None:
            raise StageError(f"no haiku row for test event {i}: run --haiku first")
        verdicts["haiku"][i] = bool(haiku_row["verdict"]) if haiku_row["error"] is None else False
    labels_test = {i: labels[i] for i in test_ids}
    days_test = {i: day_of[i] for i in test_ids}
    lab_list = [labels[i] for i in test_ids]
    ver_list = {n: [verdicts[n][i] for i in test_ids] for n in names}
    boot = mr.bootstrap(lab_list, ver_list)
    cm = {n: mr.counts(lab_list, ver_list[n]) for n in names}
    per_day = {n: pr.alerts_per_day(verdicts[n], days_test) for n in names}
    limit = {n: pr.limited_stream(verdicts[n], labels_test, test_ids, days_test) for n in names}
    examples, total = mr.disagreements(test_ids, verdicts)
    titles = {e.id: e.title.replace("|", "/") for e in events}
    display = {"rules": "rules", "decider": f"Decider {fmt} @ {stored['threshold']:.2f}", "haiku": "Haiku"}
    test_part = mr.render_test(stored, cm, boot, limit, per_day, examples, total, titles, labels_test, verdicts, names, display)
    main_rows = pilot.read_rows(paths.results)
    pilot_rows = pilot.read_rows(paths.pilot_results) if paths.pilot_results.exists() else []
    spend = mr.render_spend(mr.haiku_spend(main_rows), mr.haiku_spend(pilot_rows), pilot.CallCounter(paths.counter).total(), pilot.CALL_CAP)
    errors = [f"Decider {fmt} test calls that failed: {sum(1 for i in test_ids if latest[(i, 'decider', fmt)]['error'])}; "
              f"Haiku calls that failed: {sum(1 for i in test_ids if latest[(i, 'haiku', None)]['error'])} (counted as no alert).", ""]
    limitations = paths.limitations.read_text(encoding="utf-8") if paths.limitations.exists() else "(modelcard_limitations.txt not found)"
    text = head + mr.render_selection(sel, tune_errors) + test_part + errors + spend + mr.render_caveats(limitations)
    paths.report.write_text("\n".join(text), encoding="utf-8")
    out(f"full report written to {paths.report.name}")
    return {"stage": 2, "selection": stored, "boot": boot}


# ---------------------------------------------------------------- CLI

def main(argv=None, *, paths=None, post=None, haiku_request=None, out=print):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--local", action="store_true")
    mode.add_argument("--haiku", action="store_true")
    mode.add_argument("--report", action="store_true")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--limit", type=int)
    args = p.parse_args(argv)
    paths = paths or Paths()
    try:
        if args.local:
            run_local(paths, post or pilot.http_post, args.host, out=out)
        elif args.haiku:
            request = haiku_request
            if request is None:
                from headsup.config import load_settings  # the only place that reads .env

                key = load_settings().anthropic_api_key
                if not key:
                    out("ANTHROPIC_API_KEY is not set (checked via headsup.config)")
                    return 2
                request = pilot.make_haiku_request(pilot.build_haiku_client(key))
            result = run_haiku(paths, request, out=out, limit=args.limit)
            return 1 if result["stopped"] else 0
        else:
            run_report(paths, out=out)
    except (RegistrationError, StageError, FileNotFoundError, ValueError, pilot.CapReached, pilot.CounterError) as exc:
        out(f"refused: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
