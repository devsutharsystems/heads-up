"""Haiku writes the alert text, only for events that already passed the interrupt decision and the daily limit.

Rules (PLAN.md 8b): model claude-haiku-5-5 only; thinking disabled; no sampling params; the model sees
build_input(event) only (type, title, time phrase). One failure -> template text for that message.
Two failures in a row, or any auth or quota error -> no more Haiku calls this run, template text, and
`error_flag` is set for Dev. No retries, no other model or key. The LLM only writes text: buttons come
from code (PLAN.md section 8).
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from headsup.haiku import HAIKU_MODEL, classify_error, request_kwargs
from headsup.state import build_input, time_phrase

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "writer.txt"
MAX_TOKENS = 100
FAILURES_TO_STOP = 2
TEXT_MAX = 300
LABELS = {
    "release_published": "New release", "issue_opened": "New issue", "pr_opened": "New PR",
    "ci_failed": "CI failed", "ci_recovered": "CI recovered", "event_upcoming": "Starting soon",
    "event_moved": "Event moved", "event_cancelled": "Event cancelled",
}

log = logging.getLogger(__name__)


@dataclass
class Alert:
    text: str
    source: str  # "haiku" or "template"


def template_text(event):
    return f"{LABELS[event.kind]}: {event.title}. {time_phrase(event)}."


class Writer:
    def __init__(self, client, costlog, model=HAIKU_MODEL, prompt_path=PROMPT_PATH, dry_run=False):
        if model != HAIKU_MODEL:
            raise ValueError(f"only {HAIKU_MODEL} is allowed")
        self.client = client
        self.costlog = costlog
        self.model = model
        self.prompt = Path(prompt_path).read_text(encoding="utf-8").rstrip("\n")
        self.error_flag = None  # {"reason": ..., "message": ...} once Haiku has been switched off
        self._failures = 0
        self.skipped = "Haiku skipped (dry-run)" if dry_run else None  # not an error: a dry run never calls Haiku
        if dry_run:
            return
        if client is None:
            self._stop("no_api_key", "No Anthropic API key is configured; alerts use template text.")

    def _stop(self, reason, message):
        self.error_flag = {"reason": reason, "message": message}
        log.error("Haiku writer off: %s", reason)  # reason only, never request or key details

    def _fail(self, reason):
        self._failures += 1
        if reason in ("auth", "quota") or self._failures >= FAILURES_TO_STOP:
            why = reason if reason in ("auth", "quota") else "repeated_failures"
            self._stop(why, f"Haiku writer stopped ({why}): alerts now use template text. Tell Prateek.")

    def compose(self, decision, event):
        """Alert text for an interrupt decision. Returns None, with no API call, for anything else."""
        if decision.verdict != "interrupt":
            return None
        if self.error_flag is not None or self.skipped:
            return Alert(template_text(event), "template")
        prompt = self.prompt.replace("{event}", build_input(event))
        try:
            resp = self.client.messages.create(**request_kwargs(prompt, MAX_TOKENS))
        except Exception as exc:  # noqa: BLE001 - any SDK or network error is a failed call
            self._fail(classify_error(exc))
            return Alert(template_text(event), "template")
        if getattr(resp, "usage", None) is not None:
            self.costlog.record("runtime", self.model, resp.usage)
        text = " ".join("".join(b.text for b in resp.content if getattr(b, "type", None) == "text").split())
        if not text or getattr(resp, "stop_reason", None) == "refusal":
            self._fail("other")
            return Alert(template_text(event), "template")
        self._failures = 0
        return Alert(text[:TEXT_MAX], "haiku")
