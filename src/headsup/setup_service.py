"""What the /setup routes do. The server only checks, parses and dispatches; everything about the settings file, the restart and
Telegram happens here. Every method takes the parsed JSON body and returns (HTTP status, dict).

Nothing returned from here contains a key, the chat ID or the calendar address: the form view says "set" or "missing", the chat
finder returns an index and a name (the ids stay in `_pending` on the server), and messages are fixed text plus a setting's label.
Saving, clearing, applying and choosing a chat run under one lock from the file write to the worker restart, so two requests can
never interleave. While the worker polls Telegram it is the one that sees messages to the bot (and keeps who wrote for ten minutes), so the chat
finder reads that and asks Telegram nothing. Only when the worker is idle (no chat ID yet) does the finder ask Telegram itself,
without confirming anything, and then nobody else is polling. Neither case stops the worker or takes the settings lock.
"""

import threading
import time

from headsup import config, setup_checks

PENDING_SECONDS = 600  # how long a list of chats offered to the user stays valid
TEST_GAP_SECONDS = 5.0  # at most one test message per this many seconds

BOT_OWN_ID_MESSAGE = "That is your bot's own ID, not your chat. Send your bot a message and use Find my chat ID."
_BAD = (400, {"ok": False, "error": "bad_request", "message": "That request was not understood."})
_LABELS = {f.name: f.label for f in config.FIELDS}
_SECRET_NAMES = {f.name for f in config.FIELDS if f.kind in ("secret", "id")}


def _only(body, allowed):
    return isinstance(body, dict) and set(body) <= set(allowed)


