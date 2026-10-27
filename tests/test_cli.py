import logging
from datetime import timedelta

import pytest

from headsup import pipeline
from headsup.__main__ import main, parse_args
from headsup.config import Settings
from headsup.policy import Policy
from headsup.pipeline import ollama_start_command
from world import (BOT, CHAT, ICAL_URL, NOW, READ_TOKEN, REPO, WRITE_TOKEN, Clock, FakeAnthropic, World, ics_for, settings,
                   store_at)

SCORES = {"Kind: ci_failed. Title: CI failed: CI on main": 0.9, "Kind: release_published": 0.5, "Kind: event_upcoming": 0.8}


def world(**kw):
    return World(scores=SCORES, calendar=ics_for(NOW + timedelta(minutes=20)), **kw)


def run(argv, w, store, *, clock=None, haiku=None, out=None, cfg=None, **kw):
    out = [] if out is None else out
    code = main(argv, settings=cfg or settings(), client=w.client, anthropic_client=FakeAnthropic() if haiku is None else (None if haiku is False else haiku), now=clock or Clock(),
                out=out.append, store=store, sleep=lambda s: None, **kw)
    return code, out


# ---------------------------------------------------------------- arguments and configuration

def test_exactly_one_of_once_or_listen_is_required():
    for argv in ([], ["--dry-run"], ["--once", "--listen", "5"]):
        with pytest.raises(SystemExit):
            parse_args(argv)
    for bad in ("0", "-3", "abc"):
        with pytest.raises(SystemExit):
            parse_args(["--listen", bad])
    assert parse_args(["--listen", "0.5"]).listen == 0.5 and parse_args(["--once", "--dry-run"]).dry_run


def test_missing_telegram_settings_name_the_variables(tmp_path):
    w = world()
    code, out = run(["--once"], w, store_at(tmp_path), cfg=Settings())
    assert code == 2 and "TELEGRAM_BOT_TOKEN" in out[0] and "TELEGRAM_CHAT_ID" in out[0]
    assert w.calls == [] and w.unexpected == []


def test_bad_config_is_reported_by_name_not_value(tmp_path, monkeypatch):
    import headsup.__main__ as cli
    from headsup.config import load_settings
    # Point the loader at a temp file: a test must never load the project's real .env.
    monkeypatch.setattr(cli, "load_settings", lambda: load_settings(tmp_path / "nothing.env"))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "not-a-number")
    out = []
    assert main(["--once"], client=world().client, out=out.append, store=store_at(tmp_path)) == 2
    assert "TELEGRAM_CHAT_ID" in out[0] and "not-a-number" not in out[0]


# ---------------------------------------------------------------- one full --once pass

def test_once_end_to_end_collect_dedupe_decide_write_send_then_taps(tmp_path):
    w, store, clock, haiku, out = world(), store_at(tmp_path), Clock(), FakeAnthropic(), []
    code, _ = run(["--once"], w, store, clock=clock, haiku=haiku, out=out)
    assert code == 0 and w.unexpected == []

    # 6 events collected (release, issue, PR, two CI failures, one calendar reminder); 3 pass the threshold
    decisions = store.decisions()
    assert len(decisions) == 6 and sorted(d[1] for d in decisions) == ["interrupt"] * 3 + ["log_only"] * 3
    assert store.seen_count() == 6 and len(w.decider_requests) == 6
    assert out[-1] == "done: collected 6, new 6, interrupt 3, log_only 3, sent 3, taps 0"

    # Haiku wrote each interrupt from the minimal input only, and the real usage was logged
    assert len(haiku.calls) == 3
    for call in haiku.calls:
        content = call["messages"][0]["content"]
        assert "Source: " in content and "BODY" not in content and call["thinking"] == {"type": "disabled"}
    from headsup.costlog import CostLog
    assert CostLog(store, now=clock).day_total() > 0

    # Telegram: three plain-text messages with buttons chosen by code; rerun only on the main-branch CI failure
    sent = w.sent()
    assert len(sent) == 3 and {p["chat_id"] for p in sent} == {CHAT} and all("parse_mode" not in p for p in sent)
    assert all(p["text"] == "Heads up: take a look." for p in sent)
    labels = [[b["text"] for b in p["reply_markup"]["inline_keyboard"][0]] for p in sent]
    assert sum("Re-run CI" in row for row in labels) == 1 and all(row[:2] == ["Snooze 1h", "Remind in 2h"] for row in labels)
    assert w.github_writes == []

    # A second pass sees nothing new: ETags answer 304, the calendar reminder was given, nothing is sent twice
    w.calls.clear()
    code, out2 = run(["--once"], w, store, clock=clock, haiku=haiku)
    assert w.sent() == [] and len(haiku.calls) == 3 and "new 0" in out2[-1] and len(store.decisions()) == 6

    # Dev taps Snooze on the first alert; the next pass handles the tap before collecting
    w.tap(w_token := next(b["callback_data"] for p in sent for b in p["reply_markup"]["inline_keyboard"][0] if b["text"] == "Snooze 1h"),
          message_id=100)
    code, out3 = run(["--once"], w, store, clock=clock, haiku=haiku)
    assert "tap: done" in out3 and "taps 1" in out3[-1]
    assert "answerCallbackQuery" in w.methods() and "editMessageReplyMarkup" in w.methods()
    assert store.snooze_until() == clock.now + timedelta(hours=1)
    assert w_token not in "".join(out + out2 + out3)


