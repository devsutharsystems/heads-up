"""GitHub watcher: releases, issues, PRs, CI failed and recovered, with ETag conditional requests.

The HTTP client, store and clock are passed in, so tests run against httpx.MockTransport.
Text from GitHub (titles, bodies) is untrusted data: it is only copied into Event fields.
Timeouts: connect 10 s, read 15 s, one retry on a connect timeout only (headsup.netutil).
CI: failures and recoveries are tracked per (repo, workflow, branch); a recovery is a success newer than the last stored
failure, including a later run_attempt of the same run id. A failed attempt that comes after a recovery of the same run id
gets the id suffix -a{N}, like the recovery; a failure with no recovery in between keeps the plain run id (one alert).
Rate limits: when x-ratelimit-remaining reaches 0, or a 403/429 reports a limit, polling stops
for this run and a warning is recorded; nothing sleeps or retries.
"""

import hashlib
from datetime import datetime, timedelta, timezone

from headsup.netutil import TIMEOUT, with_connect_retry
from headsup.schema import Event

API = "https://api.github.com"
BODY_MAX = 2000
RUN_PAGE = 30


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _hash(source, event_id, title, kind):
    return hashlib.sha256(f"{source}|{event_id}|{title}|{kind}".encode()).hexdigest()


class GitHubSource:
    def __init__(self, repos, client, store, token=None, now=None, max_age=timedelta(days=1)):
        self.repos = list(repos)
        self.client = client
        self.store = store
        self._token = token
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.max_age = max_age
        self.warnings = []
        self.rate_limited = False

    # ------------------------------------------------------------------ http
    def _headers(self, etag):
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "heads-up"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if etag:
            h["If-None-Match"] = etag
        return h

    def _get(self, path, params=None, use_etag=True):
        """Returns parsed JSON, or None when unchanged (304), skipped, or an error (warning recorded)."""
        if self.rate_limited:
            return None
        url = str(self.client.build_request("GET", API + path, params=params).url)
        etag = self.store.get_etag(url) if use_etag else None
        headers = self._headers(etag)
        resp = with_connect_retry(lambda: self.client.get(url, headers=headers, timeout=TIMEOUT))
        remaining = resp.headers.get("x-ratelimit-remaining")
        limited = remaining == "0" or (resp.status_code in (403, 429) and
                                       (remaining == "0" or "retry-after" in resp.headers))
        if limited:
            self.rate_limited = True
            self.warnings.append(f"rate limit reached (reset {resp.headers.get('x-ratelimit-reset', 'unknown')}); stopped polling")
        if resp.status_code == 304:
            return None
        if resp.status_code != 200:
            if not limited:
                self.warnings.append(f"{path}: HTTP {resp.status_code}")
            return None
        if use_etag and resp.headers.get("etag"):
            self.store.set_etag(url, resp.headers["etag"])
        return resp.json()

    # ---------------------------------------------------------------- events
    def _event(self, repo, kind, key, title, body, url, occurred_at, **extra):
        event_id = f"github:{repo}:{kind}:{key}"
        return Event(id=event_id, source="github", kind=kind, title=title, body=(body or "")[:BODY_MAX], url=url,
                     occurred_at=occurred_at, fetched_at=self._now(),
                     content_hash=_hash("github", event_id, title, kind), **extra)

    def poll(self):
        events = []
        for repo in self.repos:
            events += self._releases(repo) + self._issues(repo) + self._pulls(repo) + self._runs(repo)
        cutoff = self._now() - self.max_age
        return sorted((e for e in events if e.occurred_at >= cutoff), key=lambda e: e.occurred_at)

    def _releases(self, repo):
        data = self._get(f"/repos/{repo}/releases", {"per_page": 30}) or []
        return [self._event(repo, "release_published", r["id"], f"{repo} {r.get('name') or r['tag_name']}",
                            r.get("body"), r["html_url"], _ts(r["published_at"]))
                for r in data if not r.get("draft") and r.get("published_at")]

    def _issues(self, repo):
        data = self._get(f"/repos/{repo}/issues", {"state": "open", "sort": "created", "direction": "desc", "per_page": 30}) or []
        return [self._event(repo, "issue_opened", i["number"], i["title"], i.get("body"), i["html_url"], _ts(i["created_at"]))
                for i in data if "pull_request" not in i]

    def _pulls(self, repo):
        data = self._get(f"/repos/{repo}/pulls", {"state": "open", "sort": "created", "direction": "desc", "per_page": 30}) or []
        return [self._event(repo, "pr_opened", p["number"], p["title"], p.get("body"), p["html_url"], _ts(p["created_at"]))
                for p in data]

    def _failure_key(self, repo, run_id, attempt):
        """The plain run id, unless the latest stored event of this run below `attempt` is a recovery: then it is a new
        failure and gets -a{attempt}. A plain failure counts as attempt 0, a recovery or -a{N} event as its attempt."""
        if attempt <= 1:
            return run_id
        latest = None  # (attempt, kind)
        for kind in ("ci_failed", "ci_recovered"):
            for e in self.store.events_of_kind(kind):
                tail = e.id[len(f"github:{repo}:{kind}:"):] if e.id.startswith(f"github:{repo}:{kind}:") else ""
                if tail == str(run_id):
                    n = 0 if kind == "ci_failed" else 1
                elif tail.startswith(f"{run_id}-a") and tail[len(f"{run_id}-a"):].isdigit():
                    n = int(tail[len(f"{run_id}-a"):])
                else:
                    continue
                if n < attempt and (latest is None or n >= latest[0]):
                    latest = (n, kind)
        return f"{run_id}-a{attempt}" if latest and latest[1] == "ci_recovered" else run_id

    def _runs(self, repo):
        # The repo call is not ETag'd: a 304 would not give us the default branch back.
        meta = self._get(f"/repos/{repo}", use_etag=False)
        default = meta.get("default_branch") if meta else None
        data = self._get(f"/repos/{repo}/actions/runs", {"status": "completed", "per_page": RUN_PAGE})
        if not data:
            return []
        out = []
        for run in sorted(data.get("workflow_runs", []), key=lambda r: r["updated_at"]):
            branch, conclusion = run.get("head_branch"), run.get("conclusion")
            if not branch or conclusion not in ("failure", "success"):
                continue
            name = run.get("name") or run.get("display_title") or "workflow"
            when = _ts(run["updated_at"])
            attempt = run.get("run_attempt") or 1
            is_default = None if default is None else branch == default
            state = self.store.get_ci_run(repo, name, branch)  # (failed_at, open, recovered_at) or None
            if conclusion == "failure":
                # Same run id on every attempt, so a re-run that fails again has the same content_hash: no second alert.
                # Only a failure after a recovery of this run is new (key with -a{N}).
                out.append(self._event(repo, "ci_failed", self._failure_key(repo, run["id"], attempt), f"CI failed: {name} on {branch}", run.get("display_title"),
                                       run["html_url"], when, branch=branch, is_default_branch=is_default))
                if state is None or state[1]:  # new failing workflow, or still failing: remember the latest failing time
                    self.store.set_ci_run(repo, name, branch, max(when, state[0]) if state else when, True)
                elif when > state[2]:  # failed again after the recovery; an old failure replayed in the list changes nothing
                    self.store.set_ci_run(repo, name, branch, when, True)
            elif state is not None and state[1] and when > state[0]:
                # A success NEWER than the last stored failure. A re-run attempt gets a key of its own so that a later
                # recovery of the same run id is not mistaken for this one.
                key = run["id"] if attempt == 1 else f"{run['id']}-a{attempt}"
                out.append(self._event(repo, "ci_recovered", key, f"CI recovered: {name} on {branch}", run.get("display_title"),
                                       run["html_url"], when, branch=branch, is_default_branch=is_default))
                self.store.set_ci_run(repo, name, branch, state[0], False, when)
        return out
