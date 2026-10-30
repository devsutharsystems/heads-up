"""Writing the settings file for the /setup page: atomic, mode 600, nothing echoed, nothing but the asked-for settings changed.
Every test uses a temp folder; conftest makes any use of the project's real .env fail."""

import json
import os
import stat
import threading

import pytest
from dotenv import dotenv_values

from headsup import config
from headsup.config import (EnvFileError, SettingError, env_form, reload_settings, required_missing, save_env, setup_needed,
                            shell_overrides, validate_value)

# Fake values, assembled at run time so no scanner sees a credential-shaped literal in this file.
BOT = ":".join(["123456789", "-".join(["AAH" + "x" * 8, "fake" + "y" * 20])])
READ_A = "".join(["fake", "-", "read", "-", "a1b2c3"])
KEY_A = "".join(["fake", "-", "anthropic", "-", "d4e5f6"])
WRITE_A = "".join(["fake", "-", "write", "-", "g7h8i9"])
FEED = "https://calendar.example.invalid/" + "".join(["private", "-", "feed", "-", "123abc"]) + "/basic.ics"
SECRET_VALUES = (BOT, READ_A, KEY_A, WRITE_A, "private-feed-123abc", "987654321")


def env_file(tmp_path):
    return tmp_path / ".env"


def modes(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def leftovers(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".env.") and p.name.endswith(".tmp"))


def full_save(path):
    return save_env({"TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": "987654321", "GITHUB_TOKEN": READ_A, "GITHUB_REPOS": "example-org/demo-repo",
                     "ICAL_URL": FEED, "ANTHROPIC_API_KEY": KEY_A, "GITHUB_ACTIONS_WRITE_TOKEN": WRITE_A}, env_path=path)


# ------------------------------------------------------------------------------------------ atomic, private writes
def test_a_first_save_starts_from_the_template_and_is_mode_600(tmp_path):
    path = env_file(tmp_path)
    assert save_env({"GITHUB_REPOS": "example-org/demo-repo"}, env_path=path) == ["GITHUB_REPOS"]
    text = path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "ANTHROPIC_API_KEY="
    assert "# Empty uses the defaults: format F2 and threshold 0.40" in text  # the template's comment came along
    assert "GITHUB_REPOS='example-org/demo-repo'" in text
    assert set(dotenv_values(path)) == set(config.SETTING_NAMES)
    assert modes(path) == 0o600
    assert leftovers(tmp_path) == []


def test_an_existing_file_with_loose_permissions_is_replaced_by_a_600_file(tmp_path):
    path = env_file(tmp_path)
    path.write_text("GITHUB_REPOS=\n", encoding="utf-8")
    path.chmod(0o644)
    save_env({"GITHUB_REPOS": "a/b"}, env_path=path)
    assert modes(path) == 0o600


