"""The /setup routes: the security matrix on every route, then what each route does. The server is real (127.0.0.1, an ephemeral
port); the settings file is a temp file; Telegram, GitHub and the calendar are the fake world. Fake secrets are built at run time."""

import http.client
import json
from datetime import datetime, timezone
import os
import stat
import threading
import time
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values

from headsup import __main__ as cli
from headsup import config, setup_checks
from headsup.setup_service import PENDING_SECONDS, SetupService
from headsup.store import Store
from headsup.ui_server import GET_JSON, POST_ROUTES, SETUP_BODY_LIMITS, SETUP_ROUTES, MAX_BODY
from headsup.ui_supervisor import Supervisor
from ui_world import FakeRun, Live, make_runner
from world import BOT, CHAT, ICAL_URL, READ_TOKEN, REPO, World, ics_for

KEY = "fake-anthropic-" + "key-for-tests"
WRITE = "fake-actions-write-token"
SECRETS = (BOT, READ_TOKEN, KEY, WRITE, "secret-address-made-up", str(CHAT), "999000111")
INITIAL = {"TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": str(CHAT), "GITHUB_TOKEN": READ_TOKEN, "GITHUB_REPOS": REPO,
           "ICAL_URL": ICAL_URL, "ANTHROPIC_API_KEY": KEY, "GITHUB_ACTIONS_WRITE_TOKEN": WRITE}
ALL_GET = ["/", "/fragment"] + [f"/api/{n}" for n in GET_JSON] + ["/setup", "/api/setup-form"]


class Spy:
    """Stands in for the setup service where only the guard is under test: it counts every call that gets through."""

    def __init__(self):
        self.calls = []

    def _hit(self, name, body=None):
        self.calls.append((name, body))
        return 200, {"ok": True}

    def form(self, body=None):
        return self._hit("form")

    def required_missing(self):
        return []

    def is_clean(self, text, body=None):
        return True

    def __getattr__(self, name):
        if name in ("save", "clear", "apply", "find_chat", "use_chat", "send_test", "test_connection"):
            return lambda body: self._hit(name, body)
        raise AttributeError(name)


@pytest.fixture
def guard(tmp_path):
    spy = Spy()
    live = Live(tmp_path / "t.db", make_runner(tmp_path / "t.db"), setup_service=spy)
    yield SimpleNamespace(live=live, spy=spy)
    live.close()


def raw(live, method, path, headers, body=b"", skip_host=False, timeout=5):
    """A request with exactly the given headers (http.client adds none of its own that matter here)."""
    conn = http.client.HTTPConnection("127.0.0.1", live.port, timeout=timeout)
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for k, v in headers.items():
        conn.putheader(k, v)
    conn.endheaders(body or None)
    resp = conn.getresponse()
    out = (resp.status, resp.read().decode("utf-8", "replace"))
    conn.close()
    return out


def good_headers(live, **over):
    h = {"Host": f"127.0.0.1:{live.port}", "Origin": f"http://127.0.0.1:{live.port}", "X-CSRF-Token": live.ctx.token, "Content-Type": "application/json"}
    h.update(over)
    return {k: v for k, v in h.items() if v is not None}


def bad_hosts(port):
    return ["evil.example", f"evil.example:{port}", "127.0.0.1", "localhost", "127.0.0.1:1", f"0.0.0.0:{port}", f"[::1]:{port}",
            f"127.0.0.1.evil.example:{port}", f"LOCALHOST:{port}", f"127.0.0.1:{port}.evil.example", ""]


# ========================================================================================================== every GET: the Host check
@pytest.mark.parametrize("path", ALL_GET + ["/nope", "/api/setup/save", "/api/setup/../status", "/setup/", "/api/setup-form/"])
def test_every_get_route_refuses_a_wrong_host_and_leaks_nothing(guard, path):
    live = guard.live
    for host in bad_hosts(live.port):
        status, body = raw(live, "GET", path, {"Host": host})
        assert status == 403 and json.loads(body) == {"error": "forbidden host"}, f"{path} with Host {host!r}"
    status, body = raw(live, "GET", path, {})  # no Host header at all
    assert status == 403 and json.loads(body) == {"error": "forbidden host"}, f"{path} without a Host header"
    assert guard.spy.calls == [], "the refusal came before anything ran"


@pytest.mark.parametrize("path", ALL_GET)
def test_every_get_route_answers_to_both_allowed_hosts(guard, path):
    live = guard.live
    for host in (f"127.0.0.1:{live.port}", f"localhost:{live.port}"):
        status, body = raw(live, "GET", path, {"Host": host})
        assert status == 200, f"{path} with {host}"
        assert "forbidden" not in body[:80]


def test_unknown_get_paths_are_404_for_an_allowed_host(guard):
    for path in ("/nope", "/api/setup/save", "/api/setup/../status", "/api/rerun"):
        assert raw(guard.live, "GET", path, {"Host": f"127.0.0.1:{guard.live.port}"})[0] == 404


# ========================================================================================================== every POST: the whole matrix
def limit_for(route):
    return SETUP_BODY_LIMITS.get(route, MAX_BODY)


@pytest.mark.parametrize("route", SETUP_ROUTES)
def test_a_correct_request_gets_through_the_guard_exactly_once(guard, route):
    live = guard.live
    status, body = live.request("POST", f"/api/setup/{route}", body={})[0::2]
    assert status == 200 and json.loads(body) == {"ok": True}
    assert [c[0] for c in guard.spy.calls] == [{"save": "save", "clear": "clear", "apply": "apply", "find-chat": "find_chat", "use-chat": "use_chat",
                                                "test-message": "send_test", "test-connection": "test_connection"}[route]]
    assert dict(live.request("POST", f"/api/setup/{route}", body={})[1])["Content-Security-Policy"] == "default-src 'none'"


@pytest.mark.parametrize("route", SETUP_ROUTES)
def test_wrong_host_on_a_post_is_refused_before_anything_else(guard, route):
    live = guard.live
    for host in bad_hosts(live.port):
        status, _ = raw(live, "POST", f"/api/setup/{route}", good_headers(live, Host=host, **{"Content-Length": "2"}), b"{}")
        assert status == 403, host
    assert raw(live, "POST", f"/api/setup/{route}", {k: v for k, v in good_headers(live).items() if k != "Host"} | {"Content-Length": "2"}, b"{}")[0] == 403
    assert guard.spy.calls == []


