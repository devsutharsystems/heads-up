import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_NAMES = ["ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_REPOS", "GITHUB_WRITE_REPOS", "GITHUB_ACTIONS_WRITE_TOKEN",
             "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ICAL_URL", "REMINDER_LEAD_MINUTES",
             "DECIDER_FORMAT", "DECIDER_THRESHOLD", "DECIDER_HOST"]


def test_env_example_lists_every_name_with_empty_values():
    lines = [l for l in (ROOT / ".env.example").read_text().splitlines() if l.strip() and not l.startswith("#")]  # comments are allowed
    assert [l.split("=")[0] for l in lines] == ENV_NAMES
    assert all(l.endswith("=") for l in lines)


def test_env_example_comment_names_the_chosen_pair_and_its_source():
    text = (ROOT / ".env.example").read_text()
    assert "format F2 and threshold 0.40, chosen on tune days in the main evaluation (eval/EVAL_REPORT.md)" in text
    assert "TEMPORARY" not in text


def test_pyproject_pins_python_312_and_exact_versions():
    p = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert p["requires-python"] == ">=3.12,<3.13"
    deps = p["dependencies"] + p["optional-dependencies"]["dev"]
    assert all("==" in d for d in deps)
    names = {d.split("==")[0] for d in deps}
    assert {"httpx", "icalendar", "recurring-ical-events", "anthropic", "python-dotenv", "pytest", "detect-secrets"} <= names


def test_data_dir_is_gitignored():
    assert "data/" in (ROOT / ".gitignore").read_text().splitlines()


def test_the_launcher_is_executable_and_only_starts_the_dashboard():
    import os
    path = ROOT / "Heads-Up.command"
    text = path.read_text()
    assert os.access(path, os.X_OK) and text.startswith("#!/bin/bash")
    assert 'cd "$(dirname "$0")"' in text and "-m headsup --ui" in text and ".venv/bin/python" in text
    assert "http://127.0.0.1:8765/" in text and ".env" not in text and "sudo" not in text
