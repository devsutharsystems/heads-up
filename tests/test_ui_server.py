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
    import headsup.setup_service as service
    import headsup.ui_page as page
    import headsup.ui_runner as runner
    import headsup.ui_server as server
    import headsup.ui_setup_page as setup_page
    from headsup.ui_server import SETUP_ROUTES
    assert SETUP_ROUTES == ("save", "clear", "apply", "find-chat", "use-chat", "test-message", "test-connection")  # a separate group: POST_ROUTES is unchanged
    for module in (page, runner, server, setup_page, service):  # the setup page and its service are out of reach of the judge and of Re-run CI too
        text = open(module.__file__).read()
        assert "ActionHandler" not in text and "GitHubWriter" not in text and "rerun" not in text.lower()
        assert "decider_threshold" not in text and "decider_format" not in text and "under_daily_limit" not in text


def test_all_posts_leave_the_chosen_pair_and_the_limit_unchanged(live):
    before = (config.CHOSEN_FORMAT, config.CHOSEN_THRESHOLD, DAILY_LIMIT)
    for route in POST_ROUTES:
        live.request("POST", f"/api/{route}", body={"minutes": 20})
    assert (config.CHOSEN_FORMAT, config.CHOSEN_THRESHOLD, DAILY_LIMIT) == before == ("F2", 0.40, 5)
    assert json.loads(live.request("GET", "/api/today")[2])["limit"] == 5


# ---------------------------------------------------------------- the look

def test_the_page_has_no_external_urls_or_resources(live):
    import re
    page = live.request("GET", "/")[2]
    frag = json.loads(live.request("GET", "/fragment")[2])["html"]
    for html in (page, frag):
        assert not re.search(r"https?://(?!127\.0\.0\.1)", html), "an external URL"
        assert not re.search(r"""(?:["'(=\s])//[a-z0-9]""", html, re.IGNORECASE), "a protocol-relative URL"
        for banned in ("<link", "<img", "<iframe", "<object", "<embed", "@import", "url(", "src=", "srcset", "<form"):
            assert banned not in html, banned
    assert page.count("<script") == 1 and "<script nonce=" in page and "light" in page


def test_the_redesigned_page_has_pills_cards_badges_and_a_threshold_marker(live):
    page = live.request("GET", "/")[2]
    for text in ("A proactive assistant that interrupts you only when it matters.", "Judge up", "Running", "Alerts today", "1 of 5",
                 "Next check", "Spend", 'role="progressbar"', 'aria-valuenow="1"', "Recent events", "threshold 0.40", 'class="mark"',
                 "badge interrupt", "badge logged", "Interrupt", "Logged", ">GitHub<", "Setup", "dot set", "dot missing",
                 "Alerts sent and their buttons", "Buttons active", "data-rel", "viewport"):
        assert text in page, text
    for colour in ("#4f46e5", "#8b5cf6", "#14b8a6", "#f59e0b", "#0ea5e9", "#22c55e", "#f43f5e", "#0f172a", "#64748b",
                   "#f5f3ff", "#ffffff", "#ecfeff"):
        assert colour in page, colour
    assert "--radius:16px" in page and "border-radius:var(--radius)" in page
    assert "prefers-color-scheme" not in page  # light only


def test_the_state_badges_and_the_limit_badge(tmp_path):
    from datetime import timedelta
    from headsup.schema import Decision
    from headsup.store import Store
    from ui_world import NOW, event
    path = tmp_path / "t.db"
    store = Store(path)
    for n, (verdict, reason) in enumerate([("log_only", "daily limit of 5 interrupts reached"), ("log_only", "probability 0.10 < threshold 0.40")], start=1):
        e = event(n, f"E{n}")
        store.add_if_new(e)
        store.record_decision(Decision(e.id, verdict, reason, NOW, probability=0.45 if n == 1 else 0.1, threshold=0.4))
    for n, (tap, minutes_old) in enumerate([("pending", 0), ("tap", 0), ("old", 120)], start=10):
        e = event(n, f"A{n}")
        store.add_if_new(e)
        store.save_alert(e.content_hash, e.id, "t")
        if tap == "pending":
            continue
        store.add_token("tok" + str(n) * 6, e.content_hash, "snooze_1h", NOW - timedelta(minutes=minutes_old))
        store.attach_message(e.content_hash, n)
        store.mark_alert_sent(e.content_hash, NOW)
        if tap == "tap":
            store.record_tap(f"cb{n}", e.id, "snooze_1h")
    store.close()
    server = Live(path, make_runner(path))
    try:
        page = server.request("GET", "/")[2]
    finally:
        server.close()
    for text in ("badge limit", "Limit reached", "badge pending", "Pending", "badge tapped", "Tapped: snooze 1h", "badge expired", "Expired",
                 'class="fill pass"', 'class="fill"'):
        assert text in page, text


