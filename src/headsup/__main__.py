"""Heads-Up command line.

  python -m headsup --once [--dry-run]       process taps, collect -> dedupe -> decide -> write -> send, exit
  python -m headsup --listen MINUTES         long-poll button taps (for the demo)
  python -m headsup --once --dry-run         everything except Telegram sends and GitHub writes; prints what would happen
  python -m headsup --ui                     local dashboard on 127.0.0.1:8765, timed checks, tap listener

In --once and --listen the Decider is started by you (see pipeline.ollama_start_command); Heads-Up only health-checks it.
Only --ui starts `ollama serve` itself (if nothing answers) and stops it on exit.
"""

import argparse
import atexit
import signal
import sys
import threading
import time
from datetime import datetime, timezone

import httpx

from headsup import pipeline
from headsup.actions import ActionHandler, DryRunWriter, GitHubWriter
from headsup.config import CHOSEN_FORMAT, CHOSEN_NOTE, CHOSEN_THRESHOLD, load_settings, setting_status
from headsup.costlog import CostLog
from headsup.decider import Decider
from headsup.haiku import build_client
from headsup.ollama_child import OllamaChild
from headsup.policy import Policy
from headsup.sources.github import GitHubSource
from headsup.sources.ical import IcalSource
from headsup.store import Store
from headsup.telegram import Telegram
from headsup.ui_runner import Redactor, Runner
from headsup.ui_server import HOST, PORT, Context, make_server
from headsup.writer import Writer


def positive_minutes(raw):
    value = float(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("MINUTES must be greater than 0")
    return value


def parse_args(argv):
    p = argparse.ArgumentParser(prog="headsup", description="A proactive assistant that interrupts only when it matters.")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="process taps, then collect -> dedupe -> decide -> write -> send, then exit")
    mode.add_argument("--listen", type=positive_minutes, metavar="MINUTES", help="long-poll button taps for this many minutes")
    mode.add_argument("--ui", action="store_true", help="local dashboard on 127.0.0.1:8765 with timed checks and the tap listener")
    p.add_argument("--dry-run", action="store_true", help="no Telegram sends, no GitHub writes, no Haiku call; print what would happen")
    return p.parse_args(argv)


def threshold_note(settings):
    """The label shown in decision reasons: only true while the app runs the pair chosen in the main evaluation."""
    if (settings.decider_format, settings.decider_threshold) == (CHOSEN_FORMAT, CHOSEN_THRESHOLD):
        return CHOSEN_NOTE
    return "format or threshold set in .env"


def build_app(settings, *, client, anthropic_client, now, dry_run, out, store, sleep=time.sleep, monotonic=time.monotonic):
    sources, unconfigured = [], []
    if settings.github_repos:
        sources.append(("github", GitHubSource(settings.github_repos, client, store, token=settings.github_token, now=now)))
    else:
        unconfigured.append(("github", "GITHUB_REPOS is empty"))
    if settings.ical_url:
        sources.append(("ical", IcalSource(settings.ical_url, client, store, now=now, lead_minutes=settings.reminder_lead_minutes)))
    else:
        unconfigured.append(("ical", "ICAL_URL is not set"))
    if anthropic_client is None and settings.anthropic_api_key and not dry_run:
        anthropic_client = build_client(settings.anthropic_api_key)
    app = pipeline.App(
        store=store, client=client, decider=Decider(client, settings.decider_host, settings.decider_format),
        policy=Policy(store, settings.decider_threshold, now=now, note=threshold_note(settings)),
        writer=Writer(anthropic_client, CostLog(store, now=now), dry_run=dry_run), sources=sources,
        unconfigured=unconfigured, key_missing=not settings.anthropic_api_key,
        write_repos=settings.github_write_repos, decider_host=settings.decider_host, dry_run=dry_run,
        now=now, out=out, sleep=sleep, monotonic=monotonic)
    if not dry_run:
        app.telegram = Telegram(client, settings.telegram_bot_token, settings.telegram_chat_id, store, sleep=sleep)
        app.actions = ActionHandler(store, app.telegram, GitHubWriter(client, settings.github_actions_write_token),
                                    settings.telegram_chat_id, settings.github_write_repos, now=now, out=out)
    return app


def run_ui(settings, *, out, port=PORT, client=None, db_path=None, now=None, install_signals=True):
    """The dashboard: HTTP server (main thread), one worker thread (checks and taps), and the Ollama child if needed."""
    now = now or (lambda: datetime.now(timezone.utc))
    if install_signals:  # a real run: show each line at once, also when the output goes to a file
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except AttributeError:
            pass
    own_client = client is None
    client = client or httpx.Client()
    open_store = (lambda: Store(db_path)) if db_path else Store
    telegram_ready = settings.telegram_bot_token is not None and settings.telegram_chat_id is not None

    def make_app(line_out):
        return build_app(settings, client=client, anthropic_client=None, now=now, dry_run=False, out=line_out, store=open_store())

    def make_preview_app(line_out):
        real = open_store()
        try:
            memory = real.copy_to_memory()
        finally:
            real.close()
        return build_app(settings, client=client, anthropic_client=None, now=now, dry_run=True, out=line_out, store=memory)

    secrets_known = (settings.anthropic_api_key, settings.github_token, settings.github_actions_write_token,
                     settings.telegram_bot_token, settings.ical_url)
    runner = Runner(make_app=make_app, make_preview_app=make_preview_app, telegram_ready=telegram_ready, open_store=open_store,
                    now=now, redact=Redactor(secrets_known))
    child = OllamaChild(client, settings.decider_host)
    ctx = Context(runner=runner, open_store=open_store, setup_status=setting_status, health=child.health, now=now)
    try:
        server = make_server(HOST, port, ctx)
    except OSError:
        out(f"error: port {port} is already in use (is Heads-Up already running?)")
        if own_client:
            client.close()
        return 2
    stop = threading.Event()
    atexit.register(child.stop)
    if install_signals:
        def _term(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, _term)
    out(f"Ollama: {child.ensure_running()}")
    worker = threading.Thread(target=runner.run_forever, args=(stop,), daemon=True)
    worker.start()
    out(f"Heads-Up dashboard: http://{HOST}:{server.server_address[1]}/  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        out("stopping")
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=15)
        child.stop()
        if own_client:
            client.close()
    return 0


def main(argv=None, *, settings=None, client=None, anthropic_client=None, now=None, out=print, store=None,
         sleep=time.sleep, monotonic=time.monotonic):
    args = parse_args(argv)
    try:
        settings = settings or load_settings()
    except ValueError as exc:
        out(f"configuration error: {exc}")
        return 2
    if args.ui:
        return run_ui(settings, out=out)
    if not args.dry_run:
        missing = [n for n, v in (("TELEGRAM_BOT_TOKEN", settings.telegram_bot_token),
                                  ("TELEGRAM_CHAT_ID", settings.telegram_chat_id)) if v is None]
        if missing:
            out(f"configuration error: {', '.join(missing)} must be set (or use --dry-run)")
            return 2
    own_client = client is None
    client = client or httpx.Client()
    store = store or Store()
    if args.dry_run:
        store = store.copy_to_memory()  # nothing a dry run does reaches the real database
    try:
        app = build_app(settings, client=client, anthropic_client=anthropic_client,
                        now=now or (lambda: datetime.now(timezone.utc)), dry_run=args.dry_run, out=out, store=store,
                        sleep=sleep, monotonic=monotonic)
        if args.once:
            pipeline.run_once(app)
            return 0
        return pipeline.listen(app, args.listen)
    finally:
        if own_client:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
