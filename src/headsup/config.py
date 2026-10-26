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


def load_settings(env_path=DEFAULT_ENV) -> Settings:
    load_dotenv(env_path, override=False)
    return Settings(anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None)