def test_empty_states_and_the_checking_and_disabled_markers(tmp_path):
    path = tmp_path / "empty.db"
    from headsup.store import Store
    Store(path).close()
    runner = make_runner(path)
    server = Live(path, runner)
    try:
        page = server.request("GET", "/")[2]
        assert "No events yet: the next check runs at" in page and "No alerts sent yet." in page
        assert 'data-running="0"' in page and 'data-enabled="1"' in page and "Checking..." in page  # the script sets the label
        runner.running = True
        assert 'data-running="1"' in server.request("GET", "/")[2]
        runner.running = False
        runner.paused = True
        page = server.request("GET", "/")[2]
        assert "Paused" in page and "Resume automatic checks" in page
    finally:
        server.close()
    off = make_runner(path, telegram_ready=False)
    server = Live(path, off)
    try:
        page = server.request("GET", "/")[2]
        assert 'data-enabled="0"' in page and "Checks off" in page and "checks are not scheduled" in page
    finally:
        server.close()


# ---------------------------------------------------------------- second design pass

def test_reasons_become_short_labels_and_the_full_reason_stays_in_a_tooltip():
    from headsup.ui_page import human_reason
    cases = [("interrupt", "probability 0.90 >= threshold 0.40", "Above the alert bar"),
             ("log_only", "probability 0.10 < threshold 0.40; chosen on tune days", "Below the alert bar"),
             ("log_only", "already alerted for this failing workflow", "Repeat of an earlier alert"),
             ("log_only", "a newer failure of this workflow is in the same batch", "Repeat of an earlier alert"),
             ("log_only", "already recovered", "Already fixed"),
             ("log_only", "daily limit of 5 interrupts reached", "Daily limit reached"),
             ("log_only", "decider unavailable: log only", "Judge was down"),
             ("log_only", "something new", "Logged")]
    for verdict, reason, label in cases:
        assert human_reason(verdict, reason) == label


def test_events_are_grouped_by_local_day_filterable_and_alert_rows_are_marked(tmp_path):
    from datetime import timedelta
    from headsup.schema import Decision
    from headsup.store import Store
    from ui_world import NOW, event
    path = tmp_path / "t.db"
    store = Store(path)
    plan = [(1, NOW, "interrupt", "probability 0.90 >= threshold 0.40", 0.9), (2, NOW - timedelta(days=1), "log_only", "already recovered", None),
            (3, NOW - timedelta(days=5), "log_only", "probability 0.10 < threshold 0.40", 0.1),
            (4, NOW, "log_only", "daily limit of 5 interrupts reached", 0.5)]
    for n, when, verdict, reason, prob in plan:
        e = event(n, f"Event {n}")
        store.add_if_new(e)
        store.record_decision(Decision(e.id, verdict, reason, when, probability=prob, threshold=0.4))
    store.close()
    server = Live(path, make_runner(path))
    try:
        page = server.request("GET", "/")[2]
    finally:
        server.close()
    assert page.index(">Today<") < page.index(">Yesterday<") < page.index(">Earlier<")
    for text in ('data-filter="all"', 'data-filter="alerts"', 'data-filter="logged"', 'data-kind="alerts"', 'data-kind="logged"',
                 'class="ev alert"', "Above the alert bar", "Below the alert bar", "Already fixed", "Daily limit reached",
                 'title="already recovered"', 'title="probability 0.10 &lt; threshold 0.40"', "border-left:3px solid var(--accent)",
                 "function applyFilter", "max-width:1100px"):
        assert text in page, text
    assert "probability 0.90 &gt;= threshold 0.40</div>" not in page  # the long reason is no longer shown as text