def test_taps_are_processed_before_new_events_are_collected(tmp_path):
    w, store = world(), store_at(tmp_path)
    run(["--once"], w, store)
    w.calls.clear()
    run(["--once"], w, store)
    assert w.methods()[0] == "getUpdates"


# ---------------------------------------------------------------- dry run

def test_dry_run_makes_no_sends_and_no_writes_and_leaves_the_database_alone(tmp_path):
    w, store, haiku, out = world(), store_at(tmp_path), FakeAnthropic(), []
    cfg = settings(telegram_bot_token=None, telegram_chat_id=None)  # dry-run needs no Telegram settings
    code, _ = run(["--once", "--dry-run"], w, store, haiku=haiku, out=out, cfg=cfg)
    assert code == 0 and w.unexpected == []
    assert w.calls == [] and w.github_writes == [] and haiku.calls == []  # no Telegram call of any kind, no write, no Haiku
    assert store.seen_count() == 0 and store.decisions() == [] and store.get_kv("telegram_offset") is None
    text = "\n".join(out)
    assert text.count("dry-run: would send") == 3 and "would ask Haiku (claude-haiku-5-5) with: Source: " in text
    assert "buttons: snooze_1h, remind_later, rerun_failed_ci" in text
    # nothing was consumed: a real pass afterwards still sends the three alerts
    run(["--once"], w, store)
    assert len(w.sent()) == 3


def test_dry_run_does_not_poll_telegram_or_listen(tmp_path):
    w, store = world(), store_at(tmp_path)
    w.tap("0123456789abcdef")
    code, out = run(["--listen", "1", "--dry-run"], w, store)
    assert code == 0 and w.calls == [] and any("nothing to do" in line for line in out)


# ---------------------------------------------------------------- failures never lose or invent alerts

def test_decider_down_logs_only_prints_one_hint_and_calls_nothing_else(tmp_path):
    w, store, haiku = world(), store_at(tmp_path), FakeAnthropic()
    w.decider_up = False
    code, out = run(["--once"], w, store, haiku=haiku)
    assert code == 0 and w.sent() == [] and haiku.calls == [] and w.decider_requests == []
    assert [d[1] for d in store.decisions()] == ["log_only"] * 6 and all("unavailable" in d[2] for d in store.decisions())
    hints = [line for line in out if "Decider not reachable" in line]
    assert len(hints) == 1 and "/opt/homebrew/bin/ollama serve" in hints[0] and "OLLAMA_HOST=127.0.0.1:11434" in hints[0]


def test_ollama_start_command_is_local_only_and_uses_the_project_model_folder():
    cmd = ollama_start_command("127.0.0.1:11434")
    assert cmd == ('cd "<project folder>" && OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS="$PWD/.cache/ollama" '
                   "OLLAMA_KEEP_ALIVE=60s /opt/homebrew/bin/ollama serve")


