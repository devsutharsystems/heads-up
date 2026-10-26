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
