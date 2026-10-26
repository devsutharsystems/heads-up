import csv
import io
import json
import re

import pytest

from eval import pilot
from eval import pilot_report as pr
from eval import run_main as rm
from main_world import FakeDecider, FakeHaiku, ROOT, rows, run, split_ids, tree


# ---------------------------------------------------------------- registration check

def test_registration_passes_on_the_real_committed_files():
    info = rm.verify_registration(rm.Paths())
    assert info["files_checked"] == 13


@pytest.mark.parametrize("rel", [r for r in rm.REQUIRED if not r.endswith("main_label_sheet.csv")])
def test_registration_refuses_when_a_registered_file_changes(tmp_path, rel):
    paths = tree(tmp_path)
    with open(tmp_path / rel, "ab") as f:
        f.write(b"\n# changed\n")
    with pytest.raises(rm.RegistrationError) as err:
        rm.verify_registration(paths)
    assert rel in str(err.value)


def test_registration_refuses_when_a_non_label_cell_of_the_sheet_changes(tmp_path):
    paths = tree(tmp_path)
    text = paths.sheet.read_text().replace("Haircut", "Haircut!", 1)
    paths.sheet.write_text(text)
    with pytest.raises(rm.RegistrationError, match="main_label_sheet"):
        rm.verify_registration(paths)


def test_registration_refuses_when_the_plan_drops_a_row_or_a_file_is_missing(tmp_path):
    paths = tree(tmp_path)
    kept = [l for l in paths.plan.read_text().splitlines() if "eval/rubric.md" not in l]
    paths.plan.write_text("\n".join(kept))
    with pytest.raises(rm.RegistrationError, match="no longer lists eval/rubric.md"):
        rm.verify_registration(paths)
    paths2 = tree(tmp_path / "b")
    (paths2.root / "eval" / "main_events.jsonl").unlink()
    with pytest.raises(rm.RegistrationError, match="missing"):
        rm.verify_registration(paths2)


def test_registration_refuses_when_the_choice_question_was_not_accepted(tmp_path):
    paths = tree(tmp_path)
    paths.f3_check.write_text(json.dumps({"accepted": False}))
    with pytest.raises(rm.RegistrationError, match="choice question"):
        rm.verify_registration(paths)


def test_every_mode_refuses_after_a_change_and_does_nothing(tmp_path):
    paths = tree(tmp_path)
    decider = FakeDecider(paths)  # built from the intact files first
    with open(paths.events, "ab") as f:
        f.write(b" ")
    for argv in (["--local"], ["--haiku"], ["--report"]):
        code, out = run(argv, paths, post=decider, haiku_request=lambda p: None)
        assert code == 2 and out[-1].startswith("refused:") and "main_events.jsonl" in out[-1]
    assert decider.calls == [] and not paths.results.exists() and not paths.manifest.exists()


def test_labels_are_locked_from_the_first_run(tmp_path):
    paths = tree(tmp_path)
    labelled = paths.sheet.read_text()
    # before the first run a label edit is allowed (the registered hash is of the blanked sheet)
    flipped = re.sub(r"(main-001,.*),0\n", r"\1,1\n", labelled, count=1)
    assert flipped != labelled
    paths.sheet.write_text(flipped)
    rm.guard(paths)
    assert paths.manifest.exists()
    paths.sheet.write_text(labelled)  # any later change to the labels is refused
    with pytest.raises(rm.RegistrationError, match="labelled sheet"):
        rm.guard(paths)


def test_the_plan_is_locked_from_the_first_run(tmp_path):
    paths = tree(tmp_path)
    rm.guard(paths)
    paths.plan.write_text(paths.plan.read_text() + "\nAn amendment nobody registered.\n")
    with pytest.raises(rm.RegistrationError, match="the plan"):
        rm.guard(paths)


# ---------------------------------------------------------------- --local, both stages

def run_stage_one(paths, decider=None):
    decider = decider or FakeDecider(paths)
    code, out = run(["--local"], paths, post=decider)
    return code, out, decider


def test_stage_one_scores_rules_on_all_events_and_the_decider_on_tune_events_only(tmp_path):
    paths = tree(tmp_path)
    tune, test = split_ids(paths)
    code, out, decider = run_stage_one(paths)
    assert code == 0 and "stage 1" in out[-1]
    assert len(decider.calls) == 30 * 3 and {i for i, _ in decider.calls} == tune  # never a test event
    assert {(i, f) for i, f in decider.calls} == {(i, f) for i in tune for f in ("F1", "F2", "F3")}
    r = rows(paths)
    assert sum(1 for x in r if x["decider"] == "rules") == 100 and not any(x["id"] in test for x in r if x["decider"] == "decider")
    assert not any(x["decider"] == "haiku" for x in r)


