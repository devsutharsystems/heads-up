# heads-up: rules for every session

## Source of truth
- PLAN.md is the source of truth. Never change it without asking me first.
- If a fact is not confirmed from an official source, mark it UNVERIFIED.

## Scope and paths
- Work only inside this folder. Always quote paths (they contain spaces).

## Secrets
- Never open, print or copy .env or any secret.
- Only src/headsup/config.py loads .env.

## Forbidden commands
- Never run git commit, git push or sudo.
- Never install anything outside .venv. Ask before adding any dependency.

## Python and tests
- Use .venv/bin/python (Python 3.12) for everything.
- Tests: .venv/bin/python -m pytest
- Tests never touch the network: mock HTTP (GitHub, iCal, Telegram, Ollama, Anthropic).

## Workflow
- Plan before code. One small task at a time.
- After each task, stop and report: files changed (one line each, why), how to verify, what is left.

## Models
- Haiku: claude-haiku-5-5 only. Never make a real Anthropic API call without asking me first.
- Ollama: /opt/homebrew/bin/ollama, OLLAMA_MODELS=.cache/ollama, host 127.0.0.1 only.
