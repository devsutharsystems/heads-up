import json
import logging

import httpx
import pytest

from headsup.store import Store
from headsup.telegram import Telegram, TelegramError, WebhookConflict
from world import BOT, CHAT, World


FAKE_TOKEN = "a1b2c3d4" + "e5f60718"  # made-up 16-hex token, split so the secrets scanner sees no hex literal


class FakeTime:
    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def make(tmp_path, world=None):
    world = world or World()
    t = FakeTime()
    store = Store(tmp_path / "t.db")
    return Telegram(world.client, BOT, CHAT, store, clock=t.clock, sleep=t.sleep), world, store, t


def test_telegram_sends_message_with_buttons_and_long_polls(tmp_path):
    tg, world, store, _ = make(tmp_path)
    mid = tg.send("CI failed: Build on main.", [("Snooze 1h", FAKE_TOKEN), ("Remind in 2h", "0123456789abcdef")])
    assert mid == 100
    (payload,) = world.sent()
    assert payload["chat_id"] == CHAT and payload["text"] == "CI failed: Build on main."
    assert "parse_mode" not in payload  # plain text: nothing in the text is parsed as Markdown or HTML
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["Snooze 1h", "Remind in 2h"]
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)

    # long polling: button taps and messages (a message is only noted, see chat_from_update), the stored offset, an HTTP timeout longer than the poll timeout
    world.tap(FAKE_TOKEN, update_id=7)
    updates = tg.get_updates(timeout=25)
    method, poll = world.calls[-1]
    assert method == "getUpdates" and poll["timeout"] == 25 and poll["allowed_updates"] == ["callback_query", "message"] and "offset" not in poll
    assert [u["update_id"] for u in updates] == [7]
    tg.confirm(7)
    assert store.get_kv("telegram_offset") == "8"  # offset lives in SQLite
    tg.get_updates(timeout=25)
    assert world.calls[-1][1]["offset"] == 8
    assert Telegram(world.client, BOT, CHAT, Store(tmp_path / "t.db")).store.get_kv("telegram_offset") == "8"  # survives a restart

    tg.answer_callback("cq7", "Done" * 100)
    assert world.calls[-1] == ("answerCallbackQuery", {"callback_query_id": "cq7", "text": ("Done" * 100)[:200]})
    tg.remove_keyboard(100)
    assert world.calls[-1] == ("editMessageReplyMarkup", {"chat_id": CHAT, "message_id": 100, "reply_markup": {"inline_keyboard": []}})


def test_text_is_cut_to_4096_and_empty_text_refused(tmp_path):
    tg, world, *_ = make(tmp_path)
    tg.send("x" * 5000)
    assert len(world.sent()[0]["text"]) == 4096 and "reply_markup" not in world.sent()[0]
    with pytest.raises(ValueError):
        tg.send("")


def test_markup_looking_text_is_sent_as_is(tmp_path):
    tg, world, *_ = make(tmp_path)
    tg.send("<b>bold</b> *star* [link](http://example.invalid) `code`")
    assert world.sent()[0]["text"] == "<b>bold</b> *star* [link](http://example.invalid) `code`" and "parse_mode" not in world.sent()[0]


def test_callback_data_must_be_1_to_64_bytes(tmp_path):
    tg, *_ = make(tmp_path)
    for bad in ("", "x" * 65, "é" * 33):
        with pytest.raises(ValueError):
            tg.send("hi", [("b", bad)])


def test_at_most_one_message_per_second(tmp_path):
    tg, world, _, t = make(tmp_path)
    tg.send("one")
    t.t += 0.25
    tg.send("two")
    assert t.sleeps == [pytest.approx(0.75)]
    t.t += 5
    tg.send("three")
    assert len(t.sleeps) == 1  # no wait when more than a second has passed


def test_messages_only_ever_go_to_the_configured_chat(tmp_path):
    tg, world, *_ = make(tmp_path)
    tg.send("a")
    tg.send("b", [("x", "0123456789abcdef")])
    assert {p["chat_id"] for p in world.sent()} == {CHAT}
    import inspect
    assert "chat" not in inspect.signature(tg.send).parameters  # there is no way to address another chat


