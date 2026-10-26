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