def test_the_next_check_the_spend_and_the_sources_read_in_plain_words(tmp_path):
    from datetime import timedelta
    from headsup.schema import Decision
    from headsup.store import Store
    from ui_world import NOW, event
    path = tmp_path / "t.db"
    store = Store(path)
    for n, source in ((1, "github"), (2, "ical")):
        e = event(n, f"E{n}", source=source)
        store.add_if_new(e)
        store.record_decision(Decision(e.id, "log_only", "decider unavailable: log only", NOW, probability=None, threshold=0.4))
    store.log_cost("runtime", "claude-haiku-5-5", 119, 30, 0.0000269, at=NOW)
    store.log_cost("runtime", "claude-haiku-5-5", 119, 30, 0.0000269, at=NOW)
    store.close()
    runner = make_runner(path)
    runner.next_check = NOW + timedelta(minutes=12)
    runner.last = {"at": NOW - timedelta(minutes=5), "summary": None, "error": None}
    server = Live(path, runner)
    try:
        page = server.request("GET", "/")[2]
    finally:
        server.close()
    for text in ("in 12 min", "at 17:42, every 15 min", "5 min ago", "&lt; $0.01", "2 Haiku calls today", "$0.0000538 today",
                 "&mdash;", 'title="decider unavailable: log only"', "GitHub", "Calendar", "<svg", "</svg>"):
        assert text in page, text
    assert "no score" not in page


def test_the_soft_aurora_theme_header_toolbar_cards_and_chips(live):
    page = live.request("GET", "/")[2]
    for text in ("linear-gradient(180deg,#f5f3ff 0%,#ffffff 50%,#ecfeff 100%)", 'class="logo"', "width:36px; height:36px", "font-size:var(--t-h1)",
                 ".toolbar { display:flex; flex-wrap:nowrap; align-items:center", "@media (max-width:43.7rem)",
                 'class="tile violet"', 'class="tile teal"', 'class="tile amber"', "width:40px; height:40px", "font-variant-numeric:tabular-nums",
                 "linear-gradient(90deg,#8b5cf6,#6d28d9)", 'class="label github"', 'class="label ical"', ".badge.interrupt { background:var(--grad)",
                 ".badge.limit", ".badge.repeat", ".badge.fixed", 'Score <span class="hint2">&middot; bar 0.40</span>', 'class="ev alert"', 'role="listitem"',
                 "font-variant-caps:all-small-caps", "transition:transform var(--ease)", "--ease:150ms ease", "prefers-reduced-motion: reduce",
                 "translateY(-2px)"):
        if text in ('class="label github"', 'class="label ical"'):
            continue  # the seeded events are GitHub only; the chips are checked below
        assert text in page, text
    assert 'class="dotc interrupt"' in page and 'class="dotc logged"' in page
    assert "mlabel" not in page and page.count("bar 0.40") == 1 and 'class="mark"' in page  # one header label, the marker stays


def test_chip_classes_for_every_decision_and_both_source_chips(tmp_path):
    from headsup.schema import Decision
    from headsup.store import Store
    from headsup.ui_page import CHIPS, decision_kind
    from ui_world import NOW, event
    assert CHIPS == {"interrupt": "Interrupt", "limit": "Limit reached", "repeat": "Repeat", "fixed": "Already fixed", "logged": "Logged"}
    for verdict, reason, kind in (("interrupt", "x", "interrupt"), ("log_only", "daily limit of 5 interrupts reached", "limit"),
                                  ("log_only", "already alerted for this failing workflow", "repeat"), ("log_only", "already recovered", "fixed"),
                                  ("log_only", "probability 0.1 < threshold 0.40", "logged")):
        assert decision_kind({"verdict": verdict, "reason": reason}) == kind
    path = tmp_path / "t.db"
    store = Store(path)
    for n, source in ((1, "github"), (2, "ical")):
        e = event(n, f"E{n}", source=source)
        store.add_if_new(e)
        store.record_decision(Decision(e.id, "log_only", "already recovered", NOW, probability=0.1, threshold=0.4))
    store.close()
    server = Live(path, make_runner(path))
    try:
        page = server.request("GET", "/")[2]
    finally:
        server.close()
    for text in ('class="label github"', 'class="label ical"', ">GitHub<", ">Calendar<", "badge fixed", "Already fixed", 'class="dotc fixed"'):
        assert text in page, text


