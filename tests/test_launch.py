"""First run and the launcher: the browser opens on the setup page while something required is missing and on the dashboard after that;
Heads-Up.command starts the app (and tells a first-time user to run Install.command when there is nothing to start)."""

import os
import shutil
import stat
import subprocess
import threading
import time

import pytest

from headsup import __main__ as cli
from headsup import config
from world import BOT, CHAT, REPO, World

ROOT = config.ROOT
COMPLETE = {"TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": str(CHAT), "GITHUB_REPOS": REPO}


def wait_for_line(lines, predicate, seconds=5.0):
    """run_ui prints from its own thread, so a line is only there some moments after the server object exists: poll, do not peek."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if any(predicate(line) for line in list(lines)):
            return True
        time.sleep(0.01)
    return any(predicate(line) for line in list(lines))


def start_ui(tmp_path, monkeypatch, env_values=None, browser=None, **kwargs):
    """The real run_ui on an ephemeral port with the fake world and a temp settings file. Returns what the test needs to look and to stop."""
    env = tmp_path / ".env"
    if env_values:
        config.save_env(env_values, env_path=env)
    opened, captured, lines = [], [], []
    real = cli.make_server
    monkeypatch.setattr(cli, "make_server", lambda host, port, ctx: captured.append(real(host, port, ctx)) or captured[-1])
    monkeypatch.setattr(cli.webbrowser, "open", browser or (lambda url: opened.append(url) or True))
    thread = threading.Thread(target=cli.run_ui, args=(config.load_settings(env),), daemon=True,
                              kwargs=dict(out=lines.append, port=0, client=World().client, db_path=tmp_path / "t.db", install_signals=False, env_path=env, **kwargs))
    thread.start()
    deadline = time.monotonic() + 5
    while not captured and time.monotonic() < deadline:
        time.sleep(0.01)
    assert captured, lines

    def stop():
        captured[0].shutdown()
        thread.join(timeout=20)
        assert not thread.is_alive()

    def wait_for_browser(seconds=3.0):
        end = time.monotonic() + seconds
        while not opened and time.monotonic() < end:
            time.sleep(0.01)
        return list(opened)

    return captured[0], lines, opened, stop, wait_for_browser


def test_the_first_run_opens_the_setup_page(tmp_path, monkeypatch):
    server, lines, opened, stop, wait = start_ui(tmp_path, monkeypatch)
    try:
        port = server.server_address[1]
        assert wait() == [f"http://127.0.0.1:{port}/setup"]
        assert "First run: opening the setup page in your browser." in lines
    finally:
        stop()


def test_once_everything_required_is_set_the_dashboard_opens(tmp_path, monkeypatch):
    server, lines, opened, stop, wait = start_ui(tmp_path, monkeypatch, COMPLETE)
    try:
        assert wait() == [f"http://127.0.0.1:{server.server_address[1]}/"]
        assert "Opening the dashboard in your browser." in lines
    finally:
        stop()


@pytest.mark.parametrize("have", [{"TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": str(CHAT)},  # no repository and no calendar
                                  {"TELEGRAM_CHAT_ID": str(CHAT), "GITHUB_REPOS": REPO},  # no bot token
                                  {"TELEGRAM_BOT_TOKEN": BOT, "GITHUB_REPOS": REPO}])  # no chat id
def test_any_missing_required_setting_sends_the_user_to_setup(tmp_path, monkeypatch, have):
    server, lines, opened, stop, wait = start_ui(tmp_path, monkeypatch, have)
    try:
        assert wait()[0].endswith("/setup")
    finally:
        stop()


def test_a_calendar_alone_is_enough_for_a_source(tmp_path, monkeypatch):
    server, lines, opened, stop, wait = start_ui(tmp_path, monkeypatch, {"TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": str(CHAT),
                                                                         "ICAL_URL": "https://calendar.example.invalid/x/basic.ics"})
    try:
        assert wait()[0].endswith("/") and not wait()[0].endswith("/setup")
    finally:
        stop()


def test_the_browser_is_not_opened_when_asked_not_to(tmp_path, monkeypatch):
    server, lines, opened, stop, wait = start_ui(tmp_path, monkeypatch, open_browser=False)
    try:
        assert wait_for_line(lines, lambda line: line.startswith("Heads-Up dashboard: http://127.0.0.1:")), "the app is up and has printed its address"
        assert wait(1.0) == [] and not any("opening" in line.lower() for line in lines)
    finally:
        stop()


def test_a_browser_that_cannot_open_does_not_stop_the_app_and_the_address_is_still_printed(tmp_path, monkeypatch):
    attempts = []

    def no_desktop(url):
        attempts.append(url)
        raise RuntimeError("no desktop")

    server, lines, opened, stop, wait = start_ui(tmp_path, monkeypatch, browser=no_desktop)  # the browser fails from the very start
    try:
        port = server.server_address[1]
        end = time.monotonic() + 5
        while not attempts and time.monotonic() < end:
            time.sleep(0.01)
        assert attempts == [f"http://127.0.0.1:{port}/setup"], "the opener did try, and its failure was swallowed"
        assert wait_for_line(lines, lambda line: line == f"Heads-Up dashboard: http://127.0.0.1:{port}/  (Ctrl-C to stop)"), lines
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
        assert conn.getresponse().status == 200, "and the app is still serving"
        conn.close()
    finally:
        stop()


def test_the_opener_runs_in_a_daemon_timer_so_it_cannot_hold_the_app_open(tmp_path, monkeypatch):
    made = []
    real = threading.Timer

    class SpyTimer(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            made.append(self)

    monkeypatch.setattr(cli.threading, "Timer", SpyTimer)
    server, lines, opened, stop, wait = start_ui(tmp_path, monkeypatch)
    try:
        wait()
        assert len(made) == 1 and made[0].daemon is True and made[0].interval == 0.5
    finally:
        stop()


def test_main_ui_opens_the_browser_by_default():
    import inspect
    assert inspect.signature(cli.run_ui).parameters["open_browser"].default is True


# ---------------------------------------------------------------------------------------------------------- the launcher
LAUNCHER = ROOT / "Heads-Up.command"


def test_the_launcher_is_plain_executable_and_only_starts_the_app():
    text = LAUNCHER.read_text(encoding="utf-8")
    assert os.access(LAUNCHER, os.X_OK) and text.startswith("#!/bin/bash")
    assert 'cd "$(dirname "$0")"' in text and "-m headsup --ui" in text and ".venv/bin/python" in text and "http://127.0.0.1:8765/" in text
    assert ".env" not in text and "sudo" not in text, "the launcher never touches the settings or asks for rights"
    assert "sleep" not in text and not any(line.split()[:1] == ["open"] for line in text.splitlines() if not line.strip().startswith("#")), \
        "the app opens the browser itself, so a second window cannot open"
    assert "Install.command" in text


def run_launcher(tmp_path, with_python):
    """Run a COPY of the launcher in a temp folder, with a stub interpreter or none."""
    folder = tmp_path / "project"
    folder.mkdir()
    shutil.copy(LAUNCHER, folder / "Heads-Up.command")
    if with_python:
        stub = folder / ".venv" / "bin" / "python"
        stub.parent.mkdir(parents=True)
        stub.write_text('#!/bin/bash\necho "STARTED in $(basename "$PWD") with: $* PYTHONPATH=$PYTHONPATH"\n')
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    done = subprocess.run(["/bin/bash", str(folder / "Heads-Up.command")], cwd=tmp_path, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    return done


def test_without_an_install_the_launcher_says_what_to_do_and_stops(tmp_path):
    done = run_launcher(tmp_path, with_python=False)
    assert done.returncode == 1 and "Install.command" in done.stdout and "not installed yet" in done.stdout
    assert "STARTED" not in done.stdout and not (tmp_path / "project" / ".venv").exists(), "it never creates anything"


def test_with_an_install_the_launcher_starts_the_app_from_its_own_folder_whatever_the_current_one(tmp_path):
    done = run_launcher(tmp_path, with_python=True)
    assert done.returncode == 0 and "Starting Heads-Up" in done.stdout
    assert "STARTED in project with: -m headsup --ui PYTHONPATH=src" in done.stdout
