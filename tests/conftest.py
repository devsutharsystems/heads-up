import re
from pathlib import Path

import pytest

import headsup.config as config


@pytest.fixture(autouse=True)
def never_load_the_real_env_file(monkeypatch):
    """Tests use fake settings or a temp .env. If anything points load_dotenv at the project's real .env, fail loudly."""
    real_load = config.load_dotenv
    real_path = (config.ROOT / ".env").resolve()

    def guarded(path=None, *args, **kwargs):
        if path is not None and Path(path).resolve() == real_path:
            raise AssertionError("a test tried to load the real .env file")
        return real_load(path, *args, **kwargs)

    monkeypatch.setattr(config, "load_dotenv", guarded)

    # The settings page reads and writes the file too: none of those paths may ever reach the real .env from a test.
    real_values = config.dotenv_values

    def guarded_values(path=None, *args, **kwargs):
        if path is not None and Path(path).resolve() == real_path:
            raise AssertionError("a test tried to read the real .env file")
        return real_values(path, *args, **kwargs)

    monkeypatch.setattr(config, "dotenv_values", guarded_values)
    for name in ("_read_env_text", "_write_atomic"):
        original = getattr(config, name)

        def guard(path, *args, _original=original, _name=name, **kwargs):
            if Path(path).resolve() == real_path:
                raise AssertionError(f"a test tried to use the real .env file ({_name})")
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(config, name, guard)
    monkeypatch.setattr(config, "_FROM_FILE", set())  # which names an earlier load added: every test starts with none


def _setting_names():
    """Every variable name in .env.example (names only; the file has no values)."""
    text = (config.ROOT / ".env.example").read_text(encoding="utf-8")
    return re.findall(r"^([A-Z][A-Z0-9_]*)=", text, flags=re.MULTILINE)


@pytest.fixture(autouse=True)
def no_heads_up_variables_from_the_shell(monkeypatch):
    """A real shell may export Heads-Up settings. Remove them all so no test sees them; tests that need a value set it."""
    for name in _setting_names():
        monkeypatch.delenv(name, raising=False)