def test_stage_one_again_makes_no_new_calls(tmp_path):
    paths = tree(tmp_path)
    run_stage_one(paths)
    code, out, decider = run_stage_one(paths)
    assert decider.calls == [] and len(rows(paths)) == 100 + 90


def test_local_never_reads_a_label_value(tmp_path, monkeypatch):
    paths = tree(tmp_path)

    def boom(*a, **k):
        raise AssertionError("--local must not read labels")

    monkeypatch.setattr(pr, "load_labels", boom)
    assert run(["--local"], paths, post=FakeDecider(paths))[0] == 0


def test_non_local_host_is_refused_without_a_call(tmp_path):
    paths = tree(tmp_path)
    decider = FakeDecider(paths)
    code, out = run(["--local", "--host", "example.com:11434"], paths, post=decider)
    assert code == 2 and "127.0.0.1 or localhost" in out[-1] and decider.calls == []


def test_decider_errors_are_rows_not_scores(tmp_path):
    paths = tree(tmp_path)
    code, out, decider = run_stage_one(paths, FakeDecider(paths, down=True))
    assert code == 0
    bad = [x for x in rows(paths) if x["decider"] == "decider"]
    assert len(bad) == 90 and all(x["score"] is None and x["error"] == "OSError" for x in bad)


def stage_two_ready(tmp_path, scorer=None):
    paths = tree(tmp_path)
    run_stage_one(paths, FakeDecider(paths, scorer=scorer))
    assert run(["--report"], paths)[0] == 0
    return paths


def test_stage_two_scores_only_the_chosen_format_on_test_events_and_only_once(tmp_path):
    paths = stage_two_ready(tmp_path)
    sel = json.loads(paths.selection.read_text())
    tune, test = split_ids(paths)
    decider = FakeDecider(paths)
    code, out = run(["--local"], paths, post=decider)
    assert code == 0 and "stage 2" in out[-1]
    assert len(decider.calls) == 70 and {f for _, f in decider.calls} == {sel["format"]} and {i for i, _ in decider.calls} == test
    # a second test stage refuses and calls nothing
    again = FakeDecider(paths)
    code, out = run(["--local"], paths, post=again)
    assert code == 2 and "the test run is once" in out[-1] and again.calls == []


def test_a_partial_test_stage_resumes_without_rescoring(tmp_path):
    paths = stage_two_ready(tmp_path)
    sel = json.loads(paths.selection.read_text())
    _, test = split_ids(paths)
    first = FakeDecider(paths)
    seen = {"n": 0}
    real = first.__call__

    def dies_after_ten(url, body):
        seen["n"] += 1
        if seen["n"] > 10:
            raise OSError("down")
        return real(url, body)

    run(["--local"], paths, post=dies_after_ten)  # 10 scored, 60 recorded as errors
    resume = FakeDecider(paths)
    code, out = run(["--local"], paths, post=resume)
    assert code == 0 and len(resume.calls) == 60  # only the failed ones are asked again
    ok = {r["id"] for r in rows(paths) if r["decider"] == "decider" and r["format"] == sel["format"] and r["error"] is None}
    assert test <= ok  # every test event now has a score (the tune events also have one for this format)


# ---------------------------------------------------------------- --report stage 1 and the selection file

def test_stage_one_report_writes_the_selection_and_no_test_numbers(tmp_path):
    paths = stage_two_ready(tmp_path)
    sel = json.loads(paths.selection.read_text())
    assert sel["format"] in ("F1", "F2", "F3") and 0.05 <= sel["threshold"] <= 0.95
    text = paths.report.read_text()
    assert "## Selection (tune days only)" in text and f"Chosen: Decider {sel['format']}" in text
    assert "## Test days" in text and "Not run yet" in text and "Paired differences" not in text
    assert "|" in text and text.count("### Decider F") == 3


def test_selection_does_not_depend_on_any_test_event_or_test_label(tmp_path):
    a = stage_two_ready(tmp_path / "a")
    b = tree(tmp_path / "b")
    _, test = split_ids(b)
    # flip every TEST label in b before any run: the selection must not move
    out = io.StringIO()
    reader = list(csv.reader(open(b.sheet, newline="")))
    w = csv.writer(out, lineterminator="\n")
    w.writerow(reader[0])
    for r in reader[1:]:
        w.writerow(r[:-1] + [str(1 - int(r[-1])) if r[0] in test else r[-1]])
    b.sheet.write_text(out.getvalue())
    run_stage_one(b, FakeDecider(a))  # the same scores as in a (they follow a's labels)
    assert run(["--report"], b)[0] == 0
    assert json.loads(a.selection.read_text()) == json.loads(b.selection.read_text())


