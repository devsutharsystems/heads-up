"""Connection tests and the Telegram helper behind the /setup page.

Nothing here reads or writes the settings file: every function takes a Settings object and an httpx client. Every message is
built from an HTTP status or an error class, never from a response body, a URL, a token or the calendar address. Haiku and the
re-run token are reported as "not tested" on purpose: a test call to Haiku would spend money, and the write token can only be
tested by re-running a build.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx

from headsup.netutil import TIMEOUT
from headsup.sources.github import API as GITHUB_API
from headsup.sources.ical import IcalError, IcalSource
from headsup.store import Store
from headsup.telegram import Telegram, TelegramError, WebhookConflict

TEST_TEXT = "Heads-Up test message. If you can read this, alerts will arrive in this chat."
_USERNAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_GITHUB_WHY = {401: "token rejected", 403: "no access, or rate limited", 404: "repository not found, or the token cannot see it"}
_TELEGRAM_WHY = {"HTTP 401": "token rejected", "HTTP 404": "token not recognised", "rate_limited": "rate limited, try again in a minute"}


def _result(ok, message, **extra):
    return {"ok": ok, "message": message, **extra}


def check_github(settings, client, deadline=None, clock=time.monotonic):
    """One result per watched repository: GET /repos/{repo} with the saved read token. Repositories left when `deadline` (a
    clock() value) has passed are reported as skipped instead of being asked: 50 slow repositories must not run for minutes."""
    if not settings.github_repos:
        return [_result(False, "no repositories set", name="GitHub")]
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "heads-up"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    results = []
    for repo in settings.github_repos:
        if deadline is not None and clock() > deadline:
            results.append(_result(False, "skipped: the test ran out of time", name=repo))
            continue
        url = f"{GITHUB_API}/repos/{repo}"
        try:
            status = client.get(url, headers=headers, timeout=TIMEOUT).status_code  # one attempt: a test should report, not retry
        except httpx.HTTPError:
            results.append(_result(False, "network error", name=repo))
            continue
        if status == 200:
            results.append(_result(True, "OK", name=repo))
        else:
            why = _GITHUB_WHY.get(status)
            results.append(_result(False, f"HTTP {status} ({why})" if why else f"HTTP {status}", name=repo))
    return results


def check_calendar(settings, client, now=None):
    """Fetch and parse the feed exactly as a check would, into a throw-away in-memory store."""
    if not settings.ical_url:
        return _result(False, "no calendar address set", configured=False)
    store = Store(":memory:")
    try:
        IcalSource(settings.ical_url, client, store, now=now or (lambda: datetime.now(timezone.utc)),
                   lead_minutes=settings.reminder_lead_minutes).poll()
    except IcalError as exc:
        return _result(False, str(exc), configured=True)  # IcalError text never contains the address
    except httpx.HTTPError:
        return _result(False, "network error", configured=True)
    except Exception:  # noqa: BLE001 - the parser's own errors: say what it means, never its text
        return _result(False, "the address did not return a calendar", configured=True)
    finally:
        store.close()
    return _result(True, "OK, calendar readable", configured=True)


def _telegram_message(exc):
    if isinstance(exc, WebhookConflict):
        return ("Telegram refused (HTTP 409): a webhook is set for this bot, or another program is polling it. "
                "Remove the webhook yourself; Heads-Up will not do it for you.")
    kind = exc.kind
    if kind.startswith("HTTP "):
        why = _TELEGRAM_WHY.get(kind)
        return f"{kind} ({why})" if why else kind
    return _TELEGRAM_WHY.get(kind, "network error")


def check_telegram(settings, client):
    """getMe with the saved token: proves the token. The chat ID is proven by the test message."""
    if not settings.telegram_bot_token:
        return _result(False, "no bot token set", configured=False)
    try:
        me = Telegram(client, settings.telegram_bot_token, settings.telegram_chat_id, None).get_me()
    except TelegramError as exc:
        return _result(False, _telegram_message(exc), configured=True)
    username = me.get("username") if isinstance(me, dict) else None
    bot = f"OK, bot @{username}" if isinstance(username, str) and _USERNAME.match(username) else "OK"
    return _result(True, bot if settings.telegram_chat_id is not None else f"{bot}; the chat ID is not set yet", configured=True)


def not_tested(settings):
    return {
        "haiku": {"tested": False, "message": "not tested: a test call would use your Anthropic credit" if settings.anthropic_api_key
                  else "no key set: alerts use the template text"},
        "writeToken": {"tested": False, "message": "not tested: it can only be tested by re-running a build"
                       if settings.github_actions_write_token else "no write token set"},
    }


def _timed(check, *args):
    started = time.monotonic()
    return check(*args), int((time.monotonic() - started) * 1000)


def run_checks(settings, client, now=None, budget_s=45.0, clock=time.monotonic):
    """The three checks on the saved settings, side by side (so the slowest one decides the wait, not their sum), each timed in
    milliseconds. Reads and sends nothing but the three test requests above."""
    jobs = {"github": (check_github, settings, client, clock() + budget_s, clock), "calendar": (check_calendar, settings, client, now),
            "telegram": (check_telegram, settings, client)}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(_timed, *job) for name, job in jobs.items()}
        done = {name: future.result() for name, future in futures.items()}
    return {**{name: done[name][0] for name in jobs}, "timing_ms": {name: done[name][1] for name in jobs}, **not_tested(settings)}


def find_chats(settings, client):
    """The private chats that have messaged the bot. Returns {"status", "message", "chats": [{"id","name","last"}], "ignored"}.
    The ids are for the server to keep: the page only ever receives an index and a name."""
    out = {"status": "ok", "message": "", "chats": [], "ignored": 0}
    if not settings.telegram_bot_token:
        return {**out, "status": "no_token", "message": "Save the bot token first."}
    try:
        chats, ignored = Telegram(client, settings.telegram_bot_token, settings.telegram_chat_id, None).recent_chats()
    except TelegramError as exc:
        status = "webhook" if isinstance(exc, WebhookConflict) else "error"
        return {**out, "status": status, "message": _telegram_message(exc)}
    out.update(chats=chats, ignored=ignored)
    if not chats:
        out.update(status="none", message="No messages found. Open your bot in Telegram, press Start or send it any message, then try "
                   "again. Telegram keeps messages for at most 24 hours, and forgets them once a button is tapped after them: if you "
                   "have, send it \"hi\" again.")
    return out


def stored_chats(open_store, now=None):
    """The private chats the running worker has seen write to the bot in the last ten minutes (it keeps their id, first name and
    time, nothing else). Used while the worker is polling, because then it, not the finder, is the one that sees the messages."""
    now = (now or (lambda: datetime.now(timezone.utc)))()
    store = open_store()
    try:
        rows = store.recent_chats(now)
    finally:
        store.close()
    out = {"status": "ok", "message": "", "chats": [{"id": i, "name": n, "last": t} for i, n, t in rows], "ignored": 0}
    if not rows:
        out.update(status="none", message="No message from the last 10 minutes. Open your bot in Telegram, send it any message (for "
                   "example hi), then press Find my chat ID again within 10 minutes.")
    return out


def _not_sent(code, text):
    return _result(False, f"NOT SENT ({code}): {text}", status=code)


_SEND_WHY = {
    "HTTP 400": ("http_400", "Telegram answered HTTP 400: it does not know this chat. Check the chat ID, and that you have started a chat with the bot."),
    "HTTP 401": ("http_401", "Telegram answered HTTP 401: the bot token was rejected."),
    "HTTP 403": ("http_403", "Telegram answered HTTP 403: the bot cannot write to this chat. Open the bot in Telegram and press Start. Also check the chat ID is yours, not your bot's own ID (the digits before the colon in the bot token): use Find my chat ID."),
    "HTTP 404": ("http_404", "Telegram answered HTTP 404: the bot token is not recognised."),
    "rate_limited": ("rate_limited", "Telegram says there were too many requests. Try again in a minute."),
    "ReadTimeout": ("timeout", "Telegram did not answer in time. The message may still arrive: look in the chat before trying again."),
}


def send_test(settings, client):
    """One fixed message to the saved chat. Every outcome has its own `status` and its own text, so the page shows which path ran."""
    if not settings.telegram_bot_token or settings.telegram_chat_id is None:
        return _not_sent("not_configured", "Save the bot token and the chat ID first.")
    try:
        message_id = Telegram(client, settings.telegram_bot_token, settings.telegram_chat_id, None).send(TEST_TEXT)
    except TelegramError as exc:
        known = _SEND_WHY.get(exc.kind)
        if known:
            return _not_sent(*known)
        if exc.kind.startswith("HTTP "):
            return _not_sent("http_" + exc.kind[5:], f"Telegram answered {exc.kind}.")
        return _not_sent("network", "Could not reach Telegram (network error).")
    return _result(True, f"SENT: Telegram accepted the test message (message number {message_id}). Look in your chat with the bot.", status="sent")
