"""Repeated and superseded CI failures: shaped like the live run of 27 Oct 2026 (all names and ids made up)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from headsup import pipeline
from headsup.ci_collapse import ALREADY_ALERTED, ALREADY_RECOVERED, ci_key
from headsup.costlog import CostLog
from headsup.policy import Policy
from headsup.schema import Event
from headsup.store import Store
from headsup.writer import Writer, template_text

NOW = datetime(2026, 11, 3, 2, 0, tzinfo=timezone.utc)
BRANCH_REPO, SANDBOX = "example-org/demo-repo", "example-user/demo-sandbox"
PR_WF, TESTS_WF = "PR Title Check", "tests"


def ci(kind, repo, workflow, branch, run, minutes):
    label = "CI failed" if kind == "ci_failed" else "CI recovered"
    when = NOW - timedelta(minutes=minutes)
    return Event(id=f"github:{repo}:{kind}:{run}", source="github", kind=kind, title=f"{label}: {workflow} on {branch}", body="",
                 url="https://example.invalid/run", occurred_at=when, fetched_at=NOW, content_hash=f"hash-{run}", branch=branch,
                 is_default_branch=branch == "main")


class FixedSource:
    def __init__(self, events):
        self.events, self.warnings = events, []

    def poll(self):
        return list(self.events)


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send(self, text, buttons=()):
        self.sent.append(text)
        return 100 + len(self.sent)


def make_app(tmp_path, source, store=None):
    store = store or Store(tmp_path / "t.db")
    app = pipeline.App(
        store=store, client=None, decider=SimpleNamespace(score=lambda e: 0.44 if e.kind == "ci_failed" else 0.2),
        policy=Policy(store, 0.40, now=lambda: NOW), writer=Writer(None, CostLog(store, now=lambda: NOW)),
        sources=[("github", source)], telegram=FakeTelegram(), write_repos=(SANDBOX,), now=lambda: NOW, out=lambda s: None,
        actions=SimpleNamespace(sweep_expired=lambda: None, process_updates=lambda u: []))
    app.telegram.get_updates = lambda timeout=0: []
    app.decider_host = "127.0.0.1:11434"
    app.client = SimpleNamespace(get=lambda *a, **k: None)  # the Decider counts as up
    return app


def verdicts(store):
    return {event_id: (verdict, reason) for event_id, verdict, reason, _, _ in store.decisions()}


def morning_batch():
    """5 failures of one workflow on one branch, recoveries of OTHER workflows between them, and one sandbox failure on main."""
    events = []
    for i, minutes in enumerate((300, 290, 275, 255, 245)):
        events.append(ci("ci_failed", BRANCH_REPO, PR_WF, "feat/bench", 100 + i, minutes))
        events.append(ci("ci_recovered", BRANCH_REPO, TESTS_WF, "feat/bench", 200 + i, minutes - 1))
    events.append(ci("ci_failed", SANDBOX, "ci", "main", 300, 6))
    return events


def test_morning_batch_gives_one_interrupt_for_the_branch_and_still_sends_the_sandbox_failure(tmp_path):
    app = make_app(tmp_path, FixedSource(morning_batch()))
    summary = pipeline.run_once(app)
    got = verdicts(app.store)
    branch_interrupts = [e for e, (v, _) in got.items() if v == "interrupt" and BRANCH_REPO in e]
    assert branch_interrupts == [f"github:{BRANCH_REPO}:ci_failed:104"]  # the newest of the five
    assert got[f"github:{SANDBOX}:ci_failed:300"][0] == "interrupt"  # within the limit, not starved by the old failures
    assert summary["interrupt"] == 2 and summary["sent"] == 2 and len(app.telegram.sent) == 2
    for run in (100, 101, 102, 103):
        verdict, reason = got[f"github:{BRANCH_REPO}:ci_failed:{run}"]
        assert verdict == "log_only" and reason == "a newer failure of this workflow is in the same batch"
    # recoveries of other workflows do not supersede the failing one
    assert all(got[f"github:{BRANCH_REPO}:ci_recovered:{200 + i}"][0] == "log_only" for i in range(5))


def test_a_failure_with_an_earlier_unrecovered_alert_is_logged_only(tmp_path):
    store = Store(tmp_path / "t.db")
    first = make_app(tmp_path, FixedSource([ci("ci_failed", BRANCH_REPO, PR_WF, "feat/bench", 1, 90)]), store)
    pipeline.run_once(first)
    again = make_app(tmp_path, FixedSource([ci("ci_failed", BRANCH_REPO, PR_WF, "feat/bench", 2, 30)]), store)
    pipeline.run_once(again)
    assert verdicts(store)[f"github:{BRANCH_REPO}:ci_failed:2"] == ("log_only", ALREADY_ALERTED)
    assert ALREADY_ALERTED == "already alerted for this failing workflow" and len(again.telegram.sent) == 0


def test_after_a_recovery_a_new_failure_alerts_again(tmp_path):
    store = Store(tmp_path / "t.db")
    pipeline.run_once(make_app(tmp_path, FixedSource([ci("ci_failed", BRANCH_REPO, PR_WF, "feat/bench", 1, 90)]), store))
    pipeline.run_once(make_app(tmp_path, FixedSource([ci("ci_recovered", BRANCH_REPO, PR_WF, "feat/bench", 2, 60)]), store))
    later = make_app(tmp_path, FixedSource([ci("ci_failed", BRANCH_REPO, PR_WF, "feat/bench", 3, 30)]), store)
    pipeline.run_once(later)
    assert verdicts(store)[f"github:{BRANCH_REPO}:ci_failed:3"][0] == "interrupt" and len(later.telegram.sent) == 1


def test_a_failure_with_a_later_recovery_in_the_same_batch_is_already_recovered(tmp_path):
    app = make_app(tmp_path, FixedSource([ci("ci_failed", BRANCH_REPO, PR_WF, "feat/bench", 1, 60),
                                          ci("ci_recovered", BRANCH_REPO, PR_WF, "feat/bench", 2, 30)]))
    pipeline.run_once(app)
    assert verdicts(app.store)[f"github:{BRANCH_REPO}:ci_failed:1"] == ("log_only", ALREADY_RECOVERED)
    assert ALREADY_RECOVERED == "already recovered" and app.telegram.sent == []


def test_a_failure_with_a_later_recovery_already_in_the_store_is_already_recovered(tmp_path):
    store = Store(tmp_path / "t.db")
    pipeline.run_once(make_app(tmp_path, FixedSource([ci("ci_recovered", BRANCH_REPO, PR_WF, "feat/bench", 2, 30)]), store))
    late = make_app(tmp_path, FixedSource([ci("ci_failed", BRANCH_REPO, PR_WF, "feat/bench", 1, 60)]), store)  # arrives late
    pipeline.run_once(late)
    assert verdicts(store)[f"github:{BRANCH_REPO}:ci_failed:1"] == ("log_only", ALREADY_RECOVERED) and late.telegram.sent == []


def test_other_branches_workflows_and_repos_are_separate_keys(tmp_path):
    events = [ci("ci_failed", BRANCH_REPO, PR_WF, "feat/a", 1, 50), ci("ci_failed", BRANCH_REPO, PR_WF, "feat/b", 2, 40),
              ci("ci_failed", BRANCH_REPO, TESTS_WF, "feat/a", 3, 30), ci("ci_failed", SANDBOX, PR_WF, "feat/a", 4, 20)]
    app = make_app(tmp_path, FixedSource(events))
    assert pipeline.run_once(app)["interrupt"] == 4


def test_collapsed_events_are_logged_without_asking_the_decider(tmp_path):
    asked = []
    app = make_app(tmp_path, FixedSource([ci("ci_failed", BRANCH_REPO, PR_WF, "b", 1, 50), ci("ci_failed", BRANCH_REPO, PR_WF, "b", 2, 40)]))
    app.decider = SimpleNamespace(score=lambda e: asked.append(e.id) or 0.44)
    pipeline.run_once(app)
    assert asked == [f"github:{BRANCH_REPO}:ci_failed:2"]


def test_ci_key_reads_repo_workflow_and_branch():
    assert ci_key(ci("ci_failed", BRANCH_REPO, "PR Title Check", "feat/x", 1, 5)) == (BRANCH_REPO, "PR Title Check", "feat/x")
    assert ci_key(ci("ci_recovered", BRANCH_REPO, "PR Title Check", "feat/x", 2, 5)) == (BRANCH_REPO, "PR Title Check", "feat/x")
    assert ci_key(Event("x", "calendar", "event_upcoming", "t", "", "", NOW, NOW, "h")) is None


def test_template_does_not_double_the_ci_label_or_run_words_together():
    failed = ci("ci_failed", BRANCH_REPO, PR_WF, "feat/bench", 1, 5)
    assert template_text(failed) == "CI failed: PR Title Check on feat/bench. CI on branch feat/bench."
    recovered = ci("ci_recovered", BRANCH_REPO, TESTS_WF, "main", 2, 5)
    assert template_text(recovered) == "CI recovered: tests on main. CI on branch main."
    soon = Event("c1", "calendar", "event_upcoming", "Team sync", "", "", NOW, NOW, "h", start_time=NOW + timedelta(minutes=19))
    assert template_text(soon) == "Starting soon: Team sync. Starts in 19 min."
    assert "CI failed: CI failed" not in template_text(failed)
