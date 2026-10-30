"""The supervisor: new settings without restarting the app, and a worker that never leaves the app without one."""

import threading
import time

import pytest

from headsup import __main__ as cli
from headsup.ui_runner import Runner
from headsup.ui_server import Context
from headsup.ui_supervisor import RestartResult, Supervisor
from ui_world import FakeRun, make_runner
from world import BOT, CHAT, ICAL_URL, READ_TOKEN, World, settings


class FakeRunner:
    """Just what the supervisor touches. `behaviour(runner, stop)` is what the worker does."""

    def __init__(self, name, behaviour=None):
        self.name, self.paused, self.last = name, False, None
        self.telegram_ready, self.next_check = True, f"next-check-of-{name}"
        self.run_lock = threading.Lock()  # held for the length of a check, like the real Runner's
        self.check_requested = threading.Event()
        self.in_check, self.release = threading.Event(), threading.Event()
        self.runs, self.exits = 0, 0
        self.behaviour = behaviour or idle

    def run_forever(self, stop):
        self.runs += 1
        try:
            self.behaviour(self, stop)
        finally:
            self.exits += 1


def idle(runner, stop):
    while not stop.wait(0.005):
        pass


def long_check(runner, stop):
    """A check in progress: it ignores `stop` until it is released, like a real pass does."""
    with runner.run_lock:
        runner.in_check.set()
        runner.release.wait(10)
    idle(runner, stop)


def slow_to_leave(delay):
    def behaviour(runner, stop):
        idle(runner, stop)
        time.sleep(delay)  # cleanup that outlasts a short join timeout
    return behaviour


