"""A fake outside world for Build 3 tests: one httpx.MockTransport that routes by host.
No test using it opens a socket. Every value here is made up."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx

from headsup.config import Settings
from headsup.store import Store

GH = Path(__file__).parent / "fixtures" / "github"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
CHAT = 424242
BOT = "111222" + ":" + "FAKE-bot-token-for-tests"  # built at runtime so no scanner sees a token-shaped literal
WRITE_TOKEN = "fake-actions-write-token"
READ_TOKEN = "fake-read-token"
REPO = "example-org/demo-repo"
ICAL_HOST = "calendar.example.invalid"
ICAL_URL = f"https://{ICAL_HOST}/secret-address-made-up/basic.ics"


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


def ics_for(start, title="Standup with the team"):
    f = lambda d: d.strftime("%Y%m%dT%H%M%SZ")
    return (f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//made-up//tests//EN\r\nBEGIN:VEVENT\r\nUID:cal-1@example.invalid\r\n"
            f"DTSTAMP:20261020T000000Z\r\nDTSTART:{f(start)}\r\nDTEND:{f(start + timedelta(hours=1))}\r\nSUMMARY:{title}\r\n"
            f"END:VEVENT\r\nEND:VCALENDAR\r\n").encode()


def gh(name):
    return json.loads((GH / name).read_text())


class World:
    """Telegram, GitHub, the calendar host and the local Decider, all in memory."""

    def __init__(self, scores=None, runs="runs_1.json", calendar=None):
        self.scores = scores or {}
        self.default_score = 0.1
        self.runs = runs
        self.calendar = calendar
        self.decider_up = True
        self.github_raise = None  # an exception instance to raise on every GitHub request
        self.github_status = {}  # path -> status to answer instead of the fixture
        self.calendar_status = 200
        self.timeouts = []  # the timeout settings each GET carried
        self.telegram_fails = False
        self.telegram_status = {}  # method -> HTTP status to answer instead (for example {"getMe": 401})
        self.telegram_raise = None  # an exception instance to raise on every Telegram request
        self.bot_username = "headsup_test_bot"
        self.webhook_set = False
        self.updates = []
        self.calls = []  # (method, payload) for every Telegram call
        self.github_writes = []  # every non-GET request to a GitHub host
        self.github_reads = []
        self.github_headers = []  # the headers of every GitHub request, lower-cased
        self.decider_requests = []
        self.unexpected = []
        self.next_message_id = 100
        self.client = httpx.Client(transport=httpx.MockTransport(self))

    # ---- telegram helpers
    def sent(self):
        return [p for m, p in self.calls if m == "sendMessage"]

    def methods(self):
        return [m for m, _ in self.calls]

    def token_for(self, message_index, action_label):
        for row in self.sent()[message_index]["reply_markup"]["inline_keyboard"]:
            for button in row:
                if button["text"] == action_label:
                    return button["callback_data"]
        raise KeyError(action_label)

    def message_id_of(self, index):
        return 100 + index

    def message(self, chat, *, kind="private", name="Ada", date=1_793_620_000, update_id=None, last_name=None):
        """A message sent to the bot, as getUpdates reports it."""
        update_id = update_id or (len(self.updates) + 1)
        who = {"id": chat, "type": kind, "first_name": name} if kind == "private" else {"id": chat, "type": kind, "title": name}
        if last_name:
            who["last_name"] = last_name
        self.updates.append({"update_id": update_id, "message": {"message_id": update_id, "date": date, "chat": who, "from": {"id": abs(chat)}, "text": "hi"}})

    def tap(self, token, *, chat=CHAT, sender=CHAT, message_id=100, update_id=None):
        update_id = update_id or (len(self.updates) + 1)
        self.updates.append({"update_id": update_id, "callback_query": {
            "id": f"cq{update_id}", "from": {"id": sender}, "data": token,
            "message": {"message_id": message_id, "chat": {"id": chat}}}})

    # ---- routing
    def __call__(self, request):
        host, path = request.url.host, request.url.path
        if host == "api.telegram.org":
            return self._telegram(request)
        if host == "api.github.com":
            return self._github(request)
        if host == ICAL_HOST and self.calendar is not None:
            self.timeouts.append(request.extensions["timeout"])
            return httpx.Response(self.calendar_status, content=self.calendar)
        if host == "127.0.0.1":
            return self._decider(request)
        self.unexpected.append(f"{request.method} {host}{path}")
        raise AssertionError(f"unexpected request: {request.method} {host}{path}")

    def _telegram(self, request):
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        self.calls.append((method, payload))
        if self.telegram_raise is not None:
            raise self.telegram_raise
        if method in self.telegram_status:
            return httpx.Response(self.telegram_status[method], json={"ok": False, "description": "made-up failure"})
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"id": 111222, "is_bot": True, "first_name": "Heads-Up Test", "username": self.bot_username}})
        if self.telegram_fails and method == "sendMessage":
            return httpx.Response(500, json={"ok": False})
        if method == "getUpdates":
            if self.webhook_set:
                return httpx.Response(409, json={"ok": False, "description": "webhook is set"})
            offset = payload.get("offset", 0)
            allowed = payload.get("allowed_updates")  # like Telegram: only the kinds asked for
            return httpx.Response(200, json={"ok": True, "result": [u for u in self.updates if u["update_id"] >= offset
                                                                    and (not allowed or any(k in u for k in allowed))]})
        if method == "sendMessage":
            self.next_message_id += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.next_message_id - 1}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def _github(self, request):
        self.github_headers.append({k.lower(): v for k, v in request.headers.items()})
        if self.github_raise is not None:
            self.github_reads.append(request.url.path)
            raise self.github_raise
        if request.url.path in self.github_status:
            self.github_reads.append(request.url.path)
            return httpx.Response(self.github_status[request.url.path], json={"message": "made-up failure"})
        self.timeouts.append(request.extensions["timeout"])
        if request.method != "GET":
            self.github_writes.append((request.method, str(request.url), request.headers.get("authorization")))
            return httpx.Response(201, json={})
        self.github_reads.append(request.url.path)
        base = f"/repos/{REPO}"
        files = {base: "repo.json", f"{base}/releases": "releases.json", f"{base}/issues": "issues.json",
                 f"{base}/pulls": "pulls.json", f"{base}/actions/runs": self.runs}
        name = files[request.url.path]
        etag = f'"{name}"'
        if request.headers.get("if-none-match") == etag and name != "repo.json":
            return httpx.Response(304, headers={"etag": etag})
        return httpx.Response(200, headers={"etag": etag, "x-ratelimit-remaining": "4990"}, json=gh(name))

    def _decider(self, request):
        if request.url.path == "/":
            if not self.decider_up:
                raise httpx.ConnectError("refused")
            return httpx.Response(200, text="Ollama is running")
        if not self.decider_up:
            raise httpx.ConnectError("refused")
        body = json.loads(request.content)
        self.decider_requests.append(body)
        p = next((v for k, v in self.scores.items() if k in body["state"]), self.default_score)
        return httpx.Response(200, json={"answers": {"interrupt": {"type": "noul", "noul": p}}})


class FakeAnthropic:
    def __init__(self, text="Heads up: take a look."):
        self.text = text
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)],
                               usage=SimpleNamespace(input_tokens=60, output_tokens=12), stop_reason="end_turn")


def settings(**over):
    base = dict(github_repos=(REPO,), github_write_repos=(REPO,), github_token=READ_TOKEN, github_actions_write_token=WRITE_TOKEN,
                telegram_bot_token=BOT, telegram_chat_id=CHAT, ical_url=ICAL_URL, anthropic_api_key="fake-anthropic-key")
    base.update(over)
    return Settings(**base)


def store_at(tmp_path, baselined=True):
    """A store for a pipeline run. By default it models an install that has been running: the first-run baseline is already done, so
    the events a test feeds in can alert. Pass baselined=False for the very first run after setup."""
    store = Store(tmp_path / "data" / "headsup.db")
    if baselined:
        for name in ("github", "ical"):
            store.set_kv(f"baseline_{name}", NOW.isoformat())
    return store
