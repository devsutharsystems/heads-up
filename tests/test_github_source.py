import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from headsup.sources.github import GitHubSource
from headsup.store import Store

FIX = Path(__file__).parent / "fixtures" / "github"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
REPO = "example-org/demo-repo"


def load(name):
    return json.loads((FIX / name).read_text())


class FakeGitHub:
    """Serves the fixtures with ETags; counts requests. No network: used through httpx.MockTransport."""

    def __init__(self, runs="runs_1.json"):
        self.runs = runs
        self.requests = []
        self.rate_limit_on = None

    def routes(self):
        base = f"/repos/{REPO}"
        return {base: ("repo.json", False), f"{base}/releases": ("releases.json", True), f"{base}/issues": ("issues.json", True),
                f"{base}/pulls": ("pulls.json", True), f"{base}/actions/runs": (self.runs, True)}

    def __call__(self, request):
        self.requests.append(request)
        name, etagged = self.routes()[request.url.path]
        if request.url.path == self.rate_limit_on:
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1793620000"}, json={"message": "rate limit"})
        etag = f'"{name}"'
        if etagged and request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers={"etag": etag})
        return httpx.Response(200, headers={"etag": etag, "x-ratelimit-remaining": "4990"}, json=load(name))


def make(tmp_path, fake, token=None):
    client = httpx.Client(transport=httpx.MockTransport(fake))
    store = Store(tmp_path / "t.db")
    return GitHubSource([REPO], client, store, token=token, now=lambda: NOW), store


def kinds(events):
    return sorted((e.kind, e.branch) for e in events)


def test_github_source_detects_release_issue_pr_ci_failed_and_recovered_and_uses_etag(tmp_path):
    fake = FakeGitHub()
    src, store = make(tmp_path, fake)

    first = src.poll()
    assert kinds(first) == [("ci_failed", "feature/x"), ("ci_failed", "main"), ("issue_opened", None),
                            ("pr_opened", None), ("release_published", None)]
    failed = {e.branch: e for e in first if e.kind == "ci_failed"}
    assert failed["main"].is_default_branch is True and failed["feature/x"].is_default_branch is False
    assert [e.title for e in first if e.kind == "release_published"] == [f"{REPO} Demo 1.2.0"]  # draft and old release dropped
    assert all(store.add_if_new(e) for e in first)

    # Second poll: nothing changed, so every ETag'd endpoint answers 304 and no events come back.
    fake.requests.clear()
    assert src.poll() == []
    etagged = [r for r in fake.requests if r.url.path != f"/repos/{REPO}"]
    assert len(etagged) == 4 and all(r.headers["if-none-match"] for r in etagged)

    # A later success on main recovers it; the side branch is not recovered by that.
    fake.runs = "runs_2.json"
    third = src.poll()
    recovered = [e for e in third if e.kind == "ci_recovered"]
    assert [(e.branch, e.is_default_branch) for e in recovered] == [("main", True)]
    assert store.add_if_new(recovered[0]) is True
    assert all(store.add_if_new(e) is False for e in third if e.kind == "ci_failed")  # replayed failures dedupe


def test_issue_list_skips_pull_requests(tmp_path):
    src, _ = make(tmp_path, FakeGitHub())
    events = src.poll()
    assert [e.id for e in events if e.kind == "issue_opened"] == [f"github:{REPO}:issue_opened:7"]


def test_token_header_only_when_set(tmp_path):
    fake = FakeGitHub()
    make(tmp_path, fake)[0].poll()
    assert all("authorization" not in r.headers for r in fake.requests)
    fake2 = FakeGitHub()
    make(tmp_path / "b", fake2, token="fake-token-for-test")[0].poll()
    assert all(r.headers["authorization"] == "Bearer fake-token-for-test" for r in fake2.requests)


def test_rate_limit_stops_polling_without_retrying(tmp_path):
    fake = FakeGitHub()
    fake.rate_limit_on = f"/repos/{REPO}/releases"
    src, _ = make(tmp_path, fake)
    assert src.poll() == []
    assert src.rate_limited and any("rate limit" in w for w in src.warnings)
    assert [r.url.path for r in fake.requests] == [f"/repos/{REPO}/releases"]  # one request, then stop


def test_old_events_are_dropped(tmp_path):
    src, _ = make(tmp_path, FakeGitHub())
    src.max_age = timedelta(minutes=30)
    assert src.poll() == []


