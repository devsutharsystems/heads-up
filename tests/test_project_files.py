import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_NAMES = ["ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_REPOS", "GITHUB_WRITE_REPOS", "GITHUB_ACTIONS_WRITE_TOKEN",
             "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ICAL_URL", "REMINDER_LEAD_MINUTES",
             "DECIDER_FORMAT", "DECIDER_THRESHOLD", "DECIDER_HOST"]


def test_env_example_lists_every_name_with_empty_values():
    lines = [l for l in (ROOT / ".env.example").read_text().splitlines() if l.strip()]
    assert [l.split("=")[0] for l in lines] == ENV_NAMES
    assert all(l.endswith("=") for l in lines)


def test_pyproject_pins_python_312_and_exact_versions():
    p = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert p["requires-python"] == ">=3.12,<3.13"
    deps = p["dependencies"] + p["optional-dependencies"]["dev"]
    assert all("==" in d for d in deps)
    names = {d.split("==")[0] for d in deps}
    assert {"httpx", "icalendar", "recurring-ical-events", "anthropic", "python-dotenv", "pytest", "detect-secrets"} <= names


def test_data_dir_is_gitignored():
    assert "data/" in (ROOT / ".gitignore").read_text().splitlines()
