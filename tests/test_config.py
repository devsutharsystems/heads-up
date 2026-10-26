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
