"""Who wrote to the bot, kept for ten minutes so the setup page can offer them: only the chat id, a first name and a time, private chats
only, never the message text, never printed. The worker is the one that sees the messages, so a button tap can no longer make
Telegram forget one before the finder asks."""

import sqlite3
from datetime import timedelta

from headsup.actions import ActionHandler
from headsup.store import CHAT_KEEP, Store
from headsup.telegram import Telegram
from world import BOT, CHAT, NOW, World

UNIQUE_TEXT = "the-words-nobody-may-keep-7731"


def handler(store, world, out=None):
    telegram = Telegram(world.client, BOT, CHAT, store)
    return ActionHandler(store, telegram, writer=None, chat_id=CHAT, write_repos=(), now=lambda: NOW, out=out or (lambda line: None)), telegram


def message(update_id, chat, *, kind="private", first="Ada", when=NOW, **chat_extra):
    return {"update_id": update_id, "message": {"message_id": update_id, "date": int(when.timestamp()), "text": UNIQUE_TEXT,
                                                "chat": {"id": chat, "type": kind, "first_name": first, "last_name": "Lovelace", "username": "ada_l", **chat_extra},
                                                "from": {"id": abs(chat), "first_name": first}}}


# ------------------------------------------------------------------------------------------------------------------ the store
def test_the_store_keeps_an_id_a_first_name_and_a_time_and_keeps_the_newest_time(tmp_path):
    store = Store(tmp_path / "t.db")
    store.note_chat(5, "Ada", NOW - timedelta(minutes=4), NOW)
    store.note_chat(5, "Ada", NOW - timedelta(minutes=1), NOW)
    store.note_chat(5, "Ada", NOW - timedelta(minutes=3), NOW)  # an older message arriving later does not move the time back
    assert store.recent_chats(NOW) == [(5, "Ada", NOW - timedelta(minutes=1))]
    assert [c[1] for c in store._db.execute("PRAGMA table_info(seen_chats)").fetchall()] == ["chat_id", "first_name", "last_seen"], "nothing else is kept"


def test_rows_older_than_ten_minutes_are_dropped_when_anything_is_read_or_written(tmp_path):
    store = Store(tmp_path / "t.db")
    assert CHAT_KEEP == timedelta(minutes=10)
    store.note_chat(1, "Old", NOW - timedelta(minutes=9, seconds=30), NOW)
    assert [c[0] for c in store.recent_chats(NOW)] == [1]
    assert store.recent_chats(NOW + timedelta(minutes=1)) == [], "ten minutes after the message it is gone"
    assert store._db.execute("SELECT COUNT(*) FROM seen_chats").fetchone()[0] == 0, "gone from the file, not merely hidden"
    store.note_chat(2, "Fresh", NOW, NOW)
    store.note_chat(3, "Newer", NOW + timedelta(minutes=11), NOW + timedelta(minutes=11))  # writing prunes too
    assert [c[0] for c in store._db.execute("SELECT chat_id FROM seen_chats").fetchall()] == [3]


def test_a_message_that_is_already_ten_minutes_old_is_not_stored_at_all(tmp_path):
    store = Store(tmp_path / "t.db")
    store.note_chat(1, "Stale", NOW - timedelta(minutes=10, seconds=1), NOW)
    assert store._db.execute("SELECT COUNT(*) FROM seen_chats").fetchone()[0] == 0


def test_newest_first(tmp_path):
    store = Store(tmp_path / "t.db")
    store.note_chat(1, "Ada", NOW - timedelta(minutes=5), NOW)
    store.note_chat(2, "Grace", NOW - timedelta(minutes=1), NOW)
    assert [c[1] for c in store.recent_chats(NOW)] == ["Grace", "Ada"]


