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
