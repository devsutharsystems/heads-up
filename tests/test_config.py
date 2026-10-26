from pathlib import Path

from headsup.config import Settings, load_settings

ROOT = Path(__file__).resolve().parent.parent
FAKE = "sk-fake-not-a-real-key-123"


def test_loads_from_given_env_file_and_hides_value(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    env = tmp_path / ".env"
    env.write_text(f"ANTHROPIC_API_KEY={FAKE}\n")
    s = load_settings(env)
    assert s.anthropic_api_key == FAKE
    assert FAKE not in repr(s) and FAKE not in str(s)
    out = capsys.readouterr()
    assert FAKE not in out.out + out.err


def test_missing_key_gives_none(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert load_settings(tmp_path / "nothing.env") == Settings(anthropic_api_key=None)


def test_dotenv_is_loaded_in_one_place_only():
    sources = [p for d in ("src", "eval") for p in (ROOT / d).rglob("*.py")]
    hits = [p.relative_to(ROOT).as_posix() for p in sources if "load_dotenv(" in p.read_text()]
    assert hits == ["src/headsup/config.py"]
    # nothing else reads the environment or the .env file
    for p in sources:
        text = p.read_text()
        if p.name != "config.py":
            assert "os.environ" not in text and "getenv" not in text and '".env"' not in text


def test_new_settings_parse_and_hide_secrets(tmp_path, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_REPOS", "ICAL_URL"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("GITHUB_REPOS=example-org/a, example-org/b ,\nGITHUB_TOKEN=fake-gh-token\nICAL_URL=https://calendar.example.invalid/fake\n")
    s = load_settings(env)
    assert s.github_repos == ("example-org/a", "example-org/b")
    assert s.github_token == "fake-gh-token" and s.ical_url == "https://calendar.example.invalid/fake"
    assert "fake-gh-token" not in repr(s) and "calendar.example.invalid" not in repr(s)


def test_repos_default_to_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_REPOS", raising=False)
    assert load_settings(tmp_path / "nothing.env").github_repos == ()


def test_reminder_lead_defaults_to_30_and_parses(tmp_path, monkeypatch):
    monkeypatch.delenv("REMINDER_LEAD_MINUTES", raising=False)
    assert load_settings(tmp_path / "nothing.env").reminder_lead_minutes == 30
    env = tmp_path / ".env"
    env.write_text("REMINDER_LEAD_MINUTES=45\n")
    assert load_settings(env).reminder_lead_minutes == 45


def test_reminder_lead_rejects_bad_values(tmp_path, monkeypatch):
    import pytest
    for bad in ("soon", "0", "-5", "2881", "1.5"):
        monkeypatch.setenv("REMINDER_LEAD_MINUTES", bad)
        with pytest.raises(ValueError, match="REMINDER_LEAD_MINUTES"):
            load_settings(tmp_path / "nothing.env")


def test_decider_settings_defaults_and_parsing(tmp_path, monkeypatch):
    for k in ("DECIDER_FORMAT", "DECIDER_THRESHOLD", "DECIDER_HOST"):
        monkeypatch.delenv(k, raising=False)
    s = load_settings(tmp_path / "nothing.env")
    assert (s.decider_format, s.decider_threshold, s.decider_host) == ("F1", 0.35, "127.0.0.1:11434")
    env = tmp_path / ".env"
    env.write_text("DECIDER_FORMAT=F3\nDECIDER_THRESHOLD=0.5\nDECIDER_HOST=localhost:11436\n")
    s = load_settings(env)
    assert (s.decider_format, s.decider_threshold, s.decider_host) == ("F3", 0.5, "localhost:11436")


def test_decider_settings_reject_bad_values_naming_the_variable_only(tmp_path, monkeypatch):
    import pytest
    cases = {"DECIDER_FORMAT": ["F4", "f1", "yes"], "DECIDER_THRESHOLD": ["abc", "1.5", "-0.1", "nan"],
             "DECIDER_HOST": ["example.com:11434", "127.0.0.1", "10.0.0.5:11434", "127.0.0.1:port"]}
    for name, values in cases.items():
        for bad in values:
            monkeypatch.setenv(name, bad)
            with pytest.raises(ValueError, match=name) as err:
                load_settings(tmp_path / "nothing.env")
            fixed_text = str(err.value)  # fixed wording: allowed values may appear, a rejected value must not be echoed
            if bad not in ("127.0.0.1", "nan") and not any(bad in ok for ok in ("127.0.0.1:PORT or localhost:PORT", "F1, F2 or F3")):
                assert bad not in fixed_text
            monkeypatch.delenv(name)


def test_env_loaded_in_one_place_and_never_logged(tmp_path, monkeypatch, capsys, caplog):
    import logging
    from datetime import datetime, timezone
    from types import SimpleNamespace

    import httpx

    from headsup.costlog import CostLog
    from headsup.decider import Decider
    from headsup.schema import Decision, Event
    from headsup.store import Store
    from headsup.writer import Writer

    fake_key = "sk-fake-" + "env-test-98765"
    fake_token = "ghp_fake" + "token0000"
    fake_ical = "https://calendar.example.invalid/" + "secret-xyz"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text(f"ANTHROPIC_API_KEY={fake_key}\nGITHUB_TOKEN={fake_token}\nICAL_URL={fake_ical}\n")
    s = load_settings(env)
    now = datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc)
    e = Event(id="e1", source="github", kind="issue_opened", title="t", body="b", url="https://example.invalid/x",
              occurred_at=now, fetched_at=now, content_hash="h")

    class Failing:
        messages = SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError(f"bad {s.anthropic_api_key}")))

    with caplog.at_level(logging.DEBUG):
        w = Writer(Failing(), CostLog(Store(tmp_path / "t.db")))
        w.compose(Decision("e1", "interrupt", "r", now), e)
        d = Decider(httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x")))),
                    s.decider_host, s.decider_format)
        d.score(e)
    out = capsys.readouterr()
    haystack = caplog.text + out.out + out.err + repr(s) + str(s) + repr(w.error_flag)
    for secret in (fake_key, fake_token, fake_ical):
        assert secret not in haystack
    test_dotenv_is_loaded_in_one_place_only()  # the source scan: one load_dotenv call site, no other env reads