# ------------------------------------------------------------------------------------------------------------------ the worker's handler
def test_a_private_message_is_noted_confirmed_and_not_answered(tmp_path):
    world, store = World(), Store(tmp_path / "t.db")
    actions, telegram = handler(store, world)
    outcomes = actions.process_updates([message(10, 777)])
    assert outcomes == [] and store.recent_chats(NOW) == [(777, "Ada", NOW)]
    assert store.get_kv("telegram_offset") == "11", "confirmed like any handled update"
    assert world.calls == [], "no reply, no answer, no keyboard change: the message is not acted on"


def test_the_message_text_surname_and_username_are_stored_nowhere(tmp_path):
    world, store = World(), Store(tmp_path / "t.db")
    actions, _ = handler(store, world)
    actions.process_updates([message(10, 777)])
    store.close()
    db = sqlite3.connect(tmp_path / "t.db")
    dump = "\n".join(repr(row) for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                     for row in db.execute(f"SELECT * FROM {table}"))
    assert "Ada" in dump and "777" in dump
    for forbidden in (UNIQUE_TEXT, "Lovelace", "ada_l"):
        assert forbidden not in dump


def test_nothing_about_a_message_is_ever_printed_or_logged(tmp_path, capsys, caplog):
    world, store, printed = World(), Store(tmp_path / "t.db"), []
    actions, _ = handler(store, world, out=printed.append)
    actions.process_updates([message(10, 777), message(11, -100555, kind="supergroup")])
    seen = capsys.readouterr()
    everything = "\n".join(printed) + seen.out + seen.err + caplog.text
    assert printed == [] and not any(x in everything for x in ("777", "Ada", UNIQUE_TEXT, "100555"))


def test_groups_channels_and_odd_messages_are_confirmed_but_not_stored(tmp_path):
    world, store = World(), Store(tmp_path / "t.db")
    actions, _ = handler(store, world)
    odd = {"update_id": 15, "message": {"chat": {"id": 3, "type": "private"}, "date": "soon"}}  # no name, a date that is not a number
    actions.process_updates([message(12, -100555, kind="supergroup"), message(13, 5, kind="channel"), message(14, -7), odd])
    assert [c[:2] for c in store.recent_chats(NOW)] == [(3, "(no name)")], "only the private chat, stored with what little it had"
    assert store.get_kv("telegram_offset") == "16"


def test_old_messages_in_the_backlog_are_not_kept(tmp_path):
    world, store = World(), Store(tmp_path / "t.db")
    actions, _ = handler(store, world)
    actions.process_updates([message(10, 777, when=NOW - timedelta(hours=3))])
    assert store.recent_chats(NOW) == [] and store.get_kv("telegram_offset") == "11"


def test_the_worker_asks_for_messages_as_well_as_taps(tmp_path):
    world, store = World(), Store(tmp_path / "t.db")
    _, telegram = handler(store, world)
    telegram.get_updates(timeout=1)
    assert world.calls[-1][1]["allowed_updates"] == ["callback_query", "message"]


# ------------------------------------------------------------------------------------------------------------------ the bug this fixes
def test_a_button_tap_after_a_message_no_longer_makes_the_message_disappear(tmp_path):
    """The live bug: "hi" at 17:43, a Re-run CI tap at 17:47. Confirming the tap forgot the earlier message at Telegram, so the finder
    found nothing. Now the worker has seen and kept the message before it confirms the tap."""
    world, store = World(), Store(tmp_path / "t.db")
    world.message(777, name="Ada", update_id=100, date=int(NOW.timestamp()))
    world.tap("0123456789abcdef", update_id=101, message_id=5)
    actions, telegram = handler(store, world)
    actions.process_updates(telegram.get_updates(timeout=0))  # the worker's poll: the message, then the tap
    telegram.get_updates(timeout=0)  # its next slice sends offset 102: Telegram forgets everything below it
    assert [u["update_id"] for u in world.updates] == [], "Telegram no longer has the message"
    assert store.recent_chats(NOW) == [(777, "Ada", NOW)], "but the app kept who wrote it"
