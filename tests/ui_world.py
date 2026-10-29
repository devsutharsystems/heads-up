"""Shared helpers for the dashboard tests: a seeded temp database, a fake Runner and a live server on 127.0.0.1 port 0.
Every secret here is made up and built at runtime."""

import http.client
import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from headsup.schema import Decision, Event
from headsup.store import Store
from headsup.ui_runner import Redactor, Runner
from headsup.ui_server import Context, make_server

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
FAKE_BOT = "999888" + ":" + "FAKE-telegram-secret-for-tests"
FAKE_KEY = "fake-anthropic-" + "key-for-tests-0001"
FAKE_GH = "fake-github-" + "token-for-tests-0002"
FAKE_ICAL = "https://calendar.example.invalid/ical/" + "fake-private-address-0003/basic.ics"
FAKE_BUTTON_TOKEN = "ab12cd34" + "ef56ab78"
SECRETS = (FAKE_BOT, FAKE_KEY, FAKE_GH, FAKE_ICAL, FAKE_BUTTON_TOKEN)
EVIL_TITLE = "<script>alert(1)</script> & <b>bold</b>"


def event(n, title, kind="ci_failed", source="github"):
    return Event(id=f"github:example/repo:{kind}:{n}", source=source, kind=kind, title=title, body="body text", url="https://example.invalid",
                 occurred_at=NOW, fetched_at=NOW, content_hash=f"hash-{n}")


def seed(path):
    store = Store(path)
    for n, (title, verdict, prob) in enumerate([("Plain title", "log_only", 0.2), (EVIL_TITLE, "interrupt", 0.9)], start=1):
        e = event(n, title)
        store.add_if_new(e)
        store.record_decision(Decision(e.id, verdict, f"reason {n}", NOW, probability=prob, threshold=0.4))
    e = event(2, EVIL_TITLE)
    store.save_alert(e.content_hash, e.id, "alert text")
    store.add_token(FAKE_BUTTON_TOKEN, e.content_hash, "snooze_1h", NOW)
    store.attach_message(e.content_hash, 7)
    store.mark_alert_sent(e.content_hash, NOW)
    store.log_cost("runtime", "claude-haiku-5-5", 119, 30, 0.0000269, at=NOW)
    store.close()


class FakeRun:
    def __init__(self, lines=()):
        self.calls, self.lines = 0, list(lines)

    def __call__(self, app):
        self.calls += 1
        for line in self.lines:
            app.out(line)
        return {"collected": 0, "new": 0, "interrupt": 0, "log_only": 0, "sent": 0, "taps": 0}


def make_runner(path, run_once=None, telegram_ready=True, lines=(), clock=lambda: NOW, listen=None, redact=None):
    run_once = run_once or FakeRun(lines)

    def make_app(out):
        return SimpleNamespace(store=Store(path), out=out)

    return Runner(make_app=make_app, make_preview_app=make_app, telegram_ready=telegram_ready, open_store=lambda: Store(path),
                  now=clock, redact=redact or Redactor(SECRETS), run_once=run_once, listen=listen or (lambda app, m: 0))


HEALTH = {"up": True, "started_by_us": False, "model": "present", "model_name": "strands-decider:2b-mxfp8", "pull_command": "ollama pull x"}


class Live:
    """A server on an ephemeral 127.0.0.1 port, served from a thread."""

    def __init__(self, path, runner, setup=lambda: {"TELEGRAM_BOT_TOKEN": True, "ICAL_URL": False}, health=lambda: dict(HEALTH), setup_service=None):
        self.ctx = Context(runner=runner, open_store=lambda: Store(path), setup_status=setup, health=health, now=lambda: NOW,
                           setup_service=setup_service)
        self.server = make_server("127.0.0.1", 0, self.ctx)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, headers=None, host=None, origin="default", token="default", ctype="application/json"):
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        if method == "POST":
            if origin == "default":
                origin = f"http://127.0.0.1:{self.port}"
            if origin is not None:
                h["Origin"] = origin
            h["X-CSRF-Token"] = self.ctx.token if token == "default" else (token or "")
            if ctype:
                h["Content-Type"] = ctype
        h.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = None if body is None else (body if isinstance(body, (bytes, str)) else json.dumps(body))
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        conn.close()
        return resp.status, dict(resp.getheaders()), raw