@pytest.mark.parametrize("route", SETUP_ROUTES)
def test_origin_must_be_exactly_the_dashboard_itself(guard, route):
    live = guard.live
    for origin in (None, "", "null", "http://evil.example", f"http://evil.example:{live.port}", f"https://127.0.0.1:{live.port}", "http://127.0.0.1",
                   f"http://127.0.0.1:{live.port}/", f"http://127.0.0.1.evil.example:{live.port}", f"http://[::1]:{live.port}"):
        status, body = raw(live, "POST", f"/api/setup/{route}", good_headers(live, Origin=origin, **{"Content-Length": "2"}), b"{}")
        assert status == 403 and json.loads(body) == {"error": "forbidden origin"}, repr(origin)
    for origin in (f"http://127.0.0.1:{live.port}", f"http://localhost:{live.port}"):
        assert raw(live, "POST", f"/api/setup/{route}", good_headers(live, Origin=origin, **{"Content-Length": "2"}), b"{}")[0] == 200
    assert len(guard.spy.calls) == 2


@pytest.mark.parametrize("route", SETUP_ROUTES)
def test_the_session_token_must_match_exactly(guard, route):
    live = guard.live
    real = live.ctx.token
    for token in (None, "", "x", real[:-1], real + "x", real.upper(), "Bearer " + real):  # (a leading or trailing space is trimmed by HTTP itself)
        status, body = raw(live, "POST", f"/api/setup/{route}", good_headers(live, **{"X-CSRF-Token": token, "Content-Length": "2"}), b"{}")
        assert status == 403 and json.loads(body) == {"error": "forbidden token"}, repr(token)
    assert guard.spy.calls == []


@pytest.mark.parametrize("route", SETUP_ROUTES)
def test_the_body_must_be_declared_as_json(guard, route):
    live = guard.live
    for ctype in (None, "", "text/plain", "application/x-www-form-urlencoded", "multipart/form-data", "text/json", "application/xml"):
        status, body = raw(live, "POST", f"/api/setup/{route}", good_headers(live, **{"Content-Type": ctype, "Content-Length": "2"}), b"{}")
        assert status == 415 and json.loads(body) == {"error": "JSON only"}, repr(ctype)
    assert raw(live, "POST", f"/api/setup/{route}", good_headers(live, **{"Content-Type": "Application/JSON; charset=utf-8", "Content-Length": "2"}), b"{}")[0] == 200
    assert len(guard.spy.calls) == 1


@pytest.mark.parametrize("route", SETUP_ROUTES)
def test_content_length_must_be_a_plain_number_and_never_hangs_the_server(guard, route):
    live = guard.live
    for length in ("abc", "-5", "-1", "+5", "1_0", "5 5", "0x10", "1e3", "", "99999999999"):
        started = time.monotonic()
        status, body = raw(live, "POST", f"/api/setup/{route}", good_headers(live, **{"Content-Length": length}), b"", timeout=3)
        assert status == 400 and json.loads(body) == {"error": "bad length"}, repr(length)
        assert time.monotonic() - started < 2, f"{length!r} must be refused at once, not waited on"
    assert guard.spy.calls == []


def test_a_content_length_made_of_non_ascii_digits_is_refused_too(guard):
    """int() would read Arabic-Indic digits as a number; the guard must not. http.client cannot send them, so a raw socket does."""
    import socket
    live = guard.live
    for route in SETUP_ROUTES:
        head = (f"POST /api/setup/{route} HTTP/1.0\r\nHost: 127.0.0.1:{live.port}\r\nOrigin: http://127.0.0.1:{live.port}\r\n"
                f"X-CSRF-Token: {live.ctx.token}\r\nContent-Type: application/json\r\n").encode() + "Content-Length: ٣\r\n\r\n{}".encode()
        with socket.create_connection(("127.0.0.1", live.port), timeout=3) as sock:
            sock.sendall(head)
            reply = b""
            while chunk := sock.recv(4096):
                reply += chunk
        assert reply.startswith(b"HTTP/1.0 400") and b"bad length" in reply, route
    assert guard.spy.calls == []


@pytest.mark.parametrize("route", SETUP_ROUTES)
def test_a_body_over_the_routes_limit_is_refused_and_one_byte_under_is_not(guard, route):
    live = guard.live
    limit = limit_for(route)
    padding = lambda n: json.dumps({"pad": "x" * (n - len('{"pad": ""}'))}).encode()
    over = padding(limit + 1)
    assert len(over) == limit + 1
    status, body = raw(live, "POST", f"/api/setup/{route}", good_headers(live, **{"Content-Length": str(len(over))}), over)
    assert status == 413 and json.loads(body) == {"error": "body too large"}
    assert guard.spy.calls == []
    exact = padding(limit)
    assert len(exact) == limit
    assert raw(live, "POST", f"/api/setup/{route}", good_headers(live, **{"Content-Length": str(len(exact))}), exact)[0] == 200


def test_only_saving_may_send_more_than_4_kb():
    assert SETUP_BODY_LIMITS == {"save": 16 * 1024} and MAX_BODY == 4096


@pytest.mark.parametrize("route", SETUP_ROUTES)
def test_the_body_must_be_one_json_object(guard, route):
    live = guard.live
    for body in (b"{", b"not json", b"[1, 2]", b'"text"', b"5", b"null", b"true", b"{'a': 1}", b"\xff\xfe"):
        status, text = raw(live, "POST", f"/api/setup/{route}", good_headers(live, **{"Content-Length": str(len(body))}), body)
        assert status == 400 and json.loads(text)["error"] in ("bad JSON", "bad request"), body
    assert guard.spy.calls == []


@pytest.mark.parametrize("path", ["/api/setup/nope", "/api/setup/", "/api/setup", "/api/setupx/save", "/api/setup/save/", "/api/setup/Save", "/api/setup/../check/x",
                                  "/api/setup/save/extra", "/setup", "/api/setup-form"])
def test_unknown_post_paths_are_404_after_the_guard_and_run_nothing(guard, path):
    live = guard.live
    status, body = raw(live, "POST", path, good_headers(live, **{"Content-Length": "2"}), b"{}")
    assert status == 404 and json.loads(body) == {"error": "not found"}
    assert guard.spy.calls == []


@pytest.mark.parametrize("route", SETUP_ROUTES)
def test_a_get_to_a_post_route_is_404_and_does_nothing(guard, route):
    assert raw(guard.live, "GET", f"/api/setup/{route}", {"Host": f"127.0.0.1:{guard.live.port}"})[0] == 404
    assert guard.spy.calls == []