def test_report_without_complete_tune_rows_refuses(tmp_path):
    paths = tree(tmp_path)
    code, out = run(["--report"], paths)
    assert code == 2 and "run --local first" in out[-1] and not paths.selection.exists()


def test_stop_when_no_pair_qualifies_writes_no_selection_and_blocks_the_test_run(tmp_path):
    paths = tree(tmp_path)
    run_stage_one(paths, FakeDecider(paths, scorer=lambda fmt, e: 0.99))  # every tune day has 10 alerts at every threshold
    code, out = run(["--report"], paths)
    assert code == 0 and "STOP" in out[-1] and not paths.selection.exists()
    assert "**STOP.**" in paths.report.read_text()
    again = FakeDecider(paths)
    assert run(["--local"], paths, post=again)[1][-1].startswith("stage 1") and again.calls == []  # still stage 1: no test run
    code, out = run(["--haiku"], paths, haiku_request=lambda p: None)
    assert code == 2 and "no eval/main_selection.json" in out[-1]


# ---------------------------------------------------------------- --haiku

def test_haiku_needs_a_recorded_selection(tmp_path):
    paths = tree(tmp_path)
    code, out = run(["--haiku"], paths, haiku_request=FakeHaiku(paths))
    assert code == 2 and "selection is recorded" in out[-1]


def test_haiku_runs_only_the_test_events_with_the_pilots_request_settings_and_the_shared_counter(tmp_path):
    paths = stage_two_ready(tmp_path)
    _, test = split_ids(paths)
    haiku = FakeHaiku(paths)
    code, out = run(["--haiku"], paths, haiku_request=haiku)
    assert code == 0 and len(haiku.prompts) == 70
    got = {r["id"] for r in rows(paths) if r["decider"] == "haiku"}
    assert got == test
    assert json.loads(paths.counter.read_text()) == {"total": 100, "cap": 200}  # 30 from the pilot + 70
    template = pilot.load_prompts(paths.prompts_dir)["haiku_decider.txt"]
    assert all(r["prompt_sha256"] == template[1] for r in rows(paths) if r["decider"] == "haiku")
    # the request settings are the pilot's, unchanged
    from types import SimpleNamespace

    seen = {}

    class Client:
        messages = SimpleNamespace(create=lambda **kw: seen.update(kw) or SimpleNamespace(
            content=[SimpleNamespace(type="text", text="WAIT")], usage=SimpleNamespace(input_tokens=1, output_tokens=1), stop_reason="end_turn"))

    pilot.make_haiku_request(Client)("prompt")
    assert seen == pilot.haiku_kwargs("prompt") and seen["model"] == "claude-haiku-5-5" and seen["thinking"] == {"type": "disabled"}
    assert not {"temperature", "top_p", "top_k"} & set(seen)


def test_haiku_never_reads_a_label_value(tmp_path, monkeypatch):
    paths = stage_two_ready(tmp_path)
    monkeypatch.setattr(pr, "load_labels", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no labels in --haiku")))
    assert run(["--haiku"], paths, haiku_request=FakeHaiku(paths))[0] == 0


@pytest.mark.parametrize("used, ok", [(130, True), (131, False), (190, False)])
def test_haiku_respects_the_200_call_cap(tmp_path, used, ok):
    paths = stage_two_ready(tmp_path)
    paths.counter.write_text(json.dumps({"total": used, "cap": 200}))
    haiku = FakeHaiku(paths)
    code, out = run(["--haiku"], paths, haiku_request=haiku)
    if ok:
        assert code == 0 and len(haiku.prompts) == 70 and json.loads(paths.counter.read_text())["total"] == 200
    else:
        assert code == 2 and "cap would be exceeded" in out[-1] and haiku.prompts == []
        assert json.loads(paths.counter.read_text())["total"] == used


def test_haiku_resumes_and_never_calls_a_finished_event_again(tmp_path):
    paths = stage_two_ready(tmp_path)
    run(["--haiku", "--limit", "10"], paths, haiku_request=(first := FakeHaiku(paths)))
    assert len(first.prompts) == 10
    second = FakeHaiku(paths)
    run(["--haiku"], paths, haiku_request=second)
    assert len(second.prompts) == 60 and json.loads(paths.counter.read_text())["total"] == 100


def test_haiku_stops_on_an_auth_error(tmp_path):
    paths = stage_two_ready(tmp_path)
    code, out = run(["--haiku"], paths, haiku_request=FakeHaiku(paths, fail_after=3))
    assert code == 1 and any(line.startswith("STOPPED") for line in out)
    assert sum(1 for r in rows(paths) if r["decider"] == "haiku") <= 5