class SetupService:
    def __init__(self, supervisor, client, *, env_path=config.DEFAULT_ENV, monotonic=time.monotonic, open_store=None, now=None):
        self.supervisor, self.client, self.env_path = supervisor, client, env_path
        self._monotonic = monotonic
        self._open_store, self._now = open_store, now
        self._lock = threading.Lock()  # save, clear, apply, choose a chat and the chat finder, end to end
        self._test_lock = threading.Lock()  # one connection test at a time
        self._unapplied = False  # settings are saved but the running app does not use them yet
        self._pending = None  # {"ids", "names", "at"}: the chats offered to the user (the ids never leave the server)
        self._last_test_message = None
        self._send_lock = threading.Lock()  # one test message at a time

    # ------------------------------------------------------------------------------------------ for the server
    def required_missing(self):
        return config.required_missing(self.env_path)

    def is_clean(self, text, body=None):
        """False if `text` contains any secret: the saved ones, what this request just typed, or a chat id on offer.
        The server turns that into a generic error instead of sending it."""
        secrets = list(config.known_secrets(self.env_path))
        values = body.get("values") if isinstance(body, dict) else None
        if isinstance(values, dict):
            secrets += [v for n, v in values.items() if n in _SECRET_NAMES and isinstance(v, str)]
        if self._pending:
            secrets += [str(i) for i in self._pending["ids"]]
        return not any(len(s.strip()) >= 5 and s.strip() in text for s in secrets)

    def _need_applied(self):
        if self._unapplied:
            return 409, {"ok": False, "status": "unapplied", "unapplied": True,
                         "message": "The saved settings are not active yet. Press Apply first."}
        return None

    def _reply(self, code, **fields):
        return code, {"ok": code < 400, **fields, "unapplied": self._unapplied, "form": config.env_form(self.env_path)}

    # ------------------------------------------------------------------------------------------ the form
    def form(self, body=None):
        return 200, {**config.env_form(self.env_path), "unapplied": self._unapplied}

    # ------------------------------------------------------------------------------------------ save, clear, apply
    def save(self, body):
        if not _only(body, ("values", "clear")):
            return _BAD
        values, clear = body.get("values", {}), body.get("clear", [])
        if (not isinstance(values, dict) or not isinstance(clear, list) or len(values) > len(config.FIELDS) or len(clear) > len(config.FIELDS)
                or not all(isinstance(k, str) for k in values) or not all(isinstance(n, str) for n in clear)):
            return _BAD
        with self._lock:
            return self._save_and_apply(values, clear)

    def clear(self, body):
        if not _only(body, ("name",)) or not isinstance(body.get("name"), str):
            return _BAD
        with self._lock:
            return self._save_and_apply({}, [body["name"]])

    def apply(self, body):
        if not _only(body, ()):
            return _BAD
        with self._lock:
            return self._apply([], saved=False, done="Applied. Settings reloaded.")

    def _invalid(self, name, reason=None):
        known = name in _LABELS  # a name from the request is only echoed if it is one of ours
        message = f"{_LABELS[name]} is not valid." if known else "That setting is not valid."
        if known and reason == "bot_own_id":
            message = BOT_OWN_ID_MESSAGE
        return 400, {"ok": False, "error": "invalid", "field": name if known else None, "message": message}

    def _save_and_apply(self, values, clear, extra=None):
        try:
            changed = config.save_env(values, clear, self.env_path)
        except config.SettingError as exc:
            return self._invalid(exc.name, exc.reason)
        except config.EnvFileError:
            return 500, {"ok": False, "error": "file", "message": "The settings file could not be written. Nothing was changed."}
        if not changed:
            return self._reply(200, saved=False, applied=False, changed=[], message="Nothing to save.", **(extra or {}))
        return self._apply(changed, saved=True, done="Saved. Settings reloaded.", extra=extra)

    def _apply(self, changed, *, saved, done, extra=None):
        """Read the file again and restart the worker with the result. Called with the lock held."""
        base = {"saved": saved, "changed": changed, **(extra or {})}
        try:
            settings = config.reload_settings(self.env_path)
        except ValueError as exc:  # a hand-edited line the loader refuses; its message names the setting, never the value
            names = [n for n in config.SETTING_NAMES if n in str(exc)]
            self._unapplied = True
            where = ", ".join(_LABELS[n] for n in names) or "a setting"
            return self._reply(200, applied=False, error="reload", names=names, **base,
                               message=f"Saved, but {where} in the settings file has a problem. The running app keeps the old settings. Fix it and press Apply.")
        result = self.supervisor.restart(settings)
        if result.status == "ok":
            self._unapplied = False
            return self._reply(200, applied=True, message=done, **base)
        self._unapplied = True
        if result.status == "busy":
            return self._reply(200, applied=False, error="busy", **base,
                               message="Saved. A check is still running, so the new settings are not active yet. Press Apply in a minute.")
        return self._reply(200, applied=False, error="restart", detail=result.detail, **base,
                           message=f"Saved, but the app could not start with the new settings ({result.detail}). The old settings stay active. Press Apply to try again.")

    # ------------------------------------------------------------------------------------------ the Telegram helper
    def find_chat(self, body):
        if not _only(body, ()):
            return _BAD
        blocked = self._need_applied()
        if blocked:
            return blocked
        settings = self.supervisor.settings
        if not settings.telegram_bot_token:
            return 200, {"ok": False, "status": "no_token", "message": "Save the bot token first."}
        runner = self.supervisor.runner
        if self._open_store is not None and runner.telegram_ready:
            found = setup_checks.stored_chats(self._open_store, self._now)  # the worker is polling: it keeps who wrote
        else:
            found = setup_checks.find_chats(settings, self.client)  # nobody else polls: ask Telegram, confirm nothing
        if found["status"] != "ok":
            return 200, {"ok": False, "status": found["status"], "message": found["message"]}
        chats = found["chats"]
        self._pending = {"ids": [c["id"] for c in chats], "names": [c["name"] for c in chats], "at": self._monotonic()}  # one assignment: no lock
        note = f" {found['ignored']} group chat(s) were ignored: buttons only work in a private chat." if found["ignored"] else ""
        single = len(chats) == 1
        return 200, {"ok": True, "status": "choose", "single": single, "ignored": found["ignored"],
                     "message": ("Is this you?" if single else "Several people have messaged this bot. Pick yourself.") + note,
                     "choices": [{"index": i, "name": c["name"], "last": c["last"].isoformat() if c["last"] else None}
                                 for i, c in enumerate(chats)]}

    def use_chat(self, body):
        if not _only(body, ("index",)):
            return _BAD
        index = body.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            return _BAD
        with self._lock:
            pending = self._pending
            if pending is None or self._monotonic() - pending["at"] > PENDING_SECONDS:
                self._pending = None
                return 409, {"ok": False, "status": "expired", "message": "That list is out of date. Press Find my chat ID again."}
            if not 0 <= index < len(pending["ids"]):
                return _BAD
            self._pending = None
            return self._save_and_apply({"TELEGRAM_CHAT_ID": str(pending["ids"][index])}, [],
                                        extra={"status": "saved", "name": pending["names"][index]})

    def send_test(self, body):
        """Every outcome answers with its own `status` and a text that starts "SENT" or "NOT SENT (<status>)", so the page shows
        which path ran. The 5 s gap counts from the last message that really went out."""
        if not _only(body, ()):
            return _BAD
        if self._unapplied:
            return 409, {"ok": False, "status": "unapplied", "unapplied": True,
                         "message": "NOT SENT (unapplied): the saved settings are not active yet. Press Apply first."}
        if not self._send_lock.acquire(blocking=False):
            return 409, {"ok": False, "status": "in_progress", "message": "NOT SENT (in_progress): another test message is still being sent."}
        try:
            now = self._monotonic()
            if self._last_test_message is not None and now - self._last_test_message < TEST_GAP_SECONDS:
                wait = max(1, round(TEST_GAP_SECONDS - (now - self._last_test_message) + 0.49))
                return 429, {"ok": False, "status": "too_soon",
                             "message": f"NOT SENT (too_soon): a test message went out a moment ago. Wait {wait} seconds and try again."}
            result = setup_checks.send_test(self.supervisor.settings, self.client)
            if result["ok"]:
                self._last_test_message = self._monotonic()
            return 200, result
        finally:
            self._send_lock.release()

    # ------------------------------------------------------------------------------------------ the connection test
    def test_connection(self, body):
        if not _only(body, ()):
            return _BAD
        blocked = self._need_applied()
        if blocked:
            return blocked
        if not self._test_lock.acquire(blocking=False):
            return 409, {"ok": False, "status": "busy", "message": "A test is already running."}
        try:
            return 200, {"ok": True, **setup_checks.run_checks(self.supervisor.settings, self.client)}
        finally:
            self._test_lock.release()
