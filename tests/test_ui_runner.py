import threading
from datetime import timedelta
from types import SimpleNamespace

from headsup.telegram import WebhookConflict
from headsup.ui_runner import DEFAULT_INTERVAL, Redactor
from ui_world import FAKE_BOT, NOW, FakeRun, make_runner, seed


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def test_the_first_pass_is_due_at_start_then_waits_for_the_interval(tmp_path):
    clock, run = Clock(), FakeRun()
    r = make_runner(tmp_path / "t.db", run_once=run, clock=clock, listen=lambda app, m: 0)
    assert r.interval == DEFAULT_INTERVAL == 15
    assert r.step() == "check" and run.calls == 1
    assert r.step() == "listen" and run.calls == 1  # not due: only the tap slice runs
    clock.now += timedelta(minutes=14)
    assert r.step() == "listen"
    clock.now += timedelta(minutes=1)
    assert r.step() == "check" and run.calls == 2


def test_paused_stops_scheduled_checks_but_not_taps_and_resume_restarts_them(tmp_path):
    clock, run, slices = Clock(), FakeRun(), []
    r = make_runner(tmp_path / "t.db", run_once=run, clock=clock, listen=lambda app, m: slices.append(m) or 0)
    r.step()
    r.pause()
    clock.now += timedelta(hours=3)
    assert r.step() == "listen" and run.calls == 1 and slices
    assert r.status()["next_check"] is None and r.status()["paused"] is True
    r.resume()
    assert r.step() == "check" and run.calls == 2


def test_check_now_runs_even_when_paused_and_never_overlaps_a_running_pass(tmp_path):
    started, release = threading.Event(), threading.Event()

    def slow_run(app):
        started.set()
        release.wait(5)
        return {}

    r = make_runner(tmp_path / "t.db", run_once=slow_run, listen=lambda app, m: 0)
    r.step()  # the start-up pass is done below
    release.set()
    r.pause()
    assert r.request_check() == "queued"
    release.clear()
    started.clear()
    worker = threading.Thread(target=r.step)
    worker.start()
    assert started.wait(5)
    assert r.request_check() == "busy"  # a second press while the pass runs does nothing
    assert r.preview() == ("busy", [])  # and neither does a preview
    assert r.status()["running"] is True
    release.set()
    worker.join(5)
    assert r.status()["running"] is False and r.last["error"] is None


def test_a_failing_pass_keeps_the_loop_alive_and_records_only_the_class_name(tmp_path):
    def boom(app):
        raise RuntimeError(f"secret {FAKE_BOT}")

    r = make_runner(tmp_path / "t.db", run_once=boom)
    assert r.step() == "check"
    assert r.status()["last_error"] == "RuntimeError" and FAKE_BOT not in repr(r.status())
    assert r.status()["next_check"] is not None


def test_a_webhook_conflict_is_shown_as_the_listener_state(tmp_path):
    def conflict(app):
        raise WebhookConflict("getUpdates", "webhook")

    r = make_runner(tmp_path / "t.db", run_once=conflict)
    r.step()
    assert "webhook" in r.status()["listener"]
    r2 = make_runner(tmp_path / "t.db", listen=lambda app, m: 1)
    r2.next_check = r2.next_check + timedelta(days=1)
    r2.step()
    assert "webhook" in r2.status()["listener"]


def test_without_telegram_nothing_runs_on_a_timer(tmp_path):
    run = FakeRun()
    r = make_runner(tmp_path / "t.db", run_once=run, telegram_ready=False)
    assert r.step() == "idle" and r.request_check() == "disabled" and run.calls == 0
    assert r.status()["checks_enabled"] is False and r.status()["next_check"] is None


def test_a_fresh_app_is_built_for_each_pass_and_its_store_is_closed(tmp_path):
    seed(tmp_path / "t.db")
    built, closed = [], []

    def make_app(out):
        app = SimpleNamespace(store=SimpleNamespace(close=lambda: closed.append(1), get_kv=lambda k: None), out=out)
        built.append(app)
        return app

    from headsup.ui_runner import Runner
    r = Runner(make_app=make_app, make_preview_app=make_app, telegram_ready=True, open_store=lambda: built[0].store if built else
               SimpleNamespace(close=lambda: None, get_kv=lambda k: None), run_once=lambda app: {}, listen=lambda a, m: 0)
    r.step()
    r.request_check()
    r.step()
    assert len(built) == 2 and len(closed) >= 2  # one app per pass, each closed


def test_the_run_stops_when_the_stop_event_is_set(tmp_path):
    r = make_runner(tmp_path / "t.db", listen=lambda app, m: 0)
    stop = threading.Event()
    t = threading.Thread(target=r.run_forever, args=(stop,), kwargs={"pause_s": 0.01})
    t.start()
    stop.set()
    t.join(5)
    assert not t.is_alive()


def test_redactor_replaces_secrets_of_eight_or_more_characters_only():
    red = Redactor(["short", FAKE_BOT, None, ""])
    assert red(f"x {FAKE_BOT} y short") == "x [redacted] y short"
