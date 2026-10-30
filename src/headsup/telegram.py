"""Telegram Bot API client: sendMessage (plain text + inline keyboard), getUpdates long polling,
answerCallbackQuery, editMessageReplyMarkup.

Safety rules (PLAN.md section 8):
- messages go only to the configured chat id (send() has no chat argument);
- plain text only: no parse_mode, so event text is never interpreted as Markdown or HTML;
- text is cut to 4096 characters; at most one sendMessage per second;
- long polling with the offset kept in SQLite (no webhooks).
The bot token is part of every request URL, so errors carry only the method name and an error
class, never the URL.
"""

import re
import time
from datetime import datetime, timezone

import httpx

API = "https://api.telegram.org"
TEXT_MAX = 4096
CALLBACK_MAX_BYTES = 64
ANSWER_MAX = 200
MIN_SEND_GAP_S = 1.0
OFFSET_KEY = "telegram_offset"


class TelegramError(Exception):
    def __init__(self, method, kind):
        super().__init__(f"telegram {method} failed: {kind}")
        self.method = method
        self.kind = kind


class WebhookConflict(TelegramError):
    """getUpdates refuses to run while a webhook is set. We never delete it for you."""


class Telegram:
    def __init__(self, client, bot_token, chat_id, store, clock=time.monotonic, sleep=time.sleep):
        self.client = client
        self._token = bot_token
        self.chat_id = chat_id
        self.store = store
        self._clock = clock
        self._sleep = sleep
        self._last_send = None

    def __repr__(self):
        return "Telegram(chat_id=<hidden>, token=<hidden>)"

    def _call(self, method, payload, timeout=30.0):
        url = f"{API}/bot{self._token}/{method}"
        try:
            resp = self.client.post(url, json=payload, timeout=timeout)
        except httpx.HTTPError as exc:
            raise TelegramError(method, type(exc).__name__) from None
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code == 409 and method == "getUpdates":
            raise WebhookConflict(method, "webhook_is_set")
        if resp.status_code == 429:
            raise TelegramError(method, "rate_limited")
        if resp.status_code != 200 or not data.get("ok"):
            raise TelegramError(method, f"HTTP {resp.status_code}")
        return data.get("result")

    def send(self, text, buttons=()):
        """Send plain text to the configured chat. buttons: [(label, callback_data)]. Returns the message_id."""
        if not text:
            raise ValueError("message text is empty")
        payload = {"chat_id": self.chat_id, "text": text[:TEXT_MAX]}
        if buttons:
            for _, data in buttons:
                if not 1 <= len(data.encode()) <= CALLBACK_MAX_BYTES:
                    raise ValueError("callback_data must be 1-64 bytes")
            payload["reply_markup"] = {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in buttons]]}
        if self._last_send is not None:
            wait = MIN_SEND_GAP_S - (self._clock() - self._last_send)
            if wait > 0:
                self._sleep(wait)
        self._last_send = self._clock()
        return self._call("sendMessage", payload)["message_id"]

    def get_updates(self, timeout=30):
        """Long-poll for button taps (and for messages, whose sender the setup page may offer: see chat_from_update). The caller
        confirms each handled update with confirm(); an offset forgets every earlier update of any kind, so the caller must look at
        a message before it confirms a later tap."""
        payload = {"timeout": timeout, "allowed_updates": ["callback_query", "message"]}
        offset = self.store.get_kv(OFFSET_KEY)
        if offset is not None:
            payload["offset"] = int(offset)
        return self._call("getUpdates", payload, timeout=timeout + 10.0) or []

    def get_me(self):
        """The bot's own record: proves the token works (used by the setup page's connection test)."""
        return self._call("getMe", {}, timeout=15.0) or {}

    def recent_chats(self):
        """Who has messaged the bot lately, for the setup page's "Find my chat ID". It READS without confirming: no offset is sent
        and none is stored, so it can never consume a button tap or move the polling position. Only `message` updates are asked for.
        Returns ({id: {"id", "name", "last"}} newest first as a list, number of non-private chats seen). Buttons only work in a
        private chat (the sender must be the chat), so groups and channels are counted but not offered."""
        updates = self._call("getUpdates", {"timeout": 0, "allowed_updates": ["message"]}, timeout=15.0) or []
        private, others = {}, 0
        for update in updates:
            message = update.get("message") if isinstance(update, dict) else None
            chat = message.get("chat") if isinstance(message, dict) else None
            if not isinstance(chat, dict) or isinstance(chat.get("id"), bool) or not isinstance(chat.get("id"), int):
                continue
            if chat.get("type") != "private" or chat["id"] <= 0:
                others += 1
                continue
            date = message.get("date")
            last = datetime.fromtimestamp(date, tz=timezone.utc) if isinstance(date, int) and not isinstance(date, bool) else None
            known = private.get(chat["id"])
            if known is None or (last is not None and (known["last"] is None or last > known["last"])):
                private[chat["id"]] = {"id": chat["id"], "name": _display_name(chat), "last": last}
        ordered = sorted(private.values(), key=lambda c: (c["last"] is not None, c["last"]), reverse=True)
        return ordered, others

    def confirm(self, update_id):
        self.store.set_kv(OFFSET_KEY, update_id + 1)

    def answer_callback(self, callback_query_id, text=""):
        return self._call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text[:ANSWER_MAX]})

    def remove_keyboard(self, message_id):
        return self._call("editMessageReplyMarkup", {"chat_id": self.chat_id, "message_id": message_id,
                                                      "reply_markup": {"inline_keyboard": []}})


def chat_from_update(update):
    """(chat id, first name, sent_at) for a `message` update from a PRIVATE chat, else None. Nothing else of the message is read:
    not its text, not a username, not a surname."""
    message = update.get("message") if isinstance(update, dict) else None
    chat = message.get("chat") if isinstance(message, dict) else None
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return None
    chat_id = chat.get("id")
    if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id <= 0:
        return None
    first = chat.get("first_name")
    name = re.sub(r"[\x00-\x1f\x7f]", "", first).strip()[:60] if isinstance(first, str) else ""
    date = message.get("date")
    sent = datetime.fromtimestamp(date, tz=timezone.utc) if isinstance(date, int) and not isinstance(date, bool) and date > 0 else None
    return chat_id, name or "(no name)", sent


def _display_name(chat):
    """A short, printable name for a chat: first and last name, else the username. Control characters are dropped."""
    name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if isinstance(x, str) and x.strip())
    if not name and isinstance(chat.get("username"), str):
        name = "@" + chat["username"]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:60] or "(no name)"
