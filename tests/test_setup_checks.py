"""The connection tests and the chat-ID finder behind /setup. Everything runs against the fake world: no socket is opened."""

import json
from datetime import datetime, timezone

import httpx
import pytest

from headsup import netutil, setup_checks
from headsup.setup_checks import TEST_TEXT, check_calendar, check_github, check_telegram, find_chats, not_tested, run_checks, send_test
from headsup.telegram import Telegram
from world import BOT, CHAT, ICAL_URL, READ_TOKEN, REPO, WRITE_TOKEN, World, ics_for, settings

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
SECRETS = (BOT, READ_TOKEN, WRITE_TOKEN, "secret-address-made-up", str(CHAT), "fake-anthropic-key")


def nothing_secret(value):
    text = json.dumps(value, default=str)
    return [s for s in SECRETS if s in text] == []


# ------------------------------------------------------------------------------------------------------------------ GitHub
def test_github_ok_uses_the_saved_token_and_the_shared_timeouts():
    world = World()
    assert check_github(settings(), world.client) == [{"ok": True, "message": "OK", "name": REPO}]
    assert world.github_reads == [f"/repos/{REPO}"] and world.github_writes == []
    assert world.github_headers[0]["authorization"] == f"Bearer {READ_TOKEN}"
    assert world.timeouts[0] == httpx.Timeout(15.0, connect=10.0).as_dict() == netutil.TIMEOUT.as_dict()


def test_github_without_a_token_asks_without_credentials_so_public_repositories_work():
    world = World()
    assert check_github(settings(github_token=None), world.client)[0]["ok"]
    assert "authorization" not in world.github_headers[0]


@pytest.mark.parametrize("status,message", [(401, "HTTP 401 (token rejected)"), (403, "HTTP 403 (no access, or rate limited)"),
                                            (404, "HTTP 404 (repository not found, or the token cannot see it)"), (500, "HTTP 500")])
def test_github_errors_are_a_status_and_nothing_else(status, message):
    world = World()
    world.github_status[f"/repos/{REPO}"] = status
    result = check_github(settings(), world.client)
    assert result == [{"ok": False, "message": message, "name": REPO}]
    assert nothing_secret(result)


def test_github_network_error_one_attempt_no_detail():
    world = World()
    world.github_raise = httpx.ConnectError(f"cannot reach https://api.github.com with {READ_TOKEN}")
    result = check_github(settings(), world.client)
    assert result == [{"ok": False, "message": "network error", "name": REPO}]
    assert world.github_reads == [f"/repos/{REPO}"], "one attempt, no retry"
    assert nothing_secret(result)


def test_github_checks_every_repository_and_reports_each_separately():
    world = World()
    world.github_status["/repos/example-org/other"] = 404
    result = check_github(settings(github_repos=(REPO, "example-org/other")), world.client)
    assert [(r["name"], r["ok"]) for r in result] == [(REPO, True), ("example-org/other", False)]


def test_github_with_no_repositories_says_so_and_asks_nothing():
    world = World()
    assert check_github(settings(github_repos=()), world.client) == [{"ok": False, "message": "no repositories set", "name": "GitHub"}]
    assert world.github_reads == []


# ------------------------------------------------------------------------------------------------------------------ calendar
def calendar_world(**kw):
    return World(calendar=ics_for(datetime(2026, 11, 2, 12, 20, tzinfo=timezone.utc)), **kw)


def test_calendar_readable():
    world = calendar_world()
    assert check_calendar(settings(), world.client, now=lambda: NOW) == {"ok": True, "message": "OK, calendar readable", "configured": True}
    assert world.timeouts[-1] == netutil.TIMEOUT.as_dict()


def test_calendar_not_set_asks_nothing():
    world = calendar_world()
    assert check_calendar(settings(ical_url=None), world.client)["configured"] is False
    assert world.timeouts == []


