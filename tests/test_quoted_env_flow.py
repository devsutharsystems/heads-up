"""A settings file written by the /setup page holds every value in single quotes. The whole path (save, a fresh start that reads the
file, "Send test message") must deliver exactly the saved token and chat ID to Telegram. The file is a temp file with fake values."""

import json
import os
from types import SimpleNamespace

from dotenv import dotenv_values

from headsup import config
from headsup.setup_service import SetupService
from world import BOT, CHAT, World

OTHER_CHAT = 777000111  # a second person who wrote to the bot


class FakeSupervisor:
    """Just what SetupService uses: the running settings, a restart that adopts new ones, and a runner that is not polling."""

    def __init__(self, settings):
        self.settings, self.runner = settings, SimpleNamespace(telegram_ready=False)

    def restart(self, settings):
        self.settings = settings
        return SimpleNamespace(status="ok", detail="")


def fresh_process():
    """What a new start sees: none of our variables in the environment, nothing remembered as loaded from the file."""
    for name in config.SETTING_NAMES:
        os.environ.pop(name, None)
    config._FROM_FILE.clear()


def sent_to_telegram(world):
    return [(token, payload["chat_id"]) for (method, payload), token in zip(world.calls, world.bot_tokens) if method == "sendMessage"]


def test_a_saved_quoted_file_delivers_the_exact_token_and_chat_id_after_save_and_after_a_restart(tmp_path):
    env, world = tmp_path / ".env", World()
    service = SetupService(FakeSupervisor(config.load_settings(env)), world.client, env_path=env)
    status, data = service.save({"values": {"TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": str(CHAT), "GITHUB_REPOS": "example-org/one"}})
    assert status == 200 and data["applied"] is True
    text = env.read_text(encoding="utf-8")
    assert f"TELEGRAM_CHAT_ID='{CHAT}'" in text and f"TELEGRAM_BOT_TOKEN='{BOT}'" in text  # the file really is quoted
    assert dotenv_values(env)["TELEGRAM_CHAT_ID"] == str(CHAT)

    status, data = service.send_test({})  # after Save: the reloaded settings
    assert status == 200 and data["status"] == "sent"
    assert sent_to_telegram(world) == [(BOT, CHAT)] and type(world.sent()[0]["chat_id"]) is int

    fresh_process()  # a full restart with the quotes still in the file
    restarted = SetupService(FakeSupervisor(config.load_settings(env)), world.client, env_path=env)
    status, data = restarted.send_test({})
    assert status == 200 and data["status"] == "sent"
    assert sent_to_telegram(world)[-1] == (BOT, CHAT) and len(sent_to_telegram(world)) == 2
    assert json.dumps(world.sent()[-1]["chat_id"]) == str(CHAT)  # a JSON number, as Telegram expects


def test_find_my_chat_id_then_use_this_chat_stores_the_person_who_wrote_not_anyone_else(tmp_path):
    env, world = tmp_path / ".env", World()
    config.save_env({"TELEGRAM_BOT_TOKEN": BOT, "GITHUB_REPOS": "example-org/one"}, env_path=env)
    service = SetupService(FakeSupervisor(config.load_settings(env)), world.client, env_path=env)
    world.message(OTHER_CHAT, name="Grace", date=1_793_620_000)
    world.message(CHAT, name="Ada", date=1_793_620_500)  # the newest message: the person at the keyboard
    world.message(-100123, kind="supergroup", name="A group")
    status, found = service.find_chat({})
    assert found["status"] == "choose" and [c["name"] for c in found["choices"]] == ["Ada", "Grace"]
    index = next(c["index"] for c in found["choices"] if c["name"] == "Ada")
    status, data = service.use_chat({"index": index})
    assert status == 200 and data["applied"] is True
    assert dotenv_values(env)["TELEGRAM_CHAT_ID"] == str(CHAT)

    fresh_process()
    restarted = SetupService(FakeSupervisor(config.load_settings(env)), world.client, env_path=env)
    assert restarted.send_test({})[1]["status"] == "sent"
    assert sent_to_telegram(world) == [(BOT, CHAT)]


# ------------------------------------------------------------------------------------------------ the bot's own ID is not a chat
BOT_ID = int(BOT.split(":")[0])
OWN_ID_MESSAGE = "That is your bot's own ID, not your chat. Send your bot a message and use Find my chat ID."


def test_the_bots_own_id_is_refused_as_a_chat_id_whether_the_token_is_typed_now_or_already_saved(tmp_path):
    env, world = tmp_path / ".env", World()
    service = SetupService(FakeSupervisor(config.load_settings(env)), world.client, env_path=env)
    status, data = service.save({"values": {"TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": str(BOT_ID)}})  # both in one save
    assert status == 400 and data == {"ok": False, "error": "invalid", "field": "TELEGRAM_CHAT_ID", "message": OWN_ID_MESSAGE}
    assert not env.exists()  # a refused save writes nothing

    assert service.save({"values": {"TELEGRAM_BOT_TOKEN": BOT}})[0] == 200
    before = env.read_bytes()
    for typed in (str(BOT_ID), f"0{BOT_ID}", f" {BOT_ID} "):  # the token is already saved; spelling does not matter
        status, data = service.save({"values": {"TELEGRAM_CHAT_ID": typed}})
        assert status == 400 and data["field"] == "TELEGRAM_CHAT_ID" and data["message"] == OWN_ID_MESSAGE
    assert env.read_bytes() == before
    assert str(BOT_ID) not in json.dumps(data)  # the answer never contains the value


def test_a_real_chat_id_and_a_negative_id_that_only_looks_similar_are_still_saved(tmp_path):
    env, world = tmp_path / ".env", World()
    service = SetupService(FakeSupervisor(config.load_settings(env)), world.client, env_path=env)
    assert service.save({"values": {"TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": str(CHAT)}})[0] == 200
    assert service.save({"values": {"TELEGRAM_CHAT_ID": str(-BOT_ID)}})[0] == 200  # a group-style ID is a different number
    assert dotenv_values(env)["TELEGRAM_CHAT_ID"] == str(-BOT_ID)


def test_use_this_chat_refuses_an_id_that_is_the_bots_own(tmp_path):
    env, world = tmp_path / ".env", World()
    config.save_env({"TELEGRAM_BOT_TOKEN": BOT, "GITHUB_REPOS": "example-org/one"}, env_path=env)
    service = SetupService(FakeSupervisor(config.load_settings(env)), world.client, env_path=env)
    world.message(BOT_ID, name="Looks like me")  # an update whose chat is the bot's own number
    index = service.find_chat({})[1]["choices"][0]["index"]
    status, data = service.use_chat({"index": index})
    assert status == 400 and data["message"] == OWN_ID_MESSAGE
    assert "TELEGRAM_CHAT_ID" not in {k for k, v in dotenv_values(env).items() if v}


def test_a_403_on_the_test_message_mentions_the_bots_own_id(tmp_path):
    env, world = tmp_path / ".env", World()
    service = SetupService(FakeSupervisor(config.load_settings(env)), world.client, env_path=env)
    service.save({"values": {"TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": str(CHAT)}})
    world.telegram_status["sendMessage"] = 403
    status, data = service.send_test({})
    assert data["status"] == "http_403" and data["message"].startswith("NOT SENT (http_403): ")
    assert "press Start" in data["message"] and "bot's own ID" in data["message"] and "digits before the colon" in data["message"]
    assert str(CHAT) not in data["message"] and BOT not in data["message"]