def test_the_setup_routes_do_not_exist_without_a_setup_service(tmp_path):
    live = Live(tmp_path / "t.db", make_runner(tmp_path / "t.db"))
    try:
        assert live.request("GET", "/setup")[0] == 404 and live.request("GET", "/api/setup-form")[0] == 404
        assert live.request("POST", "/api/setup/save", body={})[0] == 404
    finally:
        live.close()


@pytest.mark.parametrize("route", POST_ROUTES)
def test_the_dashboard_routes_use_the_same_strict_guard(tmp_path, route):
    live = Live(tmp_path / "t.db", make_runner(tmp_path / "t.db"))
    try:
        for length in ("-5", "abc", "1_0", "+3", "99999999999"):
            started = time.monotonic()
            status, _ = raw(live, "POST", f"/api/{route}", good_headers(live, **{"Content-Length": length}), b"", timeout=3)
            assert status == 400 and time.monotonic() - started < 2, f"{route} {length!r}"
        assert raw(live, "POST", f"/api/{route}", good_headers(live, **{"Content-Length": str(MAX_BODY + 1)}), b"x" * (MAX_BODY + 1))[0] == 413
    finally:
        live.close()


def test_a_client_that_stops_sending_does_not_hold_a_thread_for_ever(guard):
    from headsup.ui_server import Handler
    assert Handler.timeout == 30


# ========================================================================================================== the page
def test_the_setup_page_has_the_dashboard_csp_a_nonce_and_nothing_external(guard):
    live = guard.live
    status, headers, text = live.request("GET", "/setup")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    csp = headers["Content-Security-Policy"]
    nonce = csp.split("script-src 'nonce-")[1].split("'")[0]
    assert csp == (f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; connect-src 'self'; base-uri 'none'; "
                   "form-action 'none'; frame-ancestors 'none'")
    assert text.count("<script") == 1 and f'<script nonce="{nonce}">' in text
    assert f'<meta name="csrf" content="{live.ctx.token}">' in text
    assert headers["Cache-Control"] == "no-store" and headers["X-Frame-Options"] == "DENY" and headers["Referrer-Policy"] == "no-referrer"
    assert "http://" not in text and "https://" not in text and "<link" not in text and "src=" not in text and "<form" not in text
    assert not any(a in text for a in (" onclick=", " onload=", " onerror=", "javascript:"))
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert banned not in text, banned
    other = live.request("GET", "/setup")[1]["Content-Security-Policy"]
    assert other != csp, "a fresh nonce for every page load"


def test_the_page_contains_no_secret_and_no_value_of_any_setting(tmp_path):
    env = tmp_path / ".env"
    config.save_env(INITIAL, env_path=env)
    config.load_settings(env)
    live = Live(tmp_path / "t.db", make_runner(tmp_path / "t.db"), setup_service=Spy())
    try:
        text = live.request("GET", "/setup")[2]
        assert not [s for s in SECRETS if s in text]
    finally:
        live.close()


def test_the_dashboard_links_to_settings_and_says_what_is_missing(tmp_path):
    spy = Spy()
    spy.required_missing = lambda: ["Telegram bot token", "<b>Telegram chat ID</b>"]
    live = Live(tmp_path / "t.db", make_runner(tmp_path / "t.db"), setup_service=spy)
    try:
        page = live.request("GET", "/")[2]
        assert 'href="/setup"' in page and 'id="settings-link"' in page and "Setup needed: Telegram bot token, &lt;b&gt;Telegram chat ID&lt;/b&gt;." in page
        assert "<b>Telegram chat ID</b>" not in page, "escaped"
        assert "Setup needed" in live.request("GET", "/fragment")[2]
        spy.required_missing = lambda: []
        assert "Setup needed" not in live.request("GET", "/")[2]
    finally:
        live.close()


# ========================================================================================================== the real flows
@pytest.fixture
def app(tmp_path):
    made = []

    def build(env_values=INITIAL, *, run_once=None, factory_fail=None, join_timeout=5.0, monotonic=None, world=None, supervisor_kw=None, real=False):
        env = tmp_path / ".env"
        if env_values:
            config.save_env(env_values, env_path=env)
        first = config.load_settings(env)
        world = world or World(calendar=ics_for(__import__("datetime").datetime(2026, 11, 2, 12, 20, tzinfo=__import__("datetime").timezone.utc)))
        db, built = tmp_path / "t.db", []

        def factory(s):
            if factory_fail and factory_fail(s):
                raise RuntimeError("could not build: " + (s.telegram_bot_token or ""))
            built.append(s)
            ready = s.telegram_bot_token is not None and s.telegram_chat_id is not None
            if real:  # the production runner: it really polls the fake Telegram and keeps who wrote
                return cli.build_runner(s, client=world.client, open_store=lambda: Store(db), now=lambda: datetime.now(timezone.utc))
            return make_runner(db, run_once=run_once or FakeRun(), telegram_ready=ready)

        sup = Supervisor(factory, first, join_timeout=join_timeout, **(supervisor_kw or {}))
        service = SetupService(sup, world.client, env_path=env, open_store=lambda: Store(db), **({"monotonic": monotonic} if monotonic else {}))
        live = Live(db, sup.runner, setup_service=service)
        sup.on_swap = lambda runner: setattr(live.ctx, "runner", runner)
        sup.start()
        ns = SimpleNamespace(live=live, sup=sup, service=service, world=world, env=env, built=built, tmp=tmp_path, responses=[])
        made.append(ns)

        def post(route, body=None, **kw):
            status, headers, text = live.request("POST", f"/api/setup/{route}", body={} if body is None else body, **kw)
            ns.responses.append(text)
            return status, json.loads(text)

        ns.post = post
        return ns

    yield build
    for ns in made:
        ns.live.close()
        ns.sup.shutdown(5)


def file_values(app):
    return {k: v for k, v in dotenv_values(app.env, interpolate=False).items()}


def test_the_form_view_has_every_setting_and_no_secret_and_no_chat_id(app):
    a = app()
    status, headers, text = a.live.request("GET", "/api/setup-form")
    form = json.loads(text)
    assert status == 200 and [f["name"] for f in form["fields"]] == list(config.SETTING_NAMES) and form["unapplied"] is False
    assert not [s for s in SECRETS if s in text]
    by = {f["name"]: f for f in form["fields"]}
    assert by["GITHUB_REPOS"]["value"] == REPO and "value" not in by["GITHUB_TOKEN"] and by["TELEGRAM_CHAT_ID"]["state"] == "set"
    assert [by[n]["editable"] for n in ("DECIDER_FORMAT", "DECIDER_THRESHOLD", "DECIDER_HOST")] == [False, False, False]