def wait_until(cond, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def supervisor(factory=None, **kw):
    built = []

    def default(s):
        runner = FakeRunner(f"for-{s}")
        built.append((s, runner))
        return runner

    def tracked(s):
        runner = (factory or default)(s)
        if factory:
            built.append((s, runner))
        return runner

    sup = Supervisor(tracked, "first", **{"join_timeout": 2.0, "grace": 0.3, **kw})
    return sup, built


def alive_workers():
    return [t for t in threading.enumerate() if t.name == "headsup-worker"]


@pytest.fixture(autouse=True)
def no_worker_left_behind():
    yield
    assert wait_until(lambda: not alive_workers(), 3.0), "a test left a worker thread running"


# ------------------------------------------------------------------------------------------------ starting and stopping
def test_start_runs_one_worker_and_a_second_start_changes_nothing():
    sup, built = supervisor()
    sup.start()
    sup.start()
    assert wait_until(lambda: sup.runner.runs == 1) and len(alive_workers()) == 1
    sup.shutdown()
    assert not sup.running() and sup.runner.exits == 1


def test_shutdown_returns_even_while_a_check_is_still_running():
    sup, _ = supervisor(lambda s: FakeRunner("x", long_check))
    sup.start()
    assert sup.runner.in_check.wait(2)
    started = time.monotonic()
    sup.shutdown(timeout=0.2)
    assert time.monotonic() - started < 1.0, "a daemon worker: the app can exit"
    sup.runner.release.set()
    sup._stop.set()


# ------------------------------------------------------------------------------------------------ restart
def test_restart_builds_the_new_runner_from_the_new_settings_and_swaps_it_in():
    swapped = []
    sup, built = supervisor()
    sup.on_swap = swapped.append
    sup.start()
    old = sup.runner
    assert wait_until(lambda: old.runs == 1)
    assert sup.restart("second") == RestartResult("ok")
    assert [s for s, _ in built] == ["first", "second"]
    new = sup.runner
    assert new is not old and sup.settings == "second" and swapped == [new]
    assert wait_until(lambda: new.runs == 1) and old.exits == 1
    assert len(alive_workers()) == 1, "never two workers at once"
    sup.shutdown()


def test_what_should_survive_a_restart_does():
    sup, _ = supervisor()
    sup.start()
    sup.runner.paused = True
    sup.runner.last = {"at": "yesterday", "summary": {"sent": 2}, "error": None}
    sup.runner.check_requested.set()
    sup.restart("second")
    assert sup.runner.paused is True and sup.runner.last == {"at": "yesterday", "summary": {"sent": 2}, "error": None}
    assert sup.runner.check_requested.is_set()
    sup.shutdown()


def test_a_running_check_makes_a_restart_answer_busy_at_once_instead_of_waiting():
    sup, built = supervisor(lambda s: FakeRunner(s, long_check if s == "first" else idle), join_timeout=5.0)
    sup.start()
    old = sup.runner
    assert old.in_check.wait(2)
    started = time.monotonic()
    assert sup.restart("second") == RestartResult("busy")
    assert time.monotonic() - started < 0.2, "no waiting for a pass that can take minutes"
    assert sup.runner is old and sup.settings == "first" and [s for s, _ in built] == ["first"] and old.exits == 0 and sup.running()
    old.release.set()
    assert wait_until(lambda: not old.run_lock.locked())
    assert sup.restart("second").status == "ok", "and once the check is over the restart goes through"
    sup.shutdown()


def test_a_check_that_outlasts_the_wait_leaves_everything_as_it_was_and_the_old_worker_carries_on():
    sup, built = supervisor(lambda s: FakeRunner(s, long_check), join_timeout=0.2, grace=0.2)
    sup.start()
    old = sup.runner
    assert old.in_check.wait(2)
    swapped = []
    sup.on_swap = swapped.append
    assert sup.restart("second") == RestartResult("busy")
    assert sup.runner is old and sup.settings == "first" and swapped == [] and [s for s, _ in built] == ["first"], "nothing was built or swapped"
    assert sup.running()
    old.release.set()
    time.sleep(0.15)
    assert sup.running() and old.exits == 0, "the worker is still serving the old runner"
    sup.shutdown()


def test_a_worker_that_had_already_begun_to_leave_is_started_again_so_the_app_is_never_without_one():
    sup, _ = supervisor(lambda s: FakeRunner(s, slow_to_leave(0.4)), join_timeout=0.1, grace=2.0)
    sup.start()
    old = sup.runner
    assert wait_until(lambda: old.runs == 1)
    assert sup.restart("second").status == "busy"
    assert sup.runner is old and sup.running(), "a fresh worker took over for the runner that was left running"
    assert wait_until(lambda: old.runs == 2)
    sup.shutdown()


def test_settings_that_cannot_be_used_keep_the_old_runner_running_and_report_only_a_class_name():
    def factory(s):
        if s == "bad":
            raise ValueError("TELEGRAM_BOT_TOKEN value 123456:SECRET-looking-text is wrong")
        return FakeRunner(s)

    sup, _ = supervisor(factory)
    sup.start()
    old = sup.runner
    result = sup.restart("bad")
    assert result == RestartResult("failed", "ValueError") and "SECRET" not in repr(result)
    assert sup.runner is old and sup.settings == "first" and sup.running()
    assert wait_until(lambda: old.runs == 2), "the old runner got a worker again"
    assert sup.restart("fine").status == "ok"
    sup.shutdown()


def test_two_restarts_at_once_are_serialised_and_the_last_wins():
    sup, built = supervisor()
    sup.start()
    seen_alive = []
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            seen_alive.append(len(alive_workers()))
            time.sleep(0.002)

    sampler = threading.Thread(target=sample)
    sampler.start()
    threads = [threading.Thread(target=sup.restart, args=(name,)) for name in ("a", "b", "c")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    stop.set()
    sampler.join()
    assert max(seen_alive) <= 1, "never two workers at once"
    assert sup.settings in ("a", "b", "c") and sup.runner.name == f"for-{sup.settings}" and len(built) == 4
    sup.shutdown()


# ------------------------------------------------------------------------------------------------ a restart is not a check
def test_a_restart_does_not_start_a_check_and_a_pause_survives(tmp_path):
    path = tmp_path / "t.db"
    runs = {"first": FakeRun(), "second": FakeRun()}
    sup = Supervisor(lambda s: make_runner(path, run_once=runs[s]), "first", join_timeout=5.0)
    sup.start()
    assert wait_until(lambda: runs["first"].calls == 1)
    sup.runner.pause()
    due = sup.runner.next_check
    assert sup.restart("second").status == "ok"
    time.sleep(0.3)
    assert isinstance(sup.runner, Runner) and sup.runner.paused is True and sup.runner.next_check == due and runs["second"].calls == 0
    sup.runner.resume()
    time.sleep(0.3)
    assert runs["second"].calls == 0, "resuming does not pull the next check forward past the saved time either"
    sup.shutdown()


def test_saving_while_running_normally_does_not_run_a_pass_but_the_first_setup_does(tmp_path):
    path = tmp_path / "t.db"
    runs = {"a": FakeRun(), "b": FakeRun(), "c": FakeRun()}
    ready = {"a": True, "b": True, "c": True}
    sup = Supervisor(lambda s: make_runner(path, run_once=runs[s], telegram_ready=ready[s]), "a", join_timeout=5.0)
    sup.start()
    assert wait_until(lambda: runs["a"].calls == 1)
    assert sup.restart("b").status == "ok"
    time.sleep(0.3)
    assert runs["b"].calls == 0, "a save is not a reason to check now"
    sup.shutdown()
    # a runner that was idle (Telegram not set up) hands over to one that is ready: that one checks at once
    ready.update(a=False, c=True)
    idle_sup = Supervisor(lambda s: make_runner(path, run_once=runs[s], telegram_ready=ready[s]), "a", join_timeout=5.0)
    idle_sup.start()
    assert idle_sup.restart("c").status == "ok"
    assert wait_until(lambda: runs["c"].calls == 1)
    idle_sup.shutdown()


def test_the_worker_listens_in_two_second_slices_so_a_requested_check_starts_quickly():
    from headsup import ui_runner
    assert round(ui_runner.LISTEN_SLICE_MIN * 60, 3) == 2.0


def test_the_server_context_follows_the_swap(tmp_path):
    path = tmp_path / "t.db"
    sup = Supervisor(lambda s: make_runner(path), "first")
    ctx = Context(runner=sup.runner, open_store=lambda: None, setup_status=dict, health=dict)
    sup.on_swap = lambda runner: setattr(ctx, "runner", runner)  # the line run_ui uses
    first = ctx.runner
    sup.restart("second")
    assert ctx.runner is sup.runner and ctx.runner is not first
    sup.shutdown()


# ------------------------------------------------------------------------------------------------ build_runner (what a restart builds)
def test_build_runner_uses_the_settings_it_is_given_for_readiness_and_for_hiding_secrets(tmp_path):
    from headsup.store import Store
    path = tmp_path / "t.db"
    world = World()
    ready = cli.build_runner(settings(), client=world.client, open_store=lambda: Store(path), now=lambda: None)
    not_ready = cli.build_runner(settings(telegram_bot_token=None, telegram_chat_id=None, github_token="another-fake-token-0002"),
                                 client=world.client, open_store=lambda: Store(path), now=lambda: None)
    assert ready.telegram_ready is True and not_ready.telegram_ready is False
    assert ready._redact(f"x {BOT} y {READ_TOKEN} z {ICAL_URL}") == "x [redacted] y [redacted] z [redacted]"
    assert not_ready._redact(f"{BOT} {READ_TOKEN} another-fake-token-0002") == f"{BOT} {READ_TOKEN} [redacted]", "the redactor knows the NEW secrets only"
    assert world.calls == []