def test_a_failed_send_stays_pending_and_is_retried_next_run(tmp_path):
    w, store = world(), store_at(tmp_path)
    w.telegram_fails = True
    code, out = run(["--once"], w, store)
    assert code == 0 and any("stays pending" in line for line in out) and len(store.pending_alerts()) == 3
    w.telegram_fails = False
    w.calls.clear()
    run(["--once"], w, store)
    assert len(w.sent()) == 3 and store.pending_alerts() == [] and len(store.decisions()) == 6  # no new decisions, no duplicates


def test_one_failing_source_does_not_stop_the_other_and_no_url_is_printed(tmp_path):
    w = world()
    w.calendar = None  # the calendar host now raises an unexpected-host assertion inside the transport

    class Boom(Exception):
        pass

    def broken(request):
        if request.url.host == "calendar.example.invalid":
            raise Boom(f"cannot read {request.url}")
        return orig(request)

    import httpx
    orig = w.__call__
    client = httpx.Client(transport=httpx.MockTransport(broken))
    out = []
    code = main(["--once"], settings=settings(), client=client, anthropic_client=FakeAnthropic(), now=Clock(), out=out.append,
                store=store_at(tmp_path), sleep=lambda s: None)
    assert code == 0 and "ical: error Boom" in out
    assert not any("secret-address" in line for line in out) and any("collected 5" in line for line in out)


# ---------------------------------------------------------------- per-source lines, dry-run wording, timeouts

def test_every_run_prints_one_line_per_source(tmp_path):
    w, store = world(), store_at(tmp_path)
    _, out = run(["--once"], w, store)
    assert "github: 5 events" in out and "ical: 1 events" in out
    _, out2 = run(["--once"], w, store)
    assert "github: 0 events" in out2 and "ical: 0 events" in out2  # a real, explained zero (304 and already reminded)


def test_a_failing_source_shows_its_error_and_the_other_still_reports(tmp_path):
    import httpx
    w = world()
    w.github_raise = httpx.ConnectTimeout("timed out")
    _, out = run(["--once"], w, store_at(tmp_path))
    assert "github: error ConnectTimeout" in out and "ical: 1 events" in out
    assert len([p for p in w.github_reads]) == 2  # one retry on a connect timeout, then the error is reported


def test_calendar_errors_show_the_message_but_never_the_address(tmp_path):
    w = world()
    w.calendar_status = 500
    _, out = run(["--once"], w, store_at(tmp_path))
    assert "ical: error IcalError: calendar fetch failed: HTTP 500" in out and "github: 5 events" in out
    assert not any("secret-address" in line or "calendar.example.invalid" in line for line in out)


def test_unconfigured_sources_are_reported_not_silent(tmp_path):
    _, out = run(["--once"], world(), store_at(tmp_path), cfg=settings(github_repos=(), ical_url=None))
    assert "github: not configured (GITHUB_REPOS is empty)" in out and "ical: not configured (ICAL_URL is not set)" in out
    assert not any(line.startswith(("github: 0", "ical: 0")) for line in out)


def test_github_warnings_are_shown_next_to_the_count(tmp_path):
    w = world()
    w.github_status[f"/repos/{REPO}/releases"] = 500
    _, out = run(["--once"], w, store_at(tmp_path))
    line = next(l for l in out if l.startswith("github: "))
    assert line.startswith("github: 4 events (warnings: ") and "releases: HTTP 500" in line


def test_dry_run_says_haiku_skipped_and_not_no_api_key(tmp_path, caplog):
    w, store, out = world(), store_at(tmp_path), []
    with caplog.at_level(logging.DEBUG):
        run(["--once", "--dry-run"], w, store, out=out)  # settings() has an API key
    assert "Haiku skipped (dry-run)" in out
    assert not any("no_api_key" in line for line in out) and "no_api_key" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_no_api_key_is_shown_only_when_the_key_is_really_missing(tmp_path):
    # dry run without a key: skipped AND a note that the key is missing
    _, out = run(["--once", "--dry-run"], world(), store_at(tmp_path), cfg=settings(anthropic_api_key=None))
    assert "Haiku skipped (dry-run)" in out and any("no_api_key" in line for line in out)
    # real run without a key: template text and the flag for Dev
    w = world()
    _, out2 = run(["--once"], w, store_at(tmp_path / "b"), cfg=settings(anthropic_api_key=None), haiku=False)
    assert len(w.sent()) == 3 and all("Heads up" not in p["text"] for p in w.sent())  # template text, not Haiku
    assert any(line.startswith("WARNING: No Anthropic API key is configured") for line in out2)


