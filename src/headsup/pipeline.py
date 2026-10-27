"""One pass of the pipeline (--once) and the tap listener (--listen).

collect -> dedupe -> decide (Decider + policy) -> write (Haiku or template) -> send.
In --dry-run the store is an in-memory copy, nothing is sent to Telegram (not even getUpdates, which
would consume taps), nothing is written to GitHub, and Haiku is not called (its spend could not be
logged): the template text is shown with the input Haiku would have received.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from headsup.actions import LABELS, buttons_for, new_token
from headsup.ci_collapse import collapse_reasons
from headsup.dedupe import fresh
from headsup.sources.ical import IcalError
from headsup.state import build_input
from headsup.telegram import TelegramError, WebhookConflict
from headsup.writer import template_text


def ollama_start_command(host):
    """What Dev runs (Heads-Up only health-checks the server). Host is 127.0.0.1 only."""
    return (f'cd "<project folder>" && OLLAMA_HOST={host} OLLAMA_MODELS="$PWD/.cache/ollama" '
            "OLLAMA_KEEP_ALIVE=60s /opt/homebrew/bin/ollama serve")


@dataclass
class App:
    store: object
    client: object
    decider: object
    policy: object
    writer: object
    sources: list = field(default_factory=list)  # [(name, source)]
    unconfigured: list = field(default_factory=list)  # [(name, why)] sources that are not set up
    key_missing: bool = False
    telegram: object = None
    actions: object = None
    write_repos: tuple = ()
    decider_host: str = "127.0.0.1:11434"
    dry_run: bool = False
    now: object = lambda: datetime.now(timezone.utc)
    out: object = print
    sleep: object = time.sleep
    monotonic: object = time.monotonic


def decider_is_up(app):
    """Any HTTP answer from the server within 2 s counts as up; only no answer counts as down."""
    try:
        app.client.get(f"http://{app.decider_host}/", timeout=2.0)
        return True
    except httpx.HTTPError:
        return False


# ---------------------------------------------------------------- sending

def deliver(app, event, text, include_remind=True):
    """Save the alert, create fresh tokens, send. On a Telegram failure the alert stays pending (retried next run)."""
    store = app.store
    store.save_alert(event.content_hash, event.id, text)
    store.discard_unsent_tokens(event.content_hash)
    tokens = [(a, new_token()) for a in buttons_for(event, app.write_repos, include_remind)]
    for action, token in tokens:
        store.add_token(token, event.content_hash, action, app.now())
    try:
        message_id = app.telegram.send(text, [(LABELS[a], t) for a, t in tokens])
    except TelegramError as exc:
        app.out(f"warning: {exc}; the alert stays pending and is retried next run")
        return False
    store.attach_message(event.content_hash, message_id)
    store.mark_alert_sent(event.content_hash, app.now())
    return True


def _snoozed(app):
    until = app.store.snooze_until()
    return until is not None and app.now() < until


def send_due_reminders(app):
    """remind_later: one re-send per alert, 2 h after the tap. Not a decision, so not counted toward the daily limit.
    Held while snoozed."""
    sent = 0
    if _snoozed(app):
        return sent
    for event_hash in app.store.due_reminders(app.now()):
        event, alert = app.store.get_event(event_hash), app.store.get_alert(event_hash)
        if event is None or alert is None:
            app.store.mark_reminder_sent(event_hash, app.now())
            continue
        if deliver(app, event, alert[1], include_remind=False):
            app.store.mark_reminder_sent(event_hash, app.now())
            sent += 1
    return sent


def retry_pending(app):
    sent = 0
    if _snoozed(app):
        return sent
    for event_hash in app.store.pending_alerts():
        event, alert = app.store.get_event(event_hash), app.store.get_alert(event_hash)
        if event is not None and deliver(app, event, alert[1]):
            sent += 1
    return sent


# ---------------------------------------------------------------- one pass

def _process_taps(app, timeout):
    try:
        updates = app.telegram.get_updates(timeout=timeout)
    except WebhookConflict:
        app.out("error: a Telegram webhook is set, so getUpdates cannot run. Remove the webhook yourself (deleteWebhook); "
                "Heads-Up will not do it for you.")
        raise
    except TelegramError as exc:
        app.out(f"warning: {exc}")
        return []
    outcomes = app.actions.process_updates(updates)
    for outcome in outcomes:
        app.out(f"tap: {outcome}")
    return outcomes


def run_once(app):
    summary = {"collected": 0, "new": 0, "interrupt": 0, "log_only": 0, "sent": 0, "taps": 0}
    up = decider_is_up(app)
    if not up:
        app.out(f"Decider not reachable at {app.decider_host}: events are logged only. Start it with: "
                f"{ollama_start_command(app.decider_host)}")
    if app.dry_run:
        app.out("dry-run: Telegram is not polled and nothing is sent or written")
        app.out(app.writer.skipped)  # "Haiku skipped (dry-run)"
        if app.key_missing:
            app.out("note: no_api_key: ANTHROPIC_API_KEY is not set, so a real run would use template text")
    else:
        summary["taps"] = len(_process_taps(app, timeout=0))
        app.actions.sweep_expired()
        summary["sent"] += send_due_reminders(app) + retry_pending(app)

    events = []
    for name, why in app.unconfigured:
        app.out(f"{name}: not configured ({why})")
    for name, source in app.sources:  # one line per source, so a 0 is never ambiguous
        seen_warnings = len(getattr(source, "warnings", []))
        try:
            found = source.poll()
        except Exception as exc:  # noqa: BLE001 - one broken source must not stop the other
            detail = f"{type(exc).__name__}: {exc}" if isinstance(exc, IcalError) else type(exc).__name__  # never a URL
            app.out(f"{name}: error {detail}")
            continue
        events += found
        warnings = getattr(source, "warnings", [])[seen_warnings:]
        app.out(f"{name}: {len(found)} events" + (f" (warnings: {'; '.join(warnings)})" if warnings else ""))
    summary["collected"] = len(events)
    batch = fresh(sorted(events, key=lambda e: e.occurred_at), app.store)
    collapsed = collapse_reasons(batch, app.store)  # repeated or superseded CI failures: logged only, no Decider call
    for event in batch:
        summary["new"] += 1
        if event.content_hash in collapsed:
            decision = app.policy.log_only(event, collapsed[event.content_hash])
        else:
            probability = app.decider.score(event) if up else None
            decision = app.policy.decide(event, probability)
        summary[decision.verdict] += 1
        app.out(f"{decision.verdict}: {event.kind} {event.title!r} ({decision.reason})")
        if decision.verdict != "interrupt":
            continue
        if app.dry_run:
            actions = buttons_for(event, app.write_repos)
            app.out(f"  dry-run: would ask Haiku (claude-haiku-5-5) with: {build_input(event)}")
            app.out(f"  dry-run: would send (template text): {template_text(event)}  buttons: {', '.join(actions)}")
            continue
        alert = app.writer.compose(decision, event)
        if deliver(app, event, alert.text):
            summary["sent"] += 1
    if getattr(app.writer, "error_flag", None) and not app.dry_run:
        app.out(f"WARNING: {app.writer.error_flag['message']}")
    app.out("done: " + ", ".join(f"{k} {v}" for k, v in summary.items()))
    return summary


def listen(app, minutes):
    """Long-poll button taps for `minutes` (the demo mode). Returns 0 (also after Ctrl-C), or 1 if a webhook blocks polling."""
    if app.dry_run:
        app.out("dry-run: listening needs Telegram; nothing to do")
        return 0
    end = app.monotonic() + minutes * 60
    try:
        while (remaining := end - app.monotonic()) > 0:
            try:
                _process_taps(app, timeout=max(1, int(min(30, remaining))))
            except WebhookConflict:
                return 1
            app.actions.sweep_expired()
            send_due_reminders(app)
    except KeyboardInterrupt:  # Ctrl-C is the normal way to stop early: one line, exit code 0, no traceback
        app.out("stopped listening")
    return 0
