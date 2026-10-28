import json
import socket

import pytest

from headsup import config
from headsup.policy import DAILY_LIMIT
from headsup.ui_server import GET_JSON, POST_ROUTES, make_server
from ui_world import EVIL_TITLE, FAKE_BOT, FAKE_BUTTON_TOKEN, FAKE_GH, FAKE_ICAL, FAKE_KEY, SECRETS, FakeRun, Live, make_runner, seed


@pytest.fixture
def live(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    seed(path)
    # the process environment holds fake secrets; the real setting_status must still report names only
    for name, value in (("TELEGRAM_BOT_TOKEN", FAKE_BOT), ("ANTHROPIC_API_KEY", FAKE_KEY), ("GITHUB_TOKEN", FAKE_GH), ("ICAL_URL", FAKE_ICAL)):
        monkeypatch.setenv(name, value)
    run = FakeRun(lines=[f"ical: error with {FAKE_ICAL}", f"token {FAKE_BOT} leaked into a line"])
    runner = make_runner(path, run_once=run)
    server = Live(path, runner, setup=config.setting_status)
    server.run = run
    server.runner = runner
    yield server
    server.close()


# ---------------------------------------------------------------- endpoints return JSON

def test_every_get_endpoint_returns_json_with_the_documented_keys(live):
    for name in GET_JSON:
        status, headers, raw = live.request("GET", f"/api/{name}")
        assert status == 200 and headers["Content-Type"].startswith("application/json"), name
        json.loads(raw)
    st = json.loads(live.request("GET", "/api/status")[2])
    assert {"paused", "running", "interval_minutes", "last_check", "next_check", "listener", "judge", "checks_enabled"} <= set(st)
    assert st["interval_minutes"] == 15 and st["paused"] is False and st["judge"]["up"] is True
    setup = json.loads(live.request("GET", "/api/setup")[2])
    assert setup["TELEGRAM_BOT_TOKEN"] is True and all(isinstance(v, bool) for v in setup.values())
    today = json.loads(live.request("GET", "/api/today")[2])
    assert today["alerts_today"] == 1 and today["limit"] == 5 == DAILY_LIMIT
    assert today["spend_today"] == pytest.approx(0.0000269) and today["spend_total"] == pytest.approx(0.0000269)
    events = json.loads(live.request("GET", "/api/events")[2])
    assert len(events) == 2 and set(events[0]) == {"time", "source", "kind", "title", "probability", "verdict", "reason"}
    assert events[0]["title"] == EVIL_TITLE and events[0]["verdict"] == "interrupt"  # newest first
    alerts = json.loads(live.request("GET", "/api/alerts")[2])
    assert alerts[0]["state"] == "sent, buttons active" and set(alerts[0]) == {"status", "sent_at", "title", "kind", "state"}


def test_unknown_paths_and_methods_are_refused(live):
    assert live.request("GET", "/api/rerun")[0] == 404
    assert live.request("GET", "/nope")[0] == 404
    assert live.request("POST", "/api/rerun", body={})[0] == 404
    assert live.request("POST", "/api/threshold", body={"value": 0.1})[0] == 404


def test_the_page_and_fragment_are_html_with_a_nonce_csp_and_no_external_resources(live):
    status, headers, page = live.request("GET", "/")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    csp = headers["Content-Security-Policy"]
    nonce = csp.split("'nonce-")[1].split("'")[0]
    assert f'<script nonce="{nonce}">' in page and "default-src 'none'" in csp
    assert "http://" not in page.replace("http://127.0.0.1", "") and "https://" not in page and "<link" not in page and "src=" not in page
    assert headers["Cache-Control"] == "no-store" and headers["X-Frame-Options"] == "DENY"
    assert 'name="csrf"' in page and live.ctx.token in page
    for label in ("Check now", "Pause automatic checks", "Preview (dry run)", "Judge (Decider)", "Alerts: 1 of 5"):
        assert label in page
    frag = json.loads(live.request("GET", "/fragment")[2])
    assert "Recent events" in frag["html"] and "Pause automatic checks" in frag["pause_button"]


def test_event_titles_are_html_escaped(live):
    page = live.request("GET", "/")[2]
    frag = json.loads(live.request("GET", "/fragment")[2])["html"]
    for html in (page, frag):
        assert "<script>alert(1)</script>" not in html and "<b>bold</b>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;bold&lt;/b&gt;" in html


# ---------------------------------------------------------------- secrets never appear

def test_no_response_ever_contains_a_secret_value(live):
    bodies = []
    for name in GET_JSON:
        bodies.append(live.request("GET", f"/api/{name}")[2])
    bodies += [live.request("GET", "/")[2], live.request("GET", "/fragment")[2], live.request("GET", "/nope")[2]]
    bodies.append(live.request("POST", "/api/preview", body={})[2])  # the fake run printed lines containing secrets
    bodies.append(live.request("POST", "/api/check", body={}, token="wrong")[2])
    for body in bodies:
        for secret in SECRETS:
            assert secret not in body
    preview = json.loads(live.request("POST", "/api/preview", body={})[2])
    assert any("[redacted]" in line for line in preview["lines"])
    assert FAKE_BUTTON_TOKEN not in json.dumps(preview)


def test_a_server_error_gives_a_generic_message(live, monkeypatch):
    monkeypatch.setattr(live.ctx, "health", lambda: (_ for _ in ()).throw(RuntimeError(f"boom {FAKE_BOT}")))
    status, _, raw = live.request("GET", "/api/status")
    assert status == 500 and json.loads(raw) == {"error": "server error"} and FAKE_BOT not in raw


# ---------------------------------------------------------------- binding and request checks

@pytest.mark.parametrize("host", ["0.0.0.0", "", "192.168.1.5", "example.com", "localhost", "::1"])
def test_a_non_127_0_0_1_bind_is_refused_before_any_socket_opens(host, monkeypatch):
    monkeypatch.setattr(socket.socket, "bind", lambda *a, **k: pytest.fail("a socket was bound"))
    with pytest.raises(ValueError):
        make_server(host, 0, ctx=None)


def test_a_wrong_host_header_is_refused_on_get_and_post(live):
    assert live.request("GET", "/api/status", host="evil.example:80")[0] == 403
    assert live.request("GET", "/", host=f"127.0.0.1.evil.example:{live.port}")[0] == 403
    assert live.request("POST", "/api/pause", body={}, host="evil.example")[0] == 403
    assert live.runner.paused is False


def test_post_without_the_token_or_with_a_wrong_origin_gives_403_and_changes_nothing(live):
    cases = [dict(token=None), dict(token="wrong"), dict(origin=None), dict(origin="http://evil.example"),
             dict(origin=f"http://127.0.0.1:{live.port + 1}"), dict(origin=f"https://127.0.0.1:{live.port}")]
    for route in POST_ROUTES:
        for kw in cases:
            assert live.request("POST", f"/api/{route}", body={"minutes": 5}, **kw)[0] == 403, (route, kw)
    assert live.run.calls == 0 and live.runner.paused is False and live.runner.interval == 15
    assert not live.runner.check_requested.is_set()


def test_post_needs_json_and_a_small_body(live):
    assert live.request("POST", "/api/pause", body="x=1", ctype="application/x-www-form-urlencoded")[0] == 415
    assert live.request("POST", "/api/pause", body="x=1", ctype=None)[0] == 415
    assert live.request("POST", "/api/interval", body=" " * 5000)[0] == 413
    assert live.request("POST", "/api/interval", body="{not json")[0] == 400
    assert live.runner.paused is False and live.runner.interval == 15


# ---------------------------------------------------------------- the buttons

def test_check_now_queues_one_check_and_refuses_while_one_is_running(live):
    status, _, raw = live.request("POST", "/api/check", body={})
    assert status == 202 and json.loads(raw)["result"] == "queued" and live.runner.check_requested.is_set()
    assert live.runner.step() == "check" and live.run.calls == 1 and not live.runner.check_requested.is_set()
    live.runner.run_lock.acquire()
    try:
        status, _, raw = live.request("POST", "/api/check", body={})
        assert status == 409 and json.loads(raw)["result"] == "busy" and not live.runner.check_requested.is_set()
        assert live.request("POST", "/api/preview", body={})[0] == 409  # preview shares the lock
    finally:
        live.runner.run_lock.release()
    assert live.run.calls == 1


def test_pause_and_resume_stop_and_restart_the_automatic_checks(live):
    assert live.request("POST", "/api/pause", body={})[0] == 200
    assert json.loads(live.request("GET", "/api/status")[2])["paused"] is True
    page = live.request("GET", "/")[2]
    assert "Resume automatic checks" in page and "Pause automatic checks" not in page
    assert live.request("POST", "/api/resume", body={})[0] == 200
    assert json.loads(live.request("GET", "/api/status")[2])["paused"] is False


def test_interval_is_changed_within_bounds_and_survives_a_restart(live, tmp_path):
    assert live.request("POST", "/api/interval", body={"minutes": 30})[0] == 200
    assert live.runner.interval == 30
    for bad in (0, 1441, -5, 2.5, "10", True, None):
        assert live.request("POST", "/api/interval", body={"minutes": bad})[0] == 400, bad
    assert live.runner.interval == 30
    assert make_runner(tmp_path / "t.db").interval == 30  # read back from the database


def test_preview_runs_the_dry_run_and_returns_lines(live):
    status, _, raw = live.request("POST", "/api/preview", body={})
    assert status == 200 and live.run.calls == 1 and len(json.loads(raw)["lines"]) == 2


def test_checks_are_off_without_telegram_but_preview_works(tmp_path):
    path = tmp_path / "t.db"
    seed(path)
    server = Live(path, make_runner(path, telegram_ready=False))
    try:
        status, _, raw = server.request("POST", "/api/check", body={})
        assert status == 409 and json.loads(raw)["result"] == "disabled"
        assert server.request("POST", "/api/preview", body={})[0] == 200
        assert "Telegram is not set up" in server.request("GET", "/")[2]
    finally:
        server.close()


# ---------------------------------------------------------------- the judging parts are out of reach

def test_no_route_reaches_the_decider_threshold_limit_or_a_github_action():
    assert POST_ROUTES == ("check", "pause", "resume", "interval", "preview")
    assert GET_JSON == ("status", "setup", "today", "events", "alerts")
    import headsup.ui_page as page
    import headsup.ui_runner as runner
    import headsup.ui_server as server
    for module in (page, runner, server):
        text = open(module.__file__).read()
        assert "ActionHandler" not in text and "GitHubWriter" not in text and "rerun" not in text.lower()
        assert "decider_threshold" not in text and "decider_format" not in text and "under_daily_limit" not in text


def test_all_posts_leave_the_chosen_pair_and_the_limit_unchanged(live):
    before = (config.CHOSEN_FORMAT, config.CHOSEN_THRESHOLD, DAILY_LIMIT)
    for route in POST_ROUTES:
        live.request("POST", f"/api/{route}", body={"minutes": 20})
    assert (config.CHOSEN_FORMAT, config.CHOSEN_THRESHOLD, DAILY_LIMIT) == before == ("F2", 0.40, 5)
    assert json.loads(live.request("GET", "/api/today")[2])["limit"] == 5