@pytest.mark.parametrize("status,message", [(500, "calendar fetch failed: HTTP 500"), (302, "calendar fetch failed: HTTP 302"), (404, "calendar fetch failed: HTTP 404")])
def test_calendar_http_problems_are_a_status_and_never_the_address(status, message):
    world = calendar_world()
    world.calendar_status = status
    result = check_calendar(settings(), world.client, now=lambda: NOW)
    assert (result["ok"], result["message"]) == (False, message)
    assert nothing_secret(result)


def test_calendar_too_large_not_a_calendar_and_a_bad_address():
    big = World(calendar=b"X" * (2 * 1024 * 1024 + 1))
    assert check_calendar(settings(), big.client, now=lambda: NOW)["message"] == "calendar feed is larger than 2 MB"
    junk = World(calendar=b"this is not a calendar")
    result = check_calendar(settings(), junk.client, now=lambda: NOW)
    assert (result["ok"], result["message"]) == (False, "the address did not return a calendar")
    insecure = check_calendar(settings(ical_url="http://calendar.example.invalid/secret-address-made-up/a.ics"), World().client)
    assert insecure["ok"] is False and nothing_secret(insecure)


def test_calendar_check_does_not_touch_the_real_data_folder():
    from headsup import store
    world = calendar_world()
    paths = []
    real = store.Store

    def spy(path=None, *args, **kwargs):
        paths.append(str(path))
        return real(path, *args, **kwargs)

    import headsup.setup_checks as module
    original = module.Store
    module.Store = spy
    try:
        check_calendar(settings(), world.client, now=lambda: NOW)
    finally:
        module.Store = original
    assert paths == [":memory:"], "a throw-away in-memory store: nothing is remembered between tests"


# ------------------------------------------------------------------------------------------------------------------ Telegram
def test_telegram_ok_names_the_bot_and_does_not_send_anything():
    world = World()
    result = check_telegram(settings(), world.client)
    assert result == {"ok": True, "message": "OK, bot @headsup_test_bot", "configured": True}
    assert world.methods() == ["getMe"] and world.sent() == []


def test_telegram_without_a_chat_id_says_it_is_not_set_yet():
    result = check_telegram(settings(telegram_chat_id=None), World().client)
    assert result["ok"] and result["message"] == "OK, bot @headsup_test_bot; the chat ID is not set yet"


def test_telegram_token_problems_and_network_errors_carry_no_token():
    world = World()
    world.telegram_status["getMe"] = 401
    rejected = check_telegram(settings(), world.client)
    assert (rejected["ok"], rejected["message"]) == (False, "HTTP 401 (token rejected)")
    world.telegram_status["getMe"] = 429
    assert check_telegram(settings(), world.client)["message"] == "rate limited, try again in a minute"
    down = World()
    down.telegram_raise = httpx.ConnectError(f"cannot reach https://api.telegram.org/bot{BOT}/getMe")
    result = check_telegram(settings(), down.client)
    assert (result["ok"], result["message"]) == (False, "network error") and nothing_secret(result)


def test_telegram_not_configured():
    world = World()
    assert check_telegram(settings(telegram_bot_token=None), world.client) == {"ok": False, "message": "no bot token set", "configured": False}
    assert world.calls == []


def test_a_username_that_is_not_a_plain_username_is_not_shown():
    world = World()
    world.bot_username = "<b>bad name</b>"
    assert check_telegram(settings(), world.client)["message"] == "OK"


# ------------------------------------------------------------------------------------------------------------------ not tested
def test_haiku_and_the_write_token_are_never_tested_and_nothing_is_sent():
    world = World()
    result = not_tested(settings())
    assert result["haiku"] == {"tested": False, "message": "not tested: a test call would use your Anthropic credit"}
    assert result["writeToken"] == {"tested": False, "message": "not tested: it can only be tested by re-running a build"}
    assert not_tested(settings(anthropic_api_key=None, github_actions_write_token=None))["haiku"]["message"] == "no key set: alerts use the template text"
    assert world.calls == [] and world.github_writes == []


