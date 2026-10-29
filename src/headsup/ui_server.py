"""The dashboard's HTTP server: standard library only, 127.0.0.1 only.

Security: the bind address must be 127.0.0.1; every request needs a Host of 127.0.0.1:<port> or localhost:<port>
(DNS rebinding), on GET and POST alike; every POST needs the per-process token in X-CSRF-Token, an Origin of the same two
forms, a JSON content type and a Content-Length that is a plain number within the route's limit (4 KB; 16 KB for saving
settings). Responses are built from whitelisted fields: no token, key or URL from .env is ever put in one, and pipeline
text goes through the Runner's redactor. The setup routes add a last line of defence: a response that contains a known
secret is replaced by a generic error. There is no route for the Decider, the threshold, the daily limit, or any GitHub
action (Re-run CI stays Telegram-only). Any program running as the same user on this Mac can still reach the port; the
token and Origin checks stop other websites, not local programs.
"""

import hmac
import json
import re
import secrets
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from headsup.actions import TOKEN_TTL
from headsup.localtime import LOCAL_TZ, day_bounds
from headsup.policy import DAILY_LIMIT
from headsup.ui_page import pause_button, render_fragment, render_page
from headsup.ui_setup_page import render_setup_page

HOST = "127.0.0.1"
PORT = 8765
MAX_BODY = 4096
GET_JSON = ("status", "setup", "today", "events", "alerts")
POST_ROUTES = ("check", "pause", "resume", "interval", "preview")
SETUP_ROUTES = ("save", "clear", "apply", "find-chat", "use-chat", "test-message", "test-connection")  # /api/setup/<name>
SETUP_BODY_LIMITS = {"save": 16 * 1024}  # every other setup route uses MAX_BODY
_SETUP_METHODS = {"save": "save", "clear": "clear", "apply": "apply", "find-chat": "find_chat", "use-chat": "use_chat",
                  "test-message": "send_test", "test-connection": "test_connection"}
_LENGTH = re.compile(r"[0-9]{1,9}")  # a plain number: no sign, no underscores, no spaces