def test_github_and_calendar_requests_use_connect_10_read_15(tmp_path):
    w = world()
    run(["--once"], w, store_at(tmp_path))
    assert w.timeouts and all(t == {"connect": 10.0, "read": 15.0, "write": 15.0, "pool": 15.0} for t in w.timeouts)


def test_writer_errors_are_flagged_for_dev(tmp_path):
    class Auth(Exception):
        status_code = 401

    class Broken:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kw):
            raise Auth("bad key")

    w, store = world(), store_at(tmp_path)
    code, out = run(["--once"], w, store, haiku=Broken())
    assert len(w.sent()) == 3 and all("CI failed" in p["text"] or ":" in p["text"] for p in w.sent())  # template text still sent
    assert any(line.startswith("WARNING: Haiku writer stopped (auth)") and "Prateek" in line for line in out)


# ---------------------------------------------------------------- listen

def test_listen_long_polls_handles_taps_and_stops_after_the_time(tmp_path):
    w, store, clock = world(), store_at(tmp_path), Clock()
    run(["--once"], w, store, clock=clock)
    token = next(b["callback_data"] for p in w.sent() for b in p["reply_markup"]["inline_keyboard"][0] if b["text"] == "Snooze 1h")
    w.tap(token, message_id=100)
    ticks = iter(range(0, 1000, 20))  # a fake monotonic clock moving 20 s per reading
    w.calls.clear()
    code, out = run(["--listen", "1"], w, store, clock=clock, monotonic=lambda: next(ticks))
    assert code == 0 and "tap: done" in out and store.snooze_until() is not None
    polls = [p for m, p in w.calls if m == "getUpdates"]
    assert polls and all(1 <= p["timeout"] <= 30 for p in polls)


def test_ctrl_c_while_listening_stops_with_one_line_and_exit_code_0(tmp_path, monkeypatch, capsys):
    w, store = world(), store_at(tmp_path)

    def interrupted(request):
        raise KeyboardInterrupt  # what Ctrl-C raises inside the long poll

    monkeypatch.setattr(w, "_telegram", interrupted)
    ticks = iter(range(0, 1000, 20))
    code, out = run(["--listen", "10"], w, store, monotonic=lambda: next(ticks))
    assert code == 0 and out == ["stopped listening"]
    assert "Traceback" not in "".join(capsys.readouterr())


def test_listen_stops_with_a_clear_message_when_a_webhook_is_set(tmp_path):
    w, store = world(), store_at(tmp_path)
    w.webhook_set = True
    ticks = iter(range(0, 1000, 20))
    code, out = run(["--listen", "1"], w, store, monotonic=lambda: next(ticks))
    assert code == 1 and any("webhook" in line and "will not do it for you" in line for line in out)
    assert "deleteWebhook" not in w.methods()


def test_secrets_never_reach_output_or_logs(tmp_path, caplog):
    w, store, out = world(), store_at(tmp_path), []
    with caplog.at_level(logging.DEBUG):
        run(["--once"], w, store, out=out)
    text = caplog.text + "\n".join(out)
    for secret in (BOT, "FAKE-bot-token", WRITE_TOKEN, READ_TOKEN, ICAL_URL, "secret-address-made-up", "fake-anthropic-key"):
        assert secret not in text


def test_decision_reasons_carry_the_chosen_note_only_while_the_app_runs_the_chosen_pair(tmp_path):
    from headsup.__main__ import build_app, threshold_note
    from headsup.config import CHOSEN_NOTE
    chosen = settings(decider_format="F2", decider_threshold=0.40)
    assert threshold_note(chosen) == CHOSEN_NOTE
    for other in (settings(decider_format="F1", decider_threshold=0.40), settings(decider_format="F2", decider_threshold=0.35)):
        assert threshold_note(other) == "format or threshold set in .env"
    app = build_app(chosen, client=world().client, anthropic_client=None, now=Clock(), dry_run=True, out=lambda s: None,
                    store=store_at(tmp_path), sleep=lambda s: None)
    assert app.policy.threshold == 0.40 and app.decider.fmt == "F2" and app.policy._note.endswith(CHOSEN_NOTE)