def test_run_checks_runs_every_check_once_and_sends_no_message_and_no_write():
    world = calendar_world()
    result = run_checks(settings(), world.client, now=lambda: NOW)
    assert set(result) == {"github", "calendar", "telegram", "timing_ms", "haiku", "writeToken"}
    assert set(result["timing_ms"]) == {"github", "calendar", "telegram"} and all(isinstance(v, int) and v >= 0 for v in result["timing_ms"].values())
    assert result["github"][0]["ok"] and result["calendar"]["ok"] and result["telegram"]["ok"]
    assert world.methods() == ["getMe"] and world.github_writes == [] and world.decider_requests == []
    assert nothing_secret(result)


# ------------------------------------------------------------------------------------------------------------------ the chat finder
def test_the_finder_asks_for_messages_only_with_no_offset_and_never_confirms():
    world = World()
    world.message(777, name="Ada")
    world.tap("not-a-real-token")  # a button tap waiting for the worker must stay untouched
    found = find_chats(settings(), world.client)
    assert world.calls == [("getUpdates", {"timeout": 0, "allowed_updates": ["message"]})], "no offset: nothing is confirmed or skipped"
    assert [u["update_id"] for u in world.updates] == [1, 2], "the tap is still queued for the worker"
    assert found["status"] == "ok" and [c["id"] for c in found["chats"]] == [777]


def test_the_finder_never_touches_the_stored_polling_position(tmp_path):
    from world import store_at
    store = store_at(tmp_path)
    store.set_kv("telegram_offset", 41)
    world = World()
    world.message(777)
    find_chats(settings(), world.client)
    assert store.get_kv("telegram_offset") == "41" or store.get_kv("telegram_offset") == 41


def test_one_private_chat():
    world = World()
    world.message(777, name="Ada", last_name="Lovelace", date=1_793_620_000)
    found = find_chats(settings(), world.client)
    assert found["chats"] == [{"id": 777, "name": "Ada Lovelace", "last": datetime.fromtimestamp(1_793_620_000, tz=timezone.utc)}]
    assert found["ignored"] == 0


def test_several_private_chats_newest_first_and_each_listed_once():
    world = World()
    world.message(111, name="Ada", date=1_793_620_000)
    world.message(222, name="Grace", date=1_793_620_500)
    world.message(111, name="Ada", date=1_793_621_000)
    world.message(111, name="Ada", date=1_793_619_000)
    found = find_chats(settings(), world.client)
    assert [(c["id"], c["name"]) for c in found["chats"]] == [(111, "Ada"), (222, "Grace")]
    assert found["chats"][0]["last"] == datetime.fromtimestamp(1_793_621_000, tz=timezone.utc)


def test_groups_supergroups_and_channels_are_counted_but_never_offered():
    world = World()
    world.message(5, name="Ada")
    world.message(-100123, kind="supergroup", name="Team")
    world.message(-900, kind="group", name="Friends")
    world.message(-1002, kind="channel", name="News")
    found = find_chats(settings(), world.client)
    assert [c["id"] for c in found["chats"]] == [5] and found["ignored"] == 3


def test_nothing_found_explains_what_to_do_and_the_24_hour_limit():
    found = find_chats(settings(), World().client)
    assert found["status"] == "none" and found["chats"] == []
    assert "send it any message" in found["message"] and "24 hours" in found["message"]


def test_no_token_means_no_request():
    world = World()
    found = find_chats(settings(telegram_bot_token=None), world.client)
    assert found["status"] == "no_token" and world.calls == []


def test_a_webhook_gets_its_own_message_and_is_never_removed():
    world = World()
    world.webhook_set = True
    found = find_chats(settings(), world.client)
    assert found["status"] == "webhook" and "Remove the webhook yourself" in found["message"] and "will not do it for you" in found["message"]
    assert world.methods() == ["getUpdates"], "no deleteWebhook"


def test_other_failures_are_generic():
    world = World()
    world.telegram_status["getUpdates"] = 401
    found = find_chats(settings(), world.client)
    assert found["status"] == "error" and found["message"] == "HTTP 401 (token rejected)" and nothing_secret(found)
    down = World()
    down.telegram_raise = httpx.ReadTimeout(f"timed out talking to {BOT}")
    again = find_chats(settings(), down.client)
    assert again["status"] == "error" and again["message"] == "network error" and nothing_secret(again)