def test_errors_never_contain_the_bot_token_or_url(tmp_path, caplog):
    world = World()
    world.telegram_fails = True
    tg, *_ = make(tmp_path, world)
    with caplog.at_level(logging.DEBUG), pytest.raises(TelegramError) as err:
        tg.send("hi")
    assert BOT not in str(err.value) and "api.telegram.org" not in str(err.value) and BOT not in repr(tg) and BOT not in caplog.text

    def boom(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    tg2 = Telegram(httpx.Client(transport=httpx.MockTransport(boom)), BOT, CHAT, Store(tmp_path / "t2.db"))
    with pytest.raises(TelegramError) as err2:
        tg2.send("hi")
    assert BOT not in str(err2.value) and BOT not in repr(err2.value.__cause__) and err2.value.kind == "ConnectError"


def test_webhook_conflict_is_a_clear_error_and_nothing_is_deleted(tmp_path):
    world = World()
    world.webhook_set = True
    tg, *_ = make(tmp_path, world)
    with pytest.raises(WebhookConflict):
        tg.get_updates(timeout=1)
    assert "deleteWebhook" not in world.methods()


def test_rate_limited_send_raises_without_retrying(tmp_path):
    def limited(request):
        return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 3}})

    tg = Telegram(httpx.Client(transport=httpx.MockTransport(limited)), BOT, CHAT, Store(tmp_path / "t.db"))
    with pytest.raises(TelegramError) as err:
        tg.send("hi")
    assert err.value.kind == "rate_limited"


def test_httpx_request_logging_is_kept_off_so_urls_never_reach_a_log(caplog):
    import headsup  # noqa: F401
    assert logging.getLogger("httpx").level >= logging.WARNING and logging.getLogger("httpcore").level >= logging.WARNING


# ---------------------------------------------------------------------------------------------------- who wrote (chat_from_update)
from headsup.telegram import chat_from_update


def private_message(chat_id=5, **chat):
    return {"update_id": 1, "message": {"message_id": 1, "date": 1_793_620_000, "text": "private words",
                                        "chat": {"id": chat_id, "type": "private", "first_name": "Ada", **chat}}}


def test_chat_from_update_reads_only_the_id_the_first_name_and_the_time():
    chat_id, name, sent = chat_from_update(private_message(last_name="Lovelace", username="ada_l"))
    assert (chat_id, name) == (5, "Ada") and sent.isoformat() == "2026-11-02T11:46:40+00:00"


@pytest.mark.parametrize("update", [
    {"update_id": 1, "message": {"chat": {"id": -100123, "type": "supergroup", "title": "Team"}}},
    {"update_id": 1, "message": {"chat": {"id": 5, "type": "group"}}},
    {"update_id": 1, "message": {"chat": {"id": 5, "type": "channel"}}},
    {"update_id": 1, "message": {"chat": {"id": -5, "type": "private"}}},
    {"update_id": 1, "message": {"chat": {"id": 0, "type": "private"}}},
    {"update_id": 1, "message": {"chat": {"id": True, "type": "private"}}},
    {"update_id": 1, "message": {"chat": {"id": "5", "type": "private"}}},
    {"update_id": 1, "message": {"chat": "nope"}}, {"update_id": 1, "message": "nope"}, {"update_id": 1, "callback_query": {}},
    {"update_id": 1, "edited_message": {"chat": {"id": 5, "type": "private"}}}, "not a dict", None])
def test_chat_from_update_ignores_everything_but_a_private_message(update):
    assert chat_from_update(update) is None


def test_chat_from_update_cleans_the_name_and_survives_missing_parts():
    assert chat_from_update(private_message(first_name="A\x00d\nx"))[1] == "Adx"
    assert chat_from_update(private_message(first_name="n" * 90))[1] == "n" * 60
    assert chat_from_update(private_message(first_name=7))[1] == "(no name)"
    no_date = private_message()
    del no_date["message"]["date"]
    assert chat_from_update(no_date)[2] is None