# ---------------------------------------------------------------- recoveries and re-run attempts
# GitHub's workflow-run object has id, name, run_attempt, conclusion, status, head_branch, updated_at:
# https://docs.github.com/en/rest/actions/workflow-runs (whether the list shows every attempt is not stated there).

def run(rid, conclusion, updated, attempt=1, branch="main", name="CI"):
    return {"id": rid, "name": name, "display_title": "t", "head_branch": branch, "status": "completed", "conclusion": conclusion,
            "run_attempt": attempt, "updated_at": updated, "html_url": f"https://example.invalid/{REPO}/actions/runs/{rid}"}


class RunsGitHub(FakeGitHub):
    """Like FakeGitHub, but the workflow runs come from a list the test changes between polls."""

    def __init__(self, runs):
        super().__init__()
        self.run_list = runs

    def __call__(self, request):
        if request.url.path == f"/repos/{REPO}/actions/runs":
            self.requests.append(request)
            return httpx.Response(200, json={"total_count": len(self.run_list), "workflow_runs": self.run_list})
        if request.url.path in (f"/repos/{REPO}/releases", f"/repos/{REPO}/issues", f"/repos/{REPO}/pulls"):
            return httpx.Response(200, json=[])
        return super().__call__(request)


def ci_events(events):
    return [(e.kind, e.id.rsplit(":", 1)[1]) for e in events if e.kind.startswith("ci_")]


def test_an_old_success_before_a_stored_failure_is_not_a_recovery(tmp_path):
    fake = RunsGitHub([run(900, "failure", "2026-11-02T10:00:00Z")])
    src, store = make(tmp_path, fake)
    assert ci_events(src.poll()) == [("ci_failed", "900")]
    # the list now also holds a success from the day before, older than the stored failure
    fake.run_list = [run(900, "failure", "2026-11-02T10:00:00Z"), run(850, "success", "2026-11-01T11:30:00Z")]
    assert ci_events(src.poll()) == [("ci_failed", "900")]  # the failure again (it dedupes); no recovery
    # a genuinely newer success is the recovery, once
    fake.run_list.append(run(910, "success", "2026-11-02T11:00:00Z"))
    assert ("ci_recovered", "910") in ci_events(src.poll())
    assert [k for k, _ in ci_events(src.poll()) if k == "ci_recovered"] == []


def test_a_failed_run_whose_attempt_2_succeeds_gives_one_recovery(tmp_path):
    fake = RunsGitHub([run(700, "failure", "2026-11-02T09:00:00Z", attempt=1)])
    src, store = make(tmp_path, fake)
    assert ci_events(src.poll()) == [("ci_failed", "700")]
    fake.run_list = [run(700, "success", "2026-11-02T09:20:00Z", attempt=2)]  # same run id, newer attempt
    first = src.poll()
    assert ci_events(first) == [("ci_recovered", "700-a2")] and first[0].branch == "main"
    assert ci_events(src.poll()) == []  # polled again: reported once


def test_attempt_2_failing_again_is_the_same_failure_and_does_not_alert_again(tmp_path):
    fake = RunsGitHub([run(700, "failure", "2026-11-02T09:00:00Z", attempt=1)])
    src, store = make(tmp_path, fake)
    first = src.poll()
    assert all(store.add_if_new(e) for e in first)
    fake.run_list = [run(700, "failure", "2026-11-02T09:20:00Z", attempt=2)]
    second = src.poll()
    assert ci_events(second) == [("ci_failed", "700")]  # reported by the source, but it is the same event ...
    assert [store.add_if_new(e) for e in second] == [False]  # ... so dedupe drops it: no new alert and no recovery


def test_an_old_failure_replayed_after_a_recovery_does_not_reopen_it(tmp_path):
    fake = RunsGitHub([run(1, "failure", "2026-11-02T08:00:00Z")])
    src, store = make(tmp_path, fake)
    src.poll()
    fake.run_list = [run(2, "success", "2026-11-02T09:00:00Z"), run(1, "failure", "2026-11-02T08:00:00Z")]
    assert ("ci_recovered", "2") in ci_events(src.poll())
    assert ci_events(src.poll()) == [("ci_failed", "1")]  # the old failure is still listed: no second recovery