def test_names_are_made_printable_and_short_and_odd_updates_are_skipped():
    world = World()
    world.message(1, name="Ada\x07\x00X")
    world.message(2, name="n" * 200)
    world.updates.append({"update_id": 90, "message": {"chat": {"id": True, "type": "private"}}})
    world.updates.append({"update_id": 91, "message": {"chat": "not a dict"}})
    world.updates.append({"update_id": 92, "message": {"chat": {"id": 3, "type": "private"}}})  # no name, no date
    world.updates.append({"update_id": 93, "edited_message": {"chat": {"id": 4, "type": "private"}}})
    world.updates.append({"update_id": 94, "message": {"chat": {"id": 0, "type": "private"}}})
    found = find_chats(settings(), world.client)
    names = {c["id"]: c["name"] for c in found["chats"]}
    assert names[1] == "AdaX" and names[2] == "n" * 60 and names[3] == "(no name)" and set(names) == {1, 2, 3}


# ------------------------------------------------------------------------------------------------------------------ the test message
def test_the_test_message_is_one_fixed_text_to_the_saved_chat_and_nothing_else():
    world = World()
    result = send_test(settings(), world.client)
    assert result == {"ok": True, "status": "sent", "message": "SENT: Telegram accepted the test message (message number 100). Look in your chat with the bot."}
    assert world.sent() == [{"chat_id": CHAT, "text": TEST_TEXT}]
    assert world.methods() == ["sendMessage"]


@pytest.mark.parametrize("kw", [{"telegram_bot_token": None}, {"telegram_chat_id": None}])
def test_the_test_message_needs_a_token_and_a_chat_id_and_sends_nothing_without_them(kw):
    world = World()
    assert send_test(settings(**kw), world.client) == {"ok": False, "status": "not_configured",
                                                       "message": "NOT SENT (not_configured): Save the bot token and the chat ID first."}
    assert world.calls == []


@pytest.mark.parametrize("status,needle", [(400, "does not know this chat"), (403, "press Start"), (401, "was rejected"), (500, "HTTP 500")])
def test_test_message_failures_say_what_to_do_without_the_token(status, needle):
    world = World()
    world.telegram_status["sendMessage"] = status
    result = send_test(settings(), world.client)
    assert result["ok"] is False and needle in result["message"] and nothing_secret(result)


def test_none_of_the_helpers_gives_telegram_a_store_so_none_can_read_or_move_the_polling_position(monkeypatch):
    built = []

    class Spy(Telegram):
        def __init__(self, client, bot_token, chat_id, store, *args, **kwargs):
            built.append(store)
            super().__init__(client, bot_token, chat_id, store, *args, **kwargs)

    monkeypatch.setattr(setup_checks, "Telegram", Spy)
    world = World()
    world.message(777)
    check_telegram(settings(), world.client)
    find_chats(settings(), world.client)
    send_test(settings(), world.client)
    assert built == [None, None, None]


SEND_OUTCOMES = [  # what goes wrong -> the status it reports and a phrase only that path says
    ({"telegram_status": {"sendMessage": 400}}, "http_400", "does not know this chat"),
    ({"telegram_status": {"sendMessage": 401}}, "http_401", "bot token was rejected"),
    ({"telegram_status": {"sendMessage": 403}}, "http_403", "press Start"),
    ({"telegram_status": {"sendMessage": 404}}, "http_404", "not recognised"),
    ({"telegram_status": {"sendMessage": 429}}, "rate_limited", "too many requests"),
    ({"telegram_status": {"sendMessage": 500}}, "http_500", "answered HTTP 500."),
    ({"telegram_raise": httpx.ReadTimeout("slow")}, "timeout", "may still arrive"),
    ({"telegram_raise": httpx.ConnectError("down")}, "network", "Could not reach Telegram"),
    ({"telegram_raise": httpx.ConnectTimeout("down")}, "network", "Could not reach Telegram"),
]


