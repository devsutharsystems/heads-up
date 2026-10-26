"""The ONLY place that loads .env. Values are never logged or printed."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV = ROOT / ".env"


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = field(default=None, repr=False)
    github_repos: tuple[str, ...] = ()
    github_token: str | None = field(default=None, repr=False)
    ical_url: str | None = field(default=None, repr=False)  # a secret address
    reminder_lead_minutes: int = 30  # the rubric's "soon"
    decider_format: str = "F1"
    decider_threshold: float = 0.35  # TEMPORARY: a placeholder until the main evaluation picks format and threshold
    decider_host: str = "127.0.0.1:11434"
    telegram_bot_token: str | None = field(default=None, repr=False)
    telegram_chat_id: int | None = field(default=None, repr=False)
    github_write_repos: tuple[str, ...] = ()
    github_actions_write_token: str | None = field(default=None, repr=False)  # separate token, one repo, Actions: write


def load_settings(env_path=DEFAULT_ENV) -> Settings:
    load_dotenv(env_path, override=False)
    repos = _repo_list(os.environ.get("GITHUB_REPOS"))
    return Settings(
        reminder_lead_minutes=_lead_minutes(os.environ.get("REMINDER_LEAD_MINUTES")),
        decider_format=_decider_format(os.environ.get("DECIDER_FORMAT")),
        decider_threshold=_decider_threshold(os.environ.get("DECIDER_THRESHOLD")),
        decider_host=_decider_host(os.environ.get("DECIDER_HOST")),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        github_repos=repos,
        github_write_repos=_repo_list(os.environ.get("GITHUB_WRITE_REPOS")),
        github_actions_write_token=os.environ.get("GITHUB_ACTIONS_WRITE_TOKEN") or None,
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=_chat_id(os.environ.get("TELEGRAM_CHAT_ID")),
        github_token=os.environ.get("GITHUB_TOKEN") or None,
        ical_url=os.environ.get("ICAL_URL") or None,
    )


def _lead_minutes(raw):
    if raw is None or not raw.strip():
        return 30
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("REMINDER_LEAD_MINUTES must be a whole number of minutes") from None
    if not 0 < value <= 48 * 60:
        raise ValueError("REMINDER_LEAD_MINUTES must be between 1 and 2880")
    return value


def _decider_format(raw):
    if raw is None or not raw.strip():
        return "F1"
    if raw.strip() not in ("F1", "F2", "F3"):
        raise ValueError("DECIDER_FORMAT must be F1, F2 or F3")
    return raw.strip()


def _decider_threshold(raw):
    if raw is None or not raw.strip():
        return 0.35  # TEMPORARY default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError("DECIDER_THRESHOLD must be a number between 0 and 1") from None
    if not 0.0 <= value <= 1.0:
        raise ValueError("DECIDER_THRESHOLD must be a number between 0 and 1")
    return value


def _decider_host(raw):
    if raw is None or not raw.strip():
        return "127.0.0.1:11434"
    host = raw.strip()
    name, sep, port = host.rpartition(":")
    if not sep or name not in ("127.0.0.1", "localhost") or not port.isdigit():
        raise ValueError("DECIDER_HOST must be 127.0.0.1:PORT or localhost:PORT")
    return host


def _repo_list(raw):
    return tuple(r.strip() for r in (raw or "").split(",") if r.strip())


def _chat_id(raw):
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValueError("TELEGRAM_CHAT_ID must be a whole number") from None