def test_the_calendar_chip_is_always_calendar_whatever_the_stored_source(tmp_path):
    from headsup.schema import Decision
    from headsup.store import Store
    from headsup.ui_page import source_key
    from ui_world import NOW, event
    for stored in ("ical", "calendar", "Calendar", "CALDAV"):
        assert source_key(stored) == "ical"
    assert source_key("github") == "github"
    path = tmp_path / "t.db"
    store = Store(path)
    for n, source in ((1, "calendar"), (2, "ical"), (3, "github")):
        e = event(n, f"E{n}", source=source)
        store.add_if_new(e)
        store.record_decision(Decision(e.id, "log_only", "probability 0.1 < threshold 0.40", NOW, probability=0.1, threshold=0.4))
    store.close()
    server = Live(path, make_runner(path))
    try:
        page = server.request("GET", "/")[2]
    finally:
        server.close()
    assert page.count('class="label ical"') == 2 and page.count('class="label github"') == 1
    assert page.count(">Calendar<") == 2 and "calendar</span>" not in page and page.count("<svg viewBox=\"0 0 16 16\" fill=\"none\"") >= 2


def test_the_repeat_chip_is_short_with_the_full_text_as_a_tooltip_and_chips_cannot_overflow(tmp_path):
    from headsup.schema import Decision
    from headsup.store import Store
    from ui_world import NOW, event
    path = tmp_path / "t.db"
    store = Store(path)
    e = event(1, "Repeat me")
    store.add_if_new(e)
    store.record_decision(Decision(e.id, "log_only", "already alerted for this failing workflow", NOW, probability=None, threshold=0.4))
    store.close()
    server = Live(path, make_runner(path))
    try:
        page = server.request("GET", "/")[2]
    finally:
        server.close()
    assert '<span class="badge repeat" title="Repeat of an earlier alert">Repeat</span>' in page
    assert 'badge repeat">Repeat of an earlier alert' not in page  # the chip is short (the muted line under the title keeps the long wording)
    assert "max-width:100%; overflow:hidden; text-overflow:ellipsis" in page and ".dec { min-width:0; }" in page


def test_the_control_card_has_no_empty_space_under_the_row(live):
    page = live.request("GET", "/")[2]
    assert "#message:empty { margin:0; }" in page and "min-height:1.4rem" not in page
    assert '<div id="message" role="status" aria-live="polite"></div>' in page and '<pre id="preview"></pre>' in page and "pre:empty { display:none; }" in page


def test_today_keeps_relative_time_and_older_rows_show_the_clock_time_with_a_full_tooltip(tmp_path):
    from datetime import timedelta
    from headsup.schema import Decision
    from headsup.store import Store
    from ui_world import NOW, event
    path = tmp_path / "t.db"
    store = Store(path)
    for n, when in ((1, NOW - timedelta(minutes=57)), (2, NOW - timedelta(days=1)), (3, NOW - timedelta(days=5))):
        e = event(n, f"E{n}")
        store.add_if_new(e)
        store.record_decision(Decision(e.id, "log_only", "probability 0.1 < threshold 0.40", when, probability=0.1, threshold=0.4))
    store.close()
    server = Live(path, make_runner(path))
    try:
        page = server.request("GET", "/")[2]
    finally:
        server.close()
    assert ">57 min ago</time>" in page and page.count("data-rel") >= 1
    # NOW is 12:00 UTC = 17:30 in Asia/Kolkata: yesterday and earlier show the clock time, the tooltip has the date
    assert '<time datetime="2026-11-01T12:00:00+00:00" title="01 Nov 17:30">17:30</time>' in page
    assert '<time datetime="2026-10-28T12:00:00+00:00" title="28 Oct 17:30">17:30</time>' in page
    assert "data-rel>17:30" not in page


def test_header_and_rows_share_one_grid_template_and_one_padding_rule(live):
    page = live.request("GET", "/")[2]
    assert "--cols:130px 150px minmax(0,1fr) 190px 150px" in page
    assert page.count("grid-template-columns:var(--cols)") == 2  # the header row and the event cards use the same variable
    assert "padding:0 calc(var(--pad-r) + 1px) 0 calc(var(--pad-l) + 3px)" in page  # the header adds the card's borders
    assert "padding:12px var(--pad-r) 12px var(--pad-l)" in page and "border-left:3px solid var(--line)" in page
    assert ".dotc { position:absolute; left:14px" in page  # the dot sits in the left padding, not in a grid column
    assert 'white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0' in page
    assert page.count("<span>When</span><span>Source</span><span>Event</span>") == 1 and "<span>Decision</span>" in page
    assert ".ev-head { display:none; } .ev { grid-template-columns:minmax(0,1fr);" in page  # below 700px: no header, stacked cards