def test_a_first_run_form_says_what_is_missing(app):
    a = app(env_values=None)
    form = json.loads(a.live.request("GET", "/api/setup-form")[2])
    assert all(f["state"] in ("missing", "default") for f in form["fields"]) and len(form["required_missing"]) == 3 and form["file_exists"] is False


def test_saving_writes_the_file_privately_restarts_the_worker_and_answers_with_states_only(app):
    a = app(env_values=None)
    new_key = "fake-anthropic-" + "key-brand-new-0007"
    status, data = a.post("save", {"values": {"TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": str(CHAT), "GITHUB_TOKEN": READ_TOKEN,
                                              "GITHUB_REPOS": f"{REPO}\nexample-org/other", "ANTHROPIC_API_KEY": new_key}})
    assert status == 200 and data["applied"] is True and data["saved"] is True and data["unapplied"] is False
    assert data["message"] == "Saved. Settings reloaded." and data["changed"] == ["ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_REPOS", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    assert stat.S_IMODE(os.stat(a.env).st_mode) == 0o600
    assert file_values(a)["GITHUB_REPOS"] == f"{REPO},example-org/other"
    raw_text = a.responses[-1]
    for secret in (BOT, READ_TOKEN, new_key, str(CHAT)):
        assert secret not in raw_text
    by = {f["name"]: f for f in data["form"]["fields"]}
    assert by["TELEGRAM_BOT_TOKEN"]["state"] == "set" and by["GITHUB_REPOS"]["value"] == f"{REPO},example-org/other" and data["form"]["required_missing"] == []
    assert a.sup.runner is a.live.ctx.runner and a.sup.settings.github_repos == (REPO, "example-org/other")
    assert a.sup.settings.anthropic_api_key == new_key and a.sup.runner.telegram_ready is True
    assert len(a.built) == 2, "one restart"
    assert config.setting_status()["GITHUB_REPOS"] is True


def test_an_invalid_value_names_the_setting_only_and_changes_nothing(app):
    a = app()
    before = a.env.read_bytes()
    status, data = a.post("save", {"values": {"GITHUB_REPOS": "not-a-repo", "GITHUB_TOKEN": "good-looking-token-0001"}})
    assert status == 400 and data == {"ok": False, "error": "invalid", "field": "GITHUB_REPOS", "message": "Repositories to watch is not valid."}
    assert a.env.read_bytes() == before and len(a.built) == 1, "no write, no restart, and the valid field was not saved either"
    status, data = a.post("save", {"values": {"TELEGRAM_BOT_TOKEN": "nope-" + "x" * 30}})
    assert status == 400 and data["field"] == "TELEGRAM_BOT_TOKEN" and "nope-" not in json.dumps(data)


def test_names_that_are_not_settings_are_never_echoed(app):
    a = app()
    for body in ({"values": {"<script>alert(1)</script>": "x"}}, {"clear": ["../../etc/passwd"]}, {"values": {"DECIDER_THRESHOLD": "0.9"}}, {"clear": ["DECIDER_HOST"]}):
        status, data = a.post("save", body)
        assert status == 400 and data["error"] == "invalid", body
        assert "script" not in json.dumps(data) and "passwd" not in json.dumps(data)
    assert not file_values(a).get("DECIDER_THRESHOLD") and not file_values(a).get("DECIDER_HOST"), "the judge settings cannot be reached from the page"


@pytest.mark.parametrize("body", [{"values": []}, {"values": "x"}, {"clear": "GITHUB_TOKEN"}, {"values": {}, "extra": 1}, {"clear": [1]},
                                  {"extra": 1}, {"values": {n: "x" for n in range(13)}}])
def test_a_wrongly_shaped_save_is_a_generic_400(app, body):
    a = app()
    status, data = a.post("save", body)
    assert status == 400 and data["error"] in ("bad_request", "invalid")


def test_empty_values_keep_what_is_saved_and_nothing_to_save_is_not_a_restart(app):
    a = app()
    before = a.env.read_bytes()
    status, data = a.post("save", {"values": {"GITHUB_TOKEN": "", "ANTHROPIC_API_KEY": "   "}})
    assert status == 200 and data["changed"] == [] and data["message"] == "Nothing to save." and data["applied"] is False
    assert a.env.read_bytes() == before and len(a.built) == 1
    assert a.post("save", {})[1]["message"] == "Nothing to save."


def test_clear_removes_one_setting_and_applies_it(app):
    a = app()
    status, data = a.post("clear", {"name": "GITHUB_ACTIONS_WRITE_TOKEN"})
    assert status == 200 and data["applied"] is True and data["changed"] == ["GITHUB_ACTIONS_WRITE_TOKEN"]
    assert file_values(a)["GITHUB_ACTIONS_WRITE_TOKEN"] == "" and a.sup.settings.github_actions_write_token is None
    assert {f["name"]: f for f in data["form"]["fields"]}["GITHUB_ACTIONS_WRITE_TOKEN"]["state"] == "missing"
    for bad in ({"name": "DECIDER_FORMAT"}, {"name": "nope"}):
        assert a.post("clear", bad)[0] == 400
    assert a.post("clear", {"name": 5})[0] == 400 and a.post("clear", {})[0] == 400


def test_a_save_while_a_check_is_running_is_kept_but_not_applied_until_apply(app):
    release = threading.Event()
    started = threading.Event()

    def blocking(application):
        started.set()
        release.wait(10)
        return {}

    a = app(run_once=blocking, join_timeout=0.3, supervisor_kw={"grace": 0.2})
    assert started.wait(3)
    status, data = a.post("save", {"values": {"GITHUB_REPOS": "example-org/other"}})
    assert status == 200 and data["applied"] is False and data["error"] == "busy" and data["unapplied"] is True
    assert "Press Apply" in data["message"] and file_values(a)["GITHUB_REPOS"] == "example-org/other"
    assert a.sup.settings.github_repos == (REPO,), "the running app still uses the old settings"
    for route in ("find-chat", "test-message", "test-connection"):
        status, data = a.post(route)
        assert status == 409 and data["status"] == "unapplied", route
    assert json.loads(a.live.request("GET", "/api/setup-form")[2])["unapplied"] is True
    release.set()
    time.sleep(0.2)
    status, data = a.post("apply")
    assert status == 200 and data["applied"] is True and data["unapplied"] is False and data["message"] == "Applied. Settings reloaded."
    assert a.sup.settings.github_repos == ("example-org/other",)


def test_when_the_new_settings_cannot_start_the_old_ones_keep_running_and_apply_retries(app):
    state = {"fail": False}
    a = app(factory_fail=lambda s: state["fail"] and s.github_repos == ("example-org/other",))
    state["fail"] = True
    status, data = a.post("save", {"values": {"GITHUB_REPOS": "example-org/other"}})
    assert status == 200 and data["applied"] is False and data["error"] == "restart" and data["detail"] == "RuntimeError" and data["unapplied"] is True
    assert BOT not in a.responses[-1], "the exception text (which held a token) never leaves"
    assert a.sup.settings.github_repos == (REPO,) and a.sup.running()
    state["fail"] = False
    assert a.post("apply")[1]["applied"] is True and a.sup.settings.github_repos == ("example-org/other",)


def test_a_hand_edited_bad_line_is_reported_by_setting_name_and_the_old_settings_stay(app):
    a = app()
    text = a.env.read_text(encoding="utf-8").replace("DECIDER_THRESHOLD=", "DECIDER_THRESHOLD=banana-0.9-", 1)
    a.env.write_text(text, encoding="utf-8")
    status, data = a.post("save", {"values": {"GITHUB_REPOS": "example-org/other"}})
    assert status == 200 and data["applied"] is False and data["error"] == "reload" and data["names"] == ["DECIDER_THRESHOLD"]
    assert "Judge threshold" in data["message"] and "banana" not in a.responses[-1]
    assert a.sup.settings.github_repos == (REPO,)
    a.env.write_text(text.replace("banana-0.9-", ""), encoding="utf-8")
    assert a.post("apply")[1]["applied"] is True


def test_a_file_that_cannot_be_written_is_a_generic_500_and_nothing_changed(app, monkeypatch):
    a = app()
    before = a.env.read_bytes()
    monkeypatch.setattr(os, "replace", lambda *x: (_ for _ in ()).throw(OSError("disk says no: " + READ_TOKEN)))
    status, data = a.post("save", {"values": {"GITHUB_REPOS": "example-org/other"}})
    assert status == 500 and data == {"ok": False, "error": "file", "message": "The settings file could not be written. Nothing was changed."}
    assert a.env.read_bytes() == before and READ_TOKEN not in a.responses[-1] and len(a.built) == 1


def settle_first_check(a):
    """The frozen test clock makes the first runner due at once, and a restart hands that due time on, so right after start a check
    may be running and a restart would (rightly) answer "busy". Wait until the first pass is done: from then on nothing is due."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        r = a.sup.runner
        if not r.run_lock.locked() and r.next_check > r._now():
            return
        time.sleep(0.01)
    raise AssertionError("the first check never finished")


def three_saves_at_once(a):
    results = []

    def go(values):
        results.append(a.post("save", {"values": values}))

    threads = [threading.Thread(target=go, args=(v,)) for v in ({"GITHUB_REPOS": "example-org/one"}, {"GITHUB_WRITE_REPOS": "example-org/two"},
                                                                {"REMINDER_LEAD_MINUTES": "45"})]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return results


def test_two_saves_at_once_are_serialised_and_both_land(app):
    a = app()
    settle_first_check(a)  # no check is running, so every restart can go ahead
    results = three_saves_at_once(a)
    assert [s for s, _ in results] == [200, 200, 200] and all(d["applied"] for _, d in results)
    values = file_values(a)
    assert (values["GITHUB_REPOS"], values["GITHUB_WRITE_REPOS"], values["REMINDER_LEAD_MINUTES"]) == ("example-org/one", "example-org/two", "45")
    assert a.sup.settings.github_repos == ("example-org/one",) and a.sup.settings.reminder_lead_minutes == 45
    assert len(a.built) == 4 and a.sup.running()


def test_saves_at_once_while_a_check_runs_are_all_written_say_so_truthfully_and_one_apply_activates_them(app):
    a = app()
    settle_first_check(a)
    a.sup.runner.run_lock.acquire()  # a check is running
    try:
        results = three_saves_at_once(a)
        assert [s for s, _ in results] == [200, 200, 200]
        assert all(d["saved"] and d["applied"] is False and d["error"] == "busy" and d["unapplied"] for _, d in results), "nothing was activated, and each answer says so"
        values = file_values(a)
        assert (values["GITHUB_REPOS"], values["GITHUB_WRITE_REPOS"], values["REMINDER_LEAD_MINUTES"]) == ("example-org/one", "example-org/two", "45")
        assert a.sup.settings.reminder_lead_minutes != 45 and len(a.built) == 1, "the running app is untouched"
    finally:
        a.sup.runner.run_lock.release()
    status, data = a.post("apply")
    assert status == 200 and data["applied"] is True and data["unapplied"] is False
    s = a.sup.settings
    assert s.github_repos == ("example-org/one",) and s.reminder_lead_minutes == 45 and a.sup.running()


# ---------------------------------------------------------------------------------------------------------- the Telegram helper
def spy_on_finder(a, monkeypatch):
    seen = []
    real = setup_checks.find_chats

    def spy(settings, client):
        seen.append(a.sup.running())  # was the worker stopped while Telegram was asked?
        return real(settings, client)

    monkeypatch.setattr(setup_checks, "find_chats", spy)
    return seen


def test_find_chat_without_a_token_asks_nothing(app):
    a = app(env_values={"GITHUB_REPOS": REPO})
    status, data = a.post("find-chat")
    assert status == 200 and data["status"] == "no_token" and a.world.calls == []


def test_one_private_chat_is_offered_for_confirmation_not_saved(app, monkeypatch):
    a = app(env_values={"TELEGRAM_BOT_TOKEN": BOT, "GITHUB_REPOS": REPO})
    stranger_or_you = 999000111
    a.world.message(stranger_or_you, name="Ada", last_name="Lovelace")
    seen = spy_on_finder(a, monkeypatch)
    status, data = a.post("find-chat")
    assert status == 200 and data["status"] == "choose" and data["single"] is True and data["message"] == "Is this you?"
    assert data["choices"] == [{"index": 0, "name": "Ada Lovelace", "last": datetime.fromtimestamp(1_793_620_000, tz=timezone.utc).isoformat()}]
    assert "999000111" not in a.responses[-1], "the chat id never reaches the browser"
    assert "TELEGRAM_CHAT_ID" not in file_values(a) or not file_values(a)["TELEGRAM_CHAT_ID"], "nothing is saved until the user confirms"
    assert seen == [True] and a.sup.running(), "the worker was never stopped: it is idle without a chat ID, so nobody else is polling"
    assert a.world.calls[-1] == ("getUpdates", {"timeout": 0, "allowed_updates": ["message"]})


def test_confirming_the_chat_saves_it_applies_it_and_never_shows_the_id(app):
    a = app(env_values={"TELEGRAM_BOT_TOKEN": BOT, "GITHUB_REPOS": REPO})
    a.world.message(999000111, name="Ada")
    a.post("find-chat")
    status, data = a.post("use-chat", {"index": 0})
    assert status == 200 and data["status"] == "saved" and data["name"] == "Ada" and data["applied"] is True
    assert file_values(a)["TELEGRAM_CHAT_ID"] == "999000111" and a.sup.settings.telegram_chat_id == 999000111 and a.sup.runner.telegram_ready is True
    assert "999000111" not in a.responses[-1] and {f["name"]: f for f in data["form"]["fields"]}["TELEGRAM_CHAT_ID"]["state"] == "set"
    status, data = a.post("use-chat", {"index": 0})
    assert status == 409 and data["status"] == "expired", "a list can be used once"


def test_several_private_chats_must_be_picked_and_groups_are_not_offered(app):
    a = app(env_values={"TELEGRAM_BOT_TOKEN": BOT, "GITHUB_REPOS": REPO})
    a.world.message(111111111, name="Ada", date=1_793_620_000)
    a.world.message(222222222, name="Grace", date=1_793_621_000)
    a.world.message(-100333333, kind="supergroup", name="Team")
    status, data = a.post("find-chat")
    assert data["status"] == "choose" and data["single"] is False and [c["name"] for c in data["choices"]] == ["Grace", "Ada"]
    assert "1 group chat(s) were ignored" in data["message"] and not any(i in a.responses[-1] for i in ("111111111", "222222222", "100333333"))
    status, data = a.post("use-chat", {"index": 1})
    assert data["name"] == "Ada" and file_values(a)["TELEGRAM_CHAT_ID"] == "111111111", "the picked one, not the first"


def test_the_finder_reports_nothing_found_a_webhook_and_a_busy_worker(app):
    a = app(env_values={"TELEGRAM_BOT_TOKEN": BOT, "GITHUB_REPOS": REPO})
    status, data = a.post("find-chat")
    assert status == 200 and data["ok"] is False and data["status"] == "none" and "24 hours" in data["message"]
    a.world.webhook_set = True
    status, data = a.post("find-chat")
    assert data["status"] == "webhook" and "will not do it for you" in data["message"] and a.sup.running()
    assert "deleteWebhook" not in a.world.methods()


def test_the_finder_answers_at_once_from_what_the_worker_saw_even_while_a_check_runs(app):
    release, started = threading.Event(), threading.Event()
    a = app(env_values=INITIAL, run_once=lambda application: (started.set(), release.wait(10), {})[2])
    assert started.wait(3)
    began = time.monotonic()
    status, data = a.post("find-chat")
    assert time.monotonic() - began < 1.0 and status == 200 and data["status"] == "none" and "10 minutes" in data["message"]
    assert a.sup.running() and "getUpdates" not in a.world.methods(), "Telegram was not asked: the worker is the one that polls"
    store = Store(a.tmp / "t.db")
    store.note_chat(999000111, "Ada", datetime.now(timezone.utc), datetime.now(timezone.utc))
    store.close()
    status, data = a.post("find-chat")
    assert status == 200 and data["status"] == "choose" and data["single"] is True and [c["name"] for c in data["choices"]] == ["Ada"]
    assert "999000111" not in a.responses[-1] and "getUpdates" not in a.world.methods()
    release.set()


def test_the_finder_does_not_wait_for_a_save_in_progress_or_hold_the_settings_lock(app):
    a = app(env_values={"TELEGRAM_BOT_TOKEN": BOT, "GITHUB_REPOS": REPO})
    a.world.message(999000111, name="Ada")
    with a.service._lock:  # a save is in the middle of its restart
        began = time.monotonic()
        status, data = a.post("find-chat")
        assert time.monotonic() - began < 1.0 and status == 200 and data["status"] == "choose"
    assert a.post("use-chat", {"index": 0})[1]["status"] == "saved", "and choosing still works once the save is done"


def test_the_finder_with_a_ready_worker_never_confirms_or_asks_telegram_even_for_a_waiting_tap(app):
    a = app(env_values=INITIAL)
    a.post("find-chat")
    a.post("find-chat")
    assert "getUpdates" not in a.world.methods()


def test_a_message_the_running_worker_saw_is_offered_even_after_a_button_tap_confirmed_a_later_update(app):
    """The live bug, end to end with the production worker: "hi", then a later update that gets confirmed (in the live case a tap on an
    alert; here a group message, which needs no tap handling), then Find my chat ID. The tap itself is tested in test_seen_chats.py."""
    a = app(env_values=INITIAL, real=True)
    a.world.message(999000111, name="Ada", update_id=500, date=int(time.time()))
    a.world.message(-100555, kind="supergroup", name="Team", update_id=501, date=int(time.time()))  # a later update, confirmed too (offset 502)
    deadline = time.monotonic() + 15
    store = Store(a.tmp / "t.db")
    try:
        while store.get_kv("telegram_offset") != "502" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert store.get_kv("telegram_offset") == "502"
    finally:
        store.close()
    while any(u["update_id"] < 502 for u in a.world.updates) and time.monotonic() < deadline:  # the worker's next poll sends offset 502
        time.sleep(0.05)
    assert not [u for u in a.world.updates if u["update_id"] < 502], "Telegram has forgotten the message, as it does after a confirmed tap"
    status, data = a.post("find-chat")
    assert status == 200 and data["status"] == "choose" and data["single"] is True and [c["name"] for c in data["choices"]] == ["Ada"]
    assert "999000111" not in a.responses[-1]


def test_apply_and_save_say_busy_at_once_while_a_check_runs(app):
    release, started = threading.Event(), threading.Event()
    a = app(env_values=INITIAL, run_once=lambda application: (started.set(), release.wait(10), {})[2], join_timeout=30.0)
    assert started.wait(3)
    began = time.monotonic()
    status, data = a.post("save", {"values": {"GITHUB_REPOS": "example-org/one"}})
    assert time.monotonic() - began < 1.0, "no waiting for the check"
    assert status == 200 and data["saved"] is True and data["applied"] is False and data["error"] == "busy"
    began = time.monotonic()
    status, data = a.post("apply")
    assert time.monotonic() - began < 1.0 and data["error"] == "busy" and a.sup.running()
    release.set()


def test_a_save_while_things_run_normally_does_not_start_a_check(app):
    runs = FakeRun()
    a = app(env_values=INITIAL, run_once=runs)
    deadline = time.monotonic() + 3
    while runs.calls < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runs.calls == 1
    status, data = a.post("save", {"values": {"GITHUB_REPOS": "example-org/one"}})
    assert data["applied"] is True
    time.sleep(0.5)
    assert runs.calls == 1, "saving a setting did not run a pass"


@pytest.mark.parametrize("body", [{"index": True}, {"index": "0"}, {"index": 1.5}, {"index": None}, {}, {"index": 0, "extra": 1}, {"index": -1}, {"index": 5}])
def test_a_bad_choice_is_refused_and_saves_nothing(app, body):
    a = app(env_values={"TELEGRAM_BOT_TOKEN": BOT, "GITHUB_REPOS": REPO})
    a.world.message(999000111, name="Ada")
    a.post("find-chat")
    status, data = a.post("use-chat", body)
    assert status == 400 and data["error"] == "bad_request"
    assert not file_values(a).get("TELEGRAM_CHAT_ID")
    assert a.post("use-chat", {"index": 0})[1]["status"] == "saved", "the list is still valid after a refused request"


def test_a_list_of_chats_expires(app):
    clock = SimpleNamespace(now=1000.0)
    a = app(env_values={"TELEGRAM_BOT_TOKEN": BOT, "GITHUB_REPOS": REPO}, monotonic=lambda: clock.now)
    a.world.message(999000111, name="Ada")
    a.post("find-chat")
    clock.now += PENDING_SECONDS + 1
    status, data = a.post("use-chat", {"index": 0})
    assert status == 409 and data["status"] == "expired" and not file_values(a).get("TELEGRAM_CHAT_ID")


def test_the_test_message_is_one_fixed_text_to_the_saved_chat_and_is_rate_limited(app):
    clock = SimpleNamespace(now=100.0)
    a = app(monotonic=lambda: clock.now)
    status, data = a.post("test-message")
    assert status == 200 and data["ok"] is True and data["status"] == "sent" and data["message"].startswith("SENT: Telegram accepted the test message (message number ")
    assert a.world.sent() == [{"chat_id": CHAT, "text": setup_checks.TEST_TEXT}]
    clock.now += 2
    status, data = a.post("test-message")
    assert status == 429 and data["ok"] is False and data["status"] == "too_soon" and "Wait 3 seconds" in data["message"] and len(a.world.sent()) == 1
    clock.now += 3.1
    assert a.post("test-message")[0] == 200 and len(a.world.sent()) == 2


def test_the_test_message_needs_a_saved_chat(app):
    a = app(env_values={"TELEGRAM_BOT_TOKEN": BOT, "GITHUB_REPOS": REPO})
    assert a.post("test-message")[1] == {"ok": False, "status": "not_configured",
                                         "message": "NOT SENT (not_configured): Save the bot token and the chat ID first."} and a.world.sent() == []


def test_a_failed_test_message_does_not_start_the_wait_and_says_which_failure_it_was(app):
    clock = SimpleNamespace(now=100.0)
    a = app(monotonic=lambda: clock.now)
    a.world.telegram_status["sendMessage"] = 403
    status, data = a.post("test-message")
    assert status == 200 and data["ok"] is False and data["status"] == "http_403" and data["message"].startswith("NOT SENT (http_403): ")
    a.world.telegram_status.clear()
    status, data = a.post("test-message")  # straight away: nothing went out, so nothing makes it wait
    assert status == 200 and data["status"] == "sent"


def test_a_test_message_with_unapplied_settings_says_so_in_its_own_words(app):
    a = app(env_values=INITIAL, run_once=lambda application: (time.sleep(0.5), {})[1], join_timeout=30.0)
    a.post("save", {"values": {"GITHUB_REPOS": "example-org/one"}})
    status, data = a.post("test-message")
    assert status == 409 and data["status"] == "unapplied" and data["message"].startswith("NOT SENT (unapplied): ") and a.world.sent() == []


def test_two_test_messages_at_once_are_told_apart(app, monkeypatch):
    a = app()
    inside, release = threading.Event(), threading.Event()
    real = setup_checks.send_test

    def slow(settings, client):
        inside.set()
        release.wait(5)
        return real(settings, client)

    monkeypatch.setattr(setup_checks, "send_test", slow)
    first = threading.Thread(target=lambda: a.post("test-message"))
    first.start()
    assert inside.wait(3)
    status, data = a.post("test-message")
    assert status == 409 and data["status"] == "in_progress" and data["message"].startswith("NOT SENT (in_progress): ")
    release.set()
    first.join()
    assert len(a.world.sent()) == 1


def test_every_test_message_outcome_has_a_different_message(app):
    seen = {}
    clock = SimpleNamespace(now=100.0)
    a = app(monotonic=lambda: clock.now)
    seen["sent"] = a.post("test-message")[1]["message"].split("(")[0]
    clock.now += 0.1
    seen["too_soon"] = a.post("test-message")[1]["message"]
    for status in (400, 401, 403, 404):
        clock.now += 10
        a.world.telegram_status["sendMessage"] = status
        seen[f"http_{status}"] = a.post("test-message")[1]["message"]
    assert len(set(seen.values())) == len(seen) == 6


# ---------------------------------------------------------------------------------------------------------- the connection test
def test_test_connection_reports_each_check_and_marks_haiku_and_the_write_token_not_tested(app):
    a = app()
    status, data = a.post("test-connection")
    assert status == 200 and data["ok"] is True
    assert data["github"] == [{"ok": True, "message": "OK", "name": REPO}] and data["calendar"]["ok"] is True and data["telegram"]["ok"] is True
    assert data["haiku"]["tested"] is False and data["writeToken"]["tested"] is False
    assert a.world.methods() == ["getMe"] and a.world.sent() == [] and a.world.github_writes == [] and a.world.decider_requests == []
    assert not [s for s in SECRETS if s in a.responses[-1]]


def test_test_connection_says_what_is_wrong_without_secrets(app):
    a = app()
    a.world.github_status[f"/repos/{REPO}"] = 401
    a.world.telegram_status["getMe"] = 401
    a.world.calendar_status = 500
    data = a.post("test-connection")[1]
    assert data["github"][0]["message"] == "HTTP 401 (token rejected)" and data["telegram"]["message"] == "HTTP 401 (token rejected)"
    assert data["calendar"]["message"] == "calendar fetch failed: HTTP 500"
    assert not [s for s in SECRETS if s in a.responses[-1]]


def test_only_one_connection_test_runs_at_a_time(app, monkeypatch):
    a = app()
    inside, release = threading.Event(), threading.Event()

    def slow(settings, client):
        inside.set()
        release.wait(5)
        return {"github": [], "calendar": {}, "telegram": {}, "haiku": {}, "writeToken": {}}

    monkeypatch.setattr(setup_checks, "run_checks", slow)
    first = threading.Thread(target=lambda: a.post("test-connection"))
    first.start()
    assert inside.wait(3)
    status, data = a.post("test-connection")
    assert status == 409 and data["status"] == "busy"
    release.set()
    first.join()


# ---------------------------------------------------------------------------------------------------------- the last line of defence
def test_a_response_that_contains_a_secret_is_replaced_by_a_generic_error(app, monkeypatch):
    a = app()
    monkeypatch.setattr(a.service, "form", lambda body=None: (200, {"oops": f"the token is {BOT}"}))
    status, headers, text = a.live.request("GET", "/api/setup-form")
    assert status == 500 and json.loads(text) == {"error": "server error"} and BOT not in text
    assert a.live.server.last_error == "SecretInResponse"


@pytest.mark.parametrize("secret", [BOT, READ_TOKEN, KEY, WRITE, ICAL_URL, str(CHAT)])
def test_every_kind_of_secret_trips_the_wire(app, monkeypatch, secret):
    a = app()
    monkeypatch.setattr(a.service, "test_connection", lambda body: (200, {"ok": True, "note": f"x {secret} y"}))
    status, _, text = a.live.request("POST", "/api/setup/test-connection", body={})
    assert status == 500 and secret not in text


def test_a_value_the_user_just_typed_cannot_come_back_either(app, monkeypatch):
    a = app()
    typed = "fake-anthropic-" + "key-just-typed-0009"
    monkeypatch.setattr(a.service, "save", lambda body: (200, {"ok": True, "message": f"saved {typed}"}))
    status, _, text = a.live.request("POST", "/api/setup/save", body={"values": {"ANTHROPIC_API_KEY": typed}})
    assert status == 500 and typed not in text
    status, _, text = a.live.request("POST", "/api/setup/save", body={"values": {"GITHUB_REPOS": typed}})
    assert status == 200, "only keys count: a repository list may be echoed back"


def test_a_chat_id_on_offer_cannot_leak_through_any_response(app, monkeypatch):
    a = app(env_values={"TELEGRAM_BOT_TOKEN": BOT, "GITHUB_REPOS": REPO})
    a.world.message(999000111, name="Ada")
    a.post("find-chat")
    monkeypatch.setattr(a.service, "form", lambda body=None: (200, {"id": 999000111}))
    assert a.live.request("GET", "/api/setup-form")[0] == 500


def test_no_response_of_a_whole_session_ever_contains_a_secret(app):
    a = app(env_values=None)
    a.post("save", {"values": {**INITIAL, "TELEGRAM_CHAT_ID": ""}})
    a.world.message(999000111, name="Ada")
    a.post("find-chat")
    a.post("use-chat", {"index": 0})
    a.post("test-message")
    a.post("test-connection")
    a.post("clear", {"name": "GITHUB_ACTIONS_WRITE_TOKEN"})
    a.post("save", {"values": {"GITHUB_TOKEN": "bad token"}})
    a.post("apply")
    pages = [a.live.request("GET", p)[2] for p in ALL_GET]
    for text in a.responses + pages:
        assert not [s for s in SECRETS if s in text], text[:200]
    assert len(a.responses) >= 8 and a.live.server.last_error is None, "no response was swapped for a generic error"


def test_nothing_is_logged_or_printed_by_the_setup_routes(app, capsys, caplog):
    a = app(env_values=None)
    a.post("save", {"values": INITIAL})
    a.post("test-connection")
    a.post("find-chat")
    out = capsys.readouterr()
    assert not [s for s in SECRETS if s in out.out + out.err + caplog.text]


# ---------------------------------------------------------------------------------------------------------- run_ui wires it all together
def test_run_ui_serves_the_setup_page_and_a_save_restarts_the_worker(tmp_path, monkeypatch):
    """The real run_ui, with a temp settings file and the fake world: the page is served, a save writes the file privately, and the
    dashboard's runner is replaced by one built from the new settings."""
    from headsup import __main__ as cli
    env = tmp_path / ".env"
    captured, lines = [], []
    real_make_server = cli.make_server
    monkeypatch.setattr(cli, "make_server", lambda host, port, ctx: captured.append(real_make_server(host, port, ctx)) or captured[-1])
    world = World()
    settings = config.load_settings(env)  # no file yet: empty settings, the first-run state
    thread = threading.Thread(target=cli.run_ui, args=(settings,), daemon=True,
                              kwargs=dict(out=lines.append, port=0, client=world.client, db_path=tmp_path / "t.db", install_signals=False, env_path=env))
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not captured and time.monotonic() < deadline:
            time.sleep(0.01)
        assert captured, lines
        server = captured[0]
        port = server.server_address[1]
        first_runner = server.ctx.runner
        assert first_runner.telegram_ready is False

        def call(method, path, body=None):
            headers = {"Host": f"127.0.0.1:{port}"}
            if method == "POST":
                headers.update({"Origin": f"http://127.0.0.1:{port}", "X-CSRF-Token": server.ctx.token, "Content-Type": "application/json"})
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read().decode()

        status, page = call("GET", "/setup")
        assert status == 200 and "Heads-Up setup" in page
        status, text = call("GET", "/")
        assert "Setup needed: Telegram bot token, Telegram chat ID, A GitHub repository or a calendar address" in text
        status, text = call("POST", "/api/setup/save", {"values": {"TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": str(CHAT), "GITHUB_REPOS": REPO}})
        data = json.loads(text)
        assert status == 200 and data["applied"] is True and BOT not in text
        assert stat.S_IMODE(os.stat(env).st_mode) == 0o600 and dotenv_values(env)["GITHUB_REPOS"] == REPO
        assert server.ctx.runner is not first_runner and server.ctx.runner.telegram_ready is True, "the dashboard now uses a runner built from the new settings"
        assert "Setup needed" not in call("GET", "/")[1]
    finally:
        for s in captured:
            s.shutdown()
        thread.join(timeout=20)
    assert not thread.is_alive()
    assert not [s for s in SECRETS if s in "\n".join(lines)], "nothing printed contains a secret"