def test_the_temp_file_is_private_from_the_moment_it_exists(tmp_path, monkeypatch):
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((os.path.basename(src), modes(src)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    save_env({"GITHUB_REPOS": "a/b"}, env_path=env_file(tmp_path))
    assert len(seen) == 1 and seen[0][1] == 0o600 and seen[0][0].startswith(".env.") and seen[0][0].endswith(".tmp")


def test_a_failure_at_the_rename_leaves_the_original_and_no_temp_file(tmp_path, monkeypatch):
    path = env_file(tmp_path)
    path.write_text("# mine\nGITHUB_REPOS='keep/me'\n", encoding="utf-8")
    before = path.read_bytes()

    def boom(src, dst):
        raise OSError("disk says no")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(EnvFileError) as err:
        save_env({"GITHUB_REPOS": "new/repo"}, env_path=path)
    assert "disk says no" not in str(err.value) and str(path) not in str(err.value)
    assert path.read_bytes() == before
    assert leftovers(tmp_path) == []


def test_a_failure_while_writing_leaves_the_original_and_no_temp_file(tmp_path, monkeypatch):
    path = env_file(tmp_path)
    path.write_text("GITHUB_REPOS='keep/me'\n", encoding="utf-8")
    before = path.read_bytes()

    def boom(fd):
        raise OSError("full")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(EnvFileError):
        save_env({"GITHUB_REPOS": "new/repo"}, env_path=path)
    assert path.read_bytes() == before and modes(path) == modes(path) and leftovers(tmp_path) == []


def test_the_target_folder_missing_is_a_clean_error(tmp_path):
    with pytest.raises(EnvFileError):
        save_env({"GITHUB_REPOS": "a/b"}, env_path=tmp_path / "nope" / ".env")


def test_a_settings_file_that_is_a_link_is_refused_and_the_target_untouched(tmp_path):
    target = tmp_path / "elsewhere.txt"
    target.write_text("GITHUB_REPOS='keep/me'\n", encoding="utf-8")
    link = env_file(tmp_path)
    link.symlink_to(target)
    with pytest.raises(EnvFileError):
        save_env({"GITHUB_REPOS": "a/b"}, env_path=link)
    assert target.read_text(encoding="utf-8") == "GITHUB_REPOS='keep/me'\n" and link.is_symlink()


# ------------------------------------------------------------------------------------------ what changes and what does not
def test_only_the_asked_for_settings_change_and_everything_else_stays_byte_for_byte(tmp_path):
    path = env_file(tmp_path)
    original = ("# my notes, do not touch\nFOO=bar baz\n\nexport GITHUB_REPOS=old/repo\nDECIDER_THRESHOLD=0.55\n"
                "DECIDER_HOST=localhost:11435\n# trailing comment\nTELEGRAM_CHAT_ID='111'\n")
    path.write_text(original, encoding="utf-8")
    assert save_env({"GITHUB_REPOS": "new/repo", "TELEGRAM_BOT_TOKEN": BOT}, env_path=path) == ["GITHUB_REPOS", "TELEGRAM_BOT_TOKEN"]
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[:3] == ["# my notes, do not touch", "FOO=bar baz", ""]
    assert lines[3] == "export GITHUB_REPOS='new/repo'", "the export prefix and the position are kept"
    assert lines[4:8] == ["DECIDER_THRESHOLD=0.55", "DECIDER_HOST=localhost:11435", "# trailing comment", "TELEGRAM_CHAT_ID='111'"]
    assert lines[8] == f"TELEGRAM_BOT_TOKEN='{BOT}'", "a name that was not there is appended"
    assert dotenv_values(path)["FOO"] == "bar baz"


def test_an_empty_or_missing_value_keeps_what_is_stored_and_clear_empties_it(tmp_path):
    path = env_file(tmp_path)
    full_save(path)
    assert save_env({"GITHUB_TOKEN": "", "ANTHROPIC_API_KEY": "   ", "ICAL_URL": None}, env_path=path) == []
    assert dotenv_values(path)["GITHUB_TOKEN"] == READ_A
    assert save_env(clear=["GITHUB_TOKEN"], env_path=path) == ["GITHUB_TOKEN"]
    values = dotenv_values(path)
    assert values["GITHUB_TOKEN"] == "" and values["ANTHROPIC_API_KEY"] == KEY_A
    assert "\nGITHUB_TOKEN=\n" in path.read_text(encoding="utf-8")


def test_a_value_and_a_clear_for_the_same_setting_is_refused(tmp_path):
    with pytest.raises(SettingError):
        save_env({"GITHUB_TOKEN": READ_A}, clear=["GITHUB_TOKEN"], env_path=env_file(tmp_path))


@pytest.mark.parametrize("values,clear", [
    ({"NOT_A_SETTING": "x"}, ()), ({"DECIDER_THRESHOLD": "0.9"}, ()), ({"DECIDER_FORMAT": "F1"}, ()), ({"DECIDER_HOST": "127.0.0.1:1"}, ()),
    ({}, ["DECIDER_THRESHOLD"]), ({}, ["NOT_A_SETTING"]), ({"GITHUB_REPOS": 5}, ()), ({"GITHUB_REPOS": ["a/b"]}, ()),
])
def test_unknown_read_only_and_wrongly_typed_settings_are_refused_and_nothing_is_written(tmp_path, values, clear):
    path = env_file(tmp_path)
    path.write_text("GITHUB_REPOS='keep/me'\nDECIDER_THRESHOLD=0.55\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(SettingError):
        save_env({**values, "GITHUB_TOKEN": READ_A}, clear=clear, env_path=path)
    assert path.read_bytes() == before, "one bad field means the good field is not written either"


def test_a_value_that_spans_lines_is_replaced_whole_and_leaves_no_stray_line(tmp_path):
    path = env_file(tmp_path)
    path.write_text('# keep\nGITHUB_REPOS="a/b\nc/d"\nGITHUB_TOKEN=keep\n', encoding="utf-8")
    save_env({"GITHUB_REPOS": "x/y"}, env_path=path)
    assert path.read_text(encoding="utf-8") == "# keep\nGITHUB_REPOS='x/y'\nGITHUB_TOKEN=keep\n"
    assert leftovers(tmp_path) == []


def test_a_file_without_a_final_newline_and_with_an_unparsable_line_keeps_both_lines(tmp_path):
    path = env_file(tmp_path)
    path.write_text("this line is not a setting\nGITHUB_TOKEN=keep", encoding="utf-8")
    save_env({"GITHUB_REPOS": "a/b"}, env_path=path)
    assert path.read_text(encoding="utf-8") == "this line is not a setting\nGITHUB_TOKEN=keep\nGITHUB_REPOS='a/b'\n"


def test_values_with_hash_quotes_backslashes_dollars_and_unicode_read_back_exactly(tmp_path):
    path = env_file(tmp_path)
    hostile = ["a#b", "it's", 'say"hi"', "back\\slash", "tail\\", "dollar$HOME", "ünï😀", "x=y", "'quoted'", "\\'"]
    for value in hostile:
        assert validate_value("ANTHROPIC_API_KEY", value) == value
        save_env({"ANTHROPIC_API_KEY": value}, env_path=path)
        assert dotenv_values(path, interpolate=False)["ANTHROPIC_API_KEY"] == value
        assert config.load_settings(path).anthropic_api_key == value
        for name in config.SETTING_NAMES:
            os.environ.pop(name, None)
        config._FROM_FILE.clear()


def test_a_value_that_dotenv_would_expand_is_refused(tmp_path):
    for value in ("${HOME}", "pre${X}post"):
        with pytest.raises(SettingError):
            save_env({"ANTHROPIC_API_KEY": value}, env_path=env_file(tmp_path))
    assert not env_file(tmp_path).exists()


def test_two_saves_at_once_both_land(tmp_path):
    path = env_file(tmp_path)
    errors = []

    def go(name, value):
        try:
            for _ in range(15):
                save_env({name: value}, env_path=path)
        except Exception as exc:  # noqa: BLE001 - collect, assert below
            errors.append(exc)

    threads = [threading.Thread(target=go, args=a) for a in (("GITHUB_REPOS", "a/b"), ("GITHUB_TOKEN", READ_A))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    values = dotenv_values(path)
    assert (values["GITHUB_REPOS"], values["GITHUB_TOKEN"]) == ("a/b", READ_A)
    assert leftovers(tmp_path) == [] and modes(path) == 0o600


# ------------------------------------------------------------------------------------------ the format checks
@pytest.mark.parametrize("name,good,stored", [
    ("TELEGRAM_BOT_TOKEN", BOT, BOT),
    ("TELEGRAM_CHAT_ID", "123456789", "123456789"), ("TELEGRAM_CHAT_ID", " -1001234567890 ", "-1001234567890"), ("TELEGRAM_CHAT_ID", "007", "7"),
    ("GITHUB_REPOS", "a/b", "a/b"), ("GITHUB_REPOS", "a/b,\nc/d.e-f_g  A/B", "a/b,c/d.e-f_g"), ("GITHUB_WRITE_REPOS", "o/r", "o/r"),
    ("ICAL_URL", FEED, FEED), ("REMINDER_LEAD_MINUTES", "30", "30"), ("REMINDER_LEAD_MINUTES", " 2880 ", "2880"),
    ("GITHUB_TOKEN", READ_A, READ_A), ("ANTHROPIC_API_KEY", KEY_A, KEY_A), ("GITHUB_ACTIONS_WRITE_TOKEN", WRITE_A, WRITE_A),
])
def test_good_values_are_accepted_and_normalised(name, good, stored):
    assert validate_value(name, good) == stored


@pytest.mark.parametrize("name,bad", [
    ("TELEGRAM_BOT_TOKEN", "not-a-token"), ("TELEGRAM_BOT_TOKEN", "123:short"), ("TELEGRAM_BOT_TOKEN", BOT + " x"), ("TELEGRAM_BOT_TOKEN", "abc:" + "x" * 30),
    ("TELEGRAM_CHAT_ID", "12ab"), ("TELEGRAM_CHAT_ID", "1.5"), ("TELEGRAM_CHAT_ID", "@name"),
    ("GITHUB_REPOS", "justname"), ("GITHUB_REPOS", "a/b/c"), ("GITHUB_REPOS", "a b/c"), ("GITHUB_REPOS", "https://github.com/a/b"), ("GITHUB_REPOS", ","),
    ("GITHUB_REPOS", ",".join(f"o/r{i}" for i in range(51))),
    ("ICAL_URL", "http://calendar.example.invalid/a.ics"), ("ICAL_URL", "https:///a.ics"), ("ICAL_URL", "ftp://calendar.example.invalid/a"),
    ("ICAL_URL", "https://" + "user" + ":" + "pw" + "@calendar.example.invalid/a.ics"), ("ICAL_URL", FEED + " x"), ("ICAL_URL", "https://a b/c"),
    ("ICAL_URL", "x" * 2001),
    ("REMINDER_LEAD_MINUTES", "0"), ("REMINDER_LEAD_MINUTES", "2881"), ("REMINDER_LEAD_MINUTES", "soon"), ("REMINDER_LEAD_MINUTES", "1.5"),
    ("GITHUB_TOKEN", "has space"), ("ANTHROPIC_API_KEY", "x" * 601), ("GITHUB_TOKEN", "line\nbreak"), ("GITHUB_TOKEN", "nul\x00byte"),
    ("GITHUB_TOKEN", "tab\there"), ("GITHUB_TOKEN", ""), ("GITHUB_TOKEN", "   "),
])
def test_bad_values_are_refused_naming_the_setting_only(name, bad):
    with pytest.raises(SettingError) as err:
        validate_value(name, bad)
    assert str(err.value) == f"invalid {name}" and err.value.name == name


def test_the_ical_rule_is_the_one_the_calendar_source_enforces():
    from headsup.sources.ical import IcalError, IcalSource
    for url in (FEED, "http://x.invalid/a", "https:///a", "https://" + "u" + ":" + "p" + "@x.invalid/a", "ftp://x.invalid/a"):
        try:
            IcalSource(url, None, None)
            source_ok = True
        except IcalError:
            source_ok = False
        try:
            validate_value("ICAL_URL", url)
            mine_ok = True
        except SettingError:
            mine_ok = False
        assert source_ok == mine_ok, url


def test_the_reminder_lead_rule_is_the_one_the_loader_enforces(tmp_path, monkeypatch):
    for raw in ("1", "30", "2880", "0", "2881", "x"):
        try:
            validate_value("REMINDER_LEAD_MINUTES", raw)
            mine = True
        except SettingError:
            mine = False
        try:
            config._lead_minutes(raw)
            theirs = True
        except ValueError:
            theirs = False
        assert mine == theirs, raw


# ------------------------------------------------------------------------------------------ what leaves config.py
def test_the_form_view_never_contains_a_key_or_the_chat_id_and_echoes_only_the_plain_settings(tmp_path):
    path = env_file(tmp_path)
    full_save(path)
    save_env({"GITHUB_WRITE_REPOS": "example-org/demo-repo", "REMINDER_LEAD_MINUTES": "45"}, env_path=path)
    form = env_form(path)
    text = json.dumps(form)
    for secret in SECRET_VALUES:
        assert secret not in text, secret
    by = {f["name"]: f for f in form["fields"]}
    assert [f["name"] for f in form["fields"]] == list(config.SETTING_NAMES)
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GITHUB_TOKEN", "ICAL_URL", "ANTHROPIC_API_KEY", "GITHUB_ACTIONS_WRITE_TOKEN"):
        assert by[name]["state"] == "set" and "value" not in by[name], name
    assert [(n, by[n]["value"]) for n in ("GITHUB_REPOS", "GITHUB_WRITE_REPOS", "REMINDER_LEAD_MINUTES")] == [
        ("GITHUB_REPOS", "example-org/demo-repo"), ("GITHUB_WRITE_REPOS", "example-org/demo-repo"), ("REMINDER_LEAD_MINUTES", "45")]
    assert sorted(n for n, f in by.items() if "value" in f) == ["GITHUB_REPOS", "GITHUB_WRITE_REPOS", "REMINDER_LEAD_MINUTES"]


def test_missing_keys_say_missing_and_the_judge_settings_say_default_or_custom_and_are_not_editable(tmp_path):
    path = env_file(tmp_path)
    path.write_text("DECIDER_THRESHOLD=0.55\n", encoding="utf-8")
    by = {f["name"]: f for f in env_form(path)["fields"]}
    assert by["GITHUB_TOKEN"]["state"] == "missing" and by["TELEGRAM_BOT_TOKEN"]["state"] == "missing"
    assert [by[n]["state"] for n in ("DECIDER_FORMAT", "DECIDER_THRESHOLD", "DECIDER_HOST")] == ["default", "custom", "default"]
    assert all(not by[n]["editable"] and by[n]["kind"] == "readonly" for n in ("DECIDER_FORMAT", "DECIDER_THRESHOLD", "DECIDER_HOST"))
    assert "0.55" not in json.dumps(env_form(path)), "even the judge's custom value is not echoed"
    assert not env_form(tmp_path / "nothing-here")["file_exists"]


def test_required_settings_and_first_run(tmp_path):
    path = env_file(tmp_path)
    assert setup_needed(path) and len(required_missing(path)) == 3
    save_env({"TELEGRAM_BOT_TOKEN": BOT}, env_path=path)
    assert required_missing(path) == ["Telegram chat ID", "A GitHub repository or a calendar address"]
    save_env({"TELEGRAM_CHAT_ID": "5"}, env_path=path)
    assert setup_needed(path)
    save_env({"ICAL_URL": FEED}, env_path=path)
    assert not setup_needed(path) and required_missing(path) == []
    save_env({"GITHUB_REPOS": "a/b"}, clear=["ICAL_URL"], env_path=path)
    assert not setup_needed(path)


def test_the_fields_cover_every_setting_once_and_have_help_text(tmp_path):
    assert [f.name for f in config.FIELDS] == list(config.SETTING_NAMES)
    assert all(f.label and f.help and f.group for f in config.FIELDS)
    assert {f.name for f in config.FIELDS if f.required} == {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}


# ------------------------------------------------------------------------------------------ reloading after a save
def test_reload_picks_up_a_saved_value_that_a_plain_second_load_would_ignore(tmp_path):
    path = env_file(tmp_path)
    save_env({"GITHUB_REPOS": "old/repo", "GITHUB_TOKEN": READ_A}, env_path=path)
    first = config.load_settings(path)
    assert first.github_repos == ("old/repo",)
    save_env({"GITHUB_REPOS": "new/repo"}, env_path=path)
    assert config.load_settings(path).github_repos == ("old/repo",), "load_dotenv(override=False) keeps what is already in the process"
    second = reload_settings(path)
    assert second.github_repos == ("new/repo",) and second.github_token == READ_A
    assert config.setting_status()["GITHUB_REPOS"] is True


def test_reload_clears_a_value_that_was_removed_from_the_file(tmp_path):
    path = env_file(tmp_path)
    save_env({"GITHUB_TOKEN": READ_A}, env_path=path)
    assert config.load_settings(path).github_token == READ_A
    save_env(clear=["GITHUB_TOKEN"], env_path=path)
    assert reload_settings(path).github_token is None
    assert config.setting_status()["GITHUB_TOKEN"] is False


def test_reload_never_touches_a_value_the_shell_exported(tmp_path, monkeypatch, capsys):
    path = env_file(tmp_path)
    monkeypatch.setenv("GITHUB_REPOS", "shell/repo")
    save_env({"GITHUB_REPOS": "file/repo", "GITHUB_TOKEN": READ_A}, env_path=path)
    config.load_settings(path)
    capsys.readouterr()
    save_env({"GITHUB_TOKEN": READ_A + "2"}, env_path=path)
    settings = reload_settings(path)
    assert settings.github_repos == ("shell/repo",) and settings.github_token == READ_A + "2"
    assert os.environ["GITHUB_REPOS"] == "shell/repo"
    assert shell_overrides(path) == ["GITHUB_REPOS"], "names only: the page can say the shell takes priority"
    assert "shell/repo" not in json.dumps(env_form(path)["shell_overrides"]) and "file/repo" not in json.dumps(env_form(path)["shell_overrides"])
    by = {f["name"]: f for f in env_form(path)["fields"]}
    assert by["GITHUB_REPOS"]["from_shell"] is True and by["GITHUB_TOKEN"]["from_shell"] is False


def test_a_bad_file_makes_reload_fail_and_puts_the_process_back_as_it_was(tmp_path):
    path = env_file(tmp_path)
    save_env({"GITHUB_REPOS": "good/repo", "GITHUB_TOKEN": READ_A}, env_path=path)
    config.load_settings(path)
    path.write_text("GITHUB_REPOS='changed/by-hand'\nREMINDER_LEAD_MINUTES=banana\n", encoding="utf-8")
    with pytest.raises(ValueError) as err:
        reload_settings(path)
    assert "banana" not in str(err.value) and "REMINDER_LEAD_MINUTES" in str(err.value)
    assert os.environ["GITHUB_REPOS"] == "good/repo" and os.environ["GITHUB_TOKEN"] == READ_A
    path.write_text("GITHUB_REPOS='fixed/repo'\n", encoding="utf-8")
    assert reload_settings(path).github_repos == ("fixed/repo",), "still reloadable afterwards: the bookkeeping was restored too"


def test_no_other_source_file_can_touch_the_settings_file():
    """The existing rule (only config.py reads or writes .env) still holds with the new modules."""
    for path in sorted((config.ROOT / "src").rglob("*.py")) + sorted((config.ROOT / "eval").rglob("*.py")):
        if path.name == "config.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "os.environ" not in text and "getenv" not in text and '".env"' not in text and "load_dotenv(" not in text, path.name
        assert "dotenv" not in text, path.name


def test_leftover_temp_files_are_ignored_by_git():
    ignore = (config.ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignore and ".env.*.tmp" in ignore
