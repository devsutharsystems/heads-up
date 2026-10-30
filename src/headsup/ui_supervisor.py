"""Owns the worker thread and its Runner, so the settings can change without restarting the app.

The Runner is built from one Settings object and keeps it for good. To apply new settings the supervisor stops the worker, builds a
new Runner from the new Settings, carries over what should survive (paused, the last check, a requested check), starts a new worker
and tells the server to use the new Runner. Nothing is changed unless the whole sequence can succeed:
  - if a check is still running when the stop is requested, the stop is cancelled and the old worker carries on ("busy");
  - if the new Runner cannot be built, the old worker is started again ("failed").
A restart does not start a check by itself: the new runner inherits the old one's next check time, so saving a setting never
makes the app run a pass at an odd moment. Only a runner that was idle (Telegram not set up yet) starts with a check due at once.
"""

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class RestartResult:
    status: str  # "ok", "busy" (a check was still running: nothing changed) or "failed" (the new settings could not be used)
    detail: str = ""  # an error class name, never a message


class Supervisor:
    def __init__(self, make_runner, settings, *, join_timeout=10.0, grace=1.0):
        self._make_runner = make_runner
        self.settings = settings
        self.runner = make_runner(settings)
        self.on_swap = None  # called with the new Runner after a restart (the server points its handlers at it)
        self.join_timeout, self.grace = join_timeout, grace
        self._lock = threading.RLock()  # one start, stop, restart or suspend at a time
        self._stop = self._done = self._thread = None

    # ------------------------------------------------------------------------------------------ the worker thread
    def _spawn(self):
        stop, done, runner = threading.Event(), threading.Event(), self.runner

        def work():
            try:
                runner.run_forever(stop)
            finally:
                done.set()

        self._stop, self._done = stop, done
        self._thread = threading.Thread(target=work, daemon=True, name="headsup-worker")
        self._thread.start()

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        with self._lock:
            if not self.running():
                self._spawn()

    def _halt(self):
        """Ask the worker to stop and wait for it. True once it has stopped (or none was running). False at once when a check is
        running, and also when one is still running after `join_timeout`: the request is withdrawn and the worker carries on."""
        thread = self._thread
        if thread is None or not thread.is_alive():
            return True
        run_lock = getattr(self.runner, "run_lock", None)
        if run_lock is not None and run_lock.locked():
            return False  # no waiting for a pass that can take minutes: the caller says "busy" now
        self._stop.set()
        thread.join(self.join_timeout)
        if not thread.is_alive():
            return True
        self._stop.clear()
        if self._done.wait(self.grace):  # it had already decided to stop just before the request was withdrawn
            self._spawn()
        return False

    def shutdown(self, timeout=15.0):
        """Stop for good (the app is closing). The worker is a daemon thread, so a check that outlasts the timeout cannot hold the exit."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._stop.set()
                self._thread.join(timeout)

    # ------------------------------------------------------------------------------------------ new settings
    def restart(self, settings):
        with self._lock:
            if not self._halt():
                return RestartResult("busy")
            old = self.runner
            try:
                new = self._make_runner(settings)
            except Exception as exc:  # noqa: BLE001 - only the class name leaves this function
                self._spawn()  # the old runner carries on
                return RestartResult("failed", type(exc).__name__)
            new.paused = old.paused
            new.last = old.last
            if old.telegram_ready:
                new.next_check = old.next_check  # a save is not a reason to run a pass now
            if old.check_requested.is_set():  # a "Check now" that arrived while we swapped
                new.check_requested.set()
            self.runner, self.settings = new, settings
            if self.on_swap is not None:
                self.on_swap(new)
            self._spawn()
            return RestartResult("ok")
