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


def load_settings(env_path=DEFAULT_ENV) -> Settings:
    load_dotenv(env_path, override=False)
    repos = tuple(r.strip() for r in os.environ.get("GITHUB_REPOS", "").split(",") if r.strip())
    return Settings(
        reminder_lead_minutes=_lead_minutes(os.environ.get("REMINDER_LEAD_MINUTES")),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        github_repos=repos,
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