def page_csp(nonce):
    return (f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def fmt_time(iso):
    """A stored UTC ISO time as local (Asia/Kolkata) 'DD Mon HH:MM'."""
    if not iso:
        return ""
    return datetime.fromisoformat(iso).astimezone(LOCAL_TZ).strftime("%d %b %H:%M")


def alert_state(row, now):
    """pending (not sent yet), tapped, expired, or 'sent, buttons active'."""
    if row["status"] != "sent":
        return "pending"
    if row["taps"]:
        return "tapped: " + ", ".join(row["taps"])
    if row["keyboards_removed"] or (row["buttons_at"] and now - datetime.fromisoformat(row["buttons_at"]) > TOKEN_TTL):
        return "expired"
    return "sent, buttons active"


class Context:
    """What the handlers need: the runner, a store factory, the setup status function, a health function."""

    def __init__(self, *, runner, open_store, setup_status, health, now=None, setup_service=None):
        self.runner, self.open_store, self.setup_status, self.health = runner, open_store, setup_status, health
        self.setup_service = setup_service  # the /setup routes exist only when this is given
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.token = secrets.token_urlsafe(32)

    def snapshot(self):
        now = self.now()
        store = self.open_store()  # one connection per request thread, closed at the end
        try:
            start, end = day_bounds(now)
            alerts = store.alert_rows(20)
            for a in alerts:
                a["state"] = alert_state(a, now)
                del a["event_hash"], a["event_id"], a["buttons_at"], a["keyboards_removed"], a["taps"]
            costs = store.costs_between(start, end)
            today = {"alerts_today": store.interrupts_between(start, end), "limit": DAILY_LIMIT,
                     "spend_today": sum(c for _, c in costs), "spend_total": store.cost_total()}
            events = store.recent_decisions(50)
        finally:
            store.close()
        missing = self.setup_service.required_missing() if self.setup_service else []
        return {"status": self.runner.status(), "health": self.health(), "setup": self.setup_status(), "today": today,
                "events": events, "alerts": alerts, "fmt": fmt_time, "now": now, "calls_today": len(costs),  # the last two are for the page only
                "setup_missing": missing}


class Handler(BaseHTTPRequestHandler):
    server_version = "HeadsUp"
    protocol_version = "HTTP/1.0"  # one request per connection: an early refusal never leaves an unread body behind
    timeout = 30  # a client that stops sending frees its thread after 30 s instead of holding it for ever

    def log_message(self, *args):  # no request logging
        pass

    # ------------------------------------------------------------ helpers
    def _allowed(self):
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   extra={"Content-Security-Policy": "default-src 'none'"})

    def _host_ok(self):
        if self.headers.get("Host") not in self._allowed():
            self._json(403, {"error": "forbidden host"})
            return False
        return True

    # ---------------------------------------------------------------- GET
    def do_GET(self):  # noqa: N802
        try:
            self._get()
        except Exception as exc:  # noqa: BLE001 - never a traceback or detail in a response
            self.server.last_error = type(exc).__name__
            self._json(500, {"error": "server error"})

    def _get(self):
        if not self._host_ok():
            return
        ctx, path = self.server.ctx, self.path.split("?", 1)[0]
        if path == "/":
            nonce = secrets.token_urlsafe(16)
            self._send(200, render_page(ctx.snapshot(), ctx.token, nonce), "text/html; charset=utf-8",
                       {"Content-Security-Policy": page_csp(nonce)})
        elif path == "/setup" and ctx.setup_service is not None:
            nonce = secrets.token_urlsafe(16)
            self._send(200, render_setup_page(ctx.token, nonce), "text/html; charset=utf-8", {"Content-Security-Policy": page_csp(nonce)})
        elif path == "/api/setup-form" and ctx.setup_service is not None:
            code, obj = ctx.setup_service.form()
            self._setup_reply(code, obj, None)
        elif path == "/fragment":
            snap = ctx.snapshot()
            self._json(200, {"html": render_fragment(snap), "pause_button": pause_button(snap["status"]["paused"])})
        elif path.startswith("/api/") and path[5:] in GET_JSON:
            name = path[5:]
            snap = ctx.snapshot()
            body = {"status": {**snap["status"], "judge": snap["health"]}, "setup": snap["setup"], "today": snap["today"],
                    "events": snap["events"], "alerts": snap["alerts"]}[name]
            self._json(200, body)
        else:
            self._json(404, {"error": "not found"})

    # --------------------------------------------------------------- POST
    def do_POST(self):  # noqa: N802
        try:
            self._post()
        except Exception as exc:  # noqa: BLE001
            self.server.last_error = type(exc).__name__
            self._json(500, {"error": "server error"})

    def _post(self):
        if not self._host_ok():
            return
        ctx, path = self.server.ctx, self.path.split("?", 1)[0]
        origins = {f"http://{h}" for h in self._allowed()}
        if self.headers.get("Origin") not in origins:
            return self._json(403, {"error": "forbidden origin"})
        if not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), ctx.token):
            return self._json(403, {"error": "forbidden token"})
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            return self._json(415, {"error": "JSON only"})
        group, name = self._post_route(path, ctx)  # resolved BEFORE the body is read: an unknown path is a 404, the limit is per route
        if group is None:
            return self._json(404, {"error": "not found"})
        body = self._read_json(SETUP_BODY_LIMITS.get(name, MAX_BODY) if group == "setup" else MAX_BODY)
        if body is None:
            return
        if group == "setup":
            return self._setup_post(name, body, ctx.setup_service)
        self._route(name, body, ctx.runner)

    @staticmethod
    def _post_route(path, ctx):
        if path.startswith("/api/setup/") and ctx.setup_service is not None and path[11:] in SETUP_ROUTES:
            return "setup", path[11:]
        if path.startswith("/api/") and path[5:] in POST_ROUTES:
            return "dashboard", path[5:]
        return None, None

    def _read_json(self, limit):
        """The request body as a dict, or None after sending the refusal. Content-Length must be a plain number (so "-5" cannot make
        the read wait for a stream that never ends) and within `limit`."""
        raw = self.headers.get("Content-Length", "0").strip()
        if not _LENGTH.fullmatch(raw):
            self._json(400, {"error": "bad length"})
            return None
        length = int(raw)
        if length > limit:
            self._json(413, {"error": "body too large"})
            return None
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json(400, {"error": "bad JSON"})
            return None
        if not isinstance(body, dict):
            self._json(400, {"error": "bad request"})
            return None
        return body

    def _setup_post(self, name, body, service):
        code, obj = getattr(service, _SETUP_METHODS[name])(body)
        self._setup_reply(code, obj, body)

    def _setup_reply(self, code, obj, body):
        """Send a setup response, unless it contains a secret: that is a bug, so a generic error goes out instead and only the
        class name is kept."""
        text = json.dumps(obj, ensure_ascii=False)
        if not self.server.ctx.setup_service.is_clean(text, body):
            self.server.last_error = "SecretInResponse"
            return self._json(500, {"error": "server error"})
        self._send(code, text, extra={"Content-Security-Policy": "default-src 'none'"})

    def _route(self, name, body, runner):
        if name == "check":
            result = runner.request_check()
            messages = {"queued": "Check queued.", "busy": "A check is already running.",
                        "disabled": "Telegram is not set up, so checks are off. Use Preview."}
            return self._json(202 if result == "queued" else 409, {"result": result, "message": messages[result]})
        if name == "pause":
            runner.pause()
            return self._json(200, {"paused": True, "message": "Automatic checks paused. Button taps still work."})
        if name == "resume":
            runner.resume()
            return self._json(200, {"paused": False, "message": "Automatic checks resumed."})
        if name == "interval":
            if runner.set_interval(body.get("minutes")):
                return self._json(200, {"interval_minutes": runner.interval, "message": f"Checking every {runner.interval} minutes."})
            return self._json(400, {"message": "Minutes must be a whole number from 1 to 1440."})
        result, lines = runner.preview()  # preview
        if result == "busy":
            return self._json(409, {"message": "A check is running; try again in a moment.", "lines": []})
        self._json(200, {"message": "Dry run finished: nothing was sent or saved.", "lines": lines})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(host, port, ctx):
    """A server bound to 127.0.0.1 only. Any other host raises ValueError before a socket is opened."""
    if host != HOST:
        raise ValueError("the dashboard only binds 127.0.0.1")
    server = Server((host, port), Handler)
    server.ctx = ctx
    server.last_error = None
    return server