@pytest.mark.parametrize("setup,status,phrase", SEND_OUTCOMES)
def test_every_way_the_test_message_can_fail_has_its_own_status_and_words(setup, status, phrase):
    world = World()
    for name, value in setup.items():
        setattr(world, name, value)
    result = send_test(settings(), world.client)
    assert result["ok"] is False and result["status"] == status and phrase in result["message"]
    assert result["message"].startswith(f"NOT SENT ({status}): "), "it says in so many words that nothing was sent"
    assert nothing_secret(result)


def test_the_outcomes_are_all_told_apart():
    messages = {}
    for setup, status, _ in SEND_OUTCOMES:
        world = World()
        for name, value in setup.items():
            setattr(world, name, value)
        messages.setdefault(status, set()).add(send_test(settings(), world.client)["message"])
    assert len(set().union(*messages.values())) == len(messages) == 8, "eight different statuses, eight different texts"
    ok = send_test(settings(), World().client)
    assert ok["status"] == "sent" and ok["message"].startswith("SENT: ") and "NOT SENT" not in ok["message"]


# ------------------------------------------------------------------------------------------------------------------ the checks side by side
def test_the_three_checks_run_side_by_side_and_each_is_timed(monkeypatch):
    import time as _time
    started = []

    def slow(name, result):
        def check(*args, **kwargs):
            started.append((name, _time.monotonic()))
            _time.sleep(0.3)
            return result
        return check

    monkeypatch.setattr(setup_checks, "check_github", slow("github", [{"ok": True, "message": "OK", "name": "a/b"}]))
    monkeypatch.setattr(setup_checks, "check_calendar", slow("calendar", {"ok": True, "message": "OK, calendar readable", "configured": True}))
    monkeypatch.setattr(setup_checks, "check_telegram", slow("telegram", {"ok": True, "message": "OK", "configured": True}))
    began = _time.monotonic()
    result = run_checks(settings(), World().client)
    elapsed = _time.monotonic() - began
    assert elapsed < 0.7, f"sequentially this would take 0.9 s; it took {elapsed:.2f} s"
    assert max(t for _, t in started) - min(t for _, t in started) < 0.2, "all three began together"
    assert all(250 <= ms < 700 for ms in result["timing_ms"].values())
    assert result["github"][0]["name"] == "a/b" and result["calendar"]["ok"] and result["telegram"]["ok"]


def test_every_ok_says_ok():
    world = calendar_world()
    result = run_checks(settings(), world.client, now=lambda: NOW)
    assert result["github"][0]["message"] == "OK" and result["calendar"]["message"].startswith("OK") and result["telegram"]["message"].startswith("OK")


# ------------------------------------------------------------------------------------------------------------------ stored chats
def test_stored_chats_offers_what_the_worker_saw_and_asks_telegram_nothing(tmp_path):
    from datetime import timedelta
    from headsup.setup_checks import stored_chats
    from headsup.store import Store
    store = Store(tmp_path / "t.db")
    store.note_chat(777, "Ada", NOW - timedelta(minutes=2), NOW)
    store.close()
    world = World()
    found = stored_chats(lambda: Store(tmp_path / "t.db"), lambda: NOW)
    assert found["status"] == "ok" and found["ignored"] == 0 and world.calls == []
    assert found["chats"] == [{"id": 777, "name": "Ada", "last": NOW - timedelta(minutes=2)}]


def test_stored_chats_says_how_to_try_again_when_nothing_is_there(tmp_path):
    from headsup.setup_checks import stored_chats
    from headsup.store import Store
    found = stored_chats(lambda: Store(tmp_path / "t.db"), lambda: NOW)
    assert found["status"] == "none" and found["chats"] == [] and "10 minutes" in found["message"] and "hi" in found["message"]


def test_the_direct_finder_warns_that_a_button_tap_makes_telegram_forget_a_message():
    found = find_chats(settings(), World().client)
    assert found["status"] == "none" and "24 hours" in found["message"] and "forgets" in found["message"] and '"hi" again' in found["message"]
