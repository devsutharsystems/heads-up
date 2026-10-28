"""The dashboard's engine: one lock, one schedule, one worker that owns every Telegram call.

The worker alternates between a scheduled/"Check now" pass (pipeline.run_once on a freshly built App, so a stopped Haiku
writer does not stay stopped) and one short tap-polling slice (pipeline.listen). Only the worker polls Telegram, so two
pollers never compete for taps. `run_lock` makes a manual check, a scheduled check and a preview exclusive.
Nothing here changes the Decider, the threshold or the daily limit.
"""

import threading
from datetime import datetime, timedelta, timezone

from headsup import pipeline
from headsup.telegram import WebhookConflict

DEFAULT_INTERVAL = 15
MIN_INTERVAL, MAX_INTERVAL = 1, 1440
LISTEN_SLICE_MIN = 5 / 60  # a 5 s long-poll slice
INTERVAL_KEY = "ui_interval_minutes"


class Redactor:
    """Replaces every known secret value (8+ characters) with [redacted] in text that came from the pipeline."""

    def __init__(self, secrets=()):
        self._secrets = sorted({str(s) for s in secrets if s and len(str(s)) >= 8}, key=len, reverse=True)

    def __call__(self, text):
        for s in self._secrets:
            text = text.replace(s, "[redacted]")
        return text


def valid_interval(value):
    return isinstance(value, int) and not isinstance(value, bool) and MIN_INTERVAL <= value <= MAX_INTERVAL


class Runner:
    def __init__(self, *, make_app, make_preview_app, telegram_ready, open_store, now=None, redact=None,
                 run_once=pipeline.run_once, listen=pipeline.listen):
        self._make_app, self._make_preview_app = make_app, make_preview_app
        self.telegram_ready = telegram_ready
        self._open_store = open_store
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._redact = redact or Redactor()
        self._run_once, self._listen = run_once, listen
        self.run_lock = threading.Lock()
        self._state = threading.Lock()
        self.check_requested = threading.Event()
        self.paused = False
        self.running = False
        self.interval = self._load_interval()
        self.last = None  # {"at": datetime, "summary": dict | None, "error": class name | None}
        self.listener = "ok" if telegram_ready else "not configured"
        self.next_check = self._now()  # the first scheduled pass runs at start-up
        self._listen_app = None
        self.lines = []  # the last pass's printed lines, redacted

    # ------------------------------------------------------------ state
    def _load_interval(self):
        store = self._open_store()
        try:
            raw = store.get_kv(INTERVAL_KEY)
        finally:
            store.close()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_INTERVAL
        return value if valid_interval(value) else DEFAULT_INTERVAL

    def status(self):
        with self._state:
            last = self.last
            return {"paused": self.paused, "running": self.running, "interval_minutes": self.interval,
                    "last_check": None if last is None else last["at"].isoformat(),
                    "last_summary": None if last is None else last["summary"],
                    "last_error": None if last is None else last["error"],
                    "next_check": None if (self.paused or not self.telegram_ready) else self.next_check.isoformat(),
                    "listener": self.listener, "checks_enabled": self.telegram_ready}

    def pause(self):
        with self._state:
            self.paused = True

    def resume(self):
        with self._state:
            self.paused = False
            self.next_check = min(self.next_check, self._now() + timedelta(minutes=self.interval))

    def set_interval(self, minutes):
        """Persist and apply a new interval (whole minutes, 1 to 1440). False if the value is not acceptable."""
        if not valid_interval(minutes):
            return False
        store = self._open_store()
        try:
            store.set_kv(INTERVAL_KEY, minutes)
        finally:
            store.close()
        with self._state:
            self.interval = minutes
            base = self.last["at"] if self.last else self._now()
            self.next_check = base + timedelta(minutes=minutes)
        return True

    def request_check(self):
        """'queued', 'busy' (a pass is running) or 'disabled' (Telegram is not set up)."""
        if not self.telegram_ready:
            return "disabled"
        if self.run_lock.locked():
            return "busy"
        self.check_requested.set()
        return "queued"

    # ----------------------------------------------------------- one step
    def _out(self, line):
        self.lines.append(self._redact(str(line)))
        del self.lines[:-200]

    def _run_check(self):
        self.check_requested.clear()
        with self.run_lock:
            with self._state:
                self.running = True
            self.lines = []
            app, summary, error = None, None, None
            try:
                app = self._make_app(self._out)
                summary = self._run_once(app)
            except WebhookConflict:
                error = "WebhookConflict"
                with self._state:
                    self.listener = "blocked by a Telegram webhook"
            except Exception as exc:  # noqa: BLE001 - the loop must survive; only the class name is kept
                error = type(exc).__name__
            finally:
                if app is not None:
                    app.store.close()
                done = self._now()
                with self._state:
                    self.running = False
                    self.last = {"at": done, "summary": summary, "error": error}
                    self.next_check = done + timedelta(minutes=self.interval)

    def _listen_slice(self):
        if self._listen_app is None:
            self._listen_app = self._make_app(self._out)
        try:
            code = self._listen(self._listen_app, LISTEN_SLICE_MIN)
            with self._state:
                self.listener = "blocked by a Telegram webhook" if code == 1 else "ok"
        except Exception as exc:  # noqa: BLE001
            with self._state:
                self.listener = f"error: {type(exc).__name__}"

    def step(self):
        """One loop iteration. Returns 'check', 'listen' or 'idle'."""
        if not self.telegram_ready:
            return "idle"
        with self._state:
            due = self.check_requested.is_set() or (not self.paused and self._now() >= self.next_check)
        if due:
            self._run_check()
            return "check"
        self._listen_slice()
        return "listen"

    def run_forever(self, stop, pause_s=1.0):
        """The worker loop (a thread). `stop` is a threading.Event."""
        while not stop.is_set():
            kind = self.step()
            if kind == "idle":
                stop.wait(pause_s)
            else:
                stop.wait(0.05 if kind == "check" else 0.5)  # never a hot loop when Telegram answers at once
        if self._listen_app is not None:
            self._listen_app.store.close()
            self._listen_app = None

    # ------------------------------------------------------------ preview
    def preview(self):
        """A dry run on an in-memory copy: ('ok', lines) or ('busy', [])."""
        if not self.run_lock.acquire(blocking=False):
            return "busy", []
        lines = []
        try:
            app = self._make_preview_app(lambda s: lines.append(self._redact(str(s))))
            try:
                self._run_once(app)
            except Exception as exc:  # noqa: BLE001
                lines.append(f"preview stopped: {type(exc).__name__}")
            finally:
                app.store.close()
        finally:
            self.run_lock.release()
        return "ok", lines
