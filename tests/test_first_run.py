"""The first run after setup: whatever the sources already hold is recorded and logged only, with no alert. Only events that appear
after that first run may interrupt. Everything goes through the real command line (main) against the fake world."""

from datetime import timedelta

from headsup import pipeline
from headsup.__main__ import build_app
from headsup.pipeline import FIRST_RUN_REASON, baseline_key
from headsup.ui_page import decision_kind, human_reason
from test_cli import run, world
from world import NOW, Clock, FakeAnthropic, ics_for, settings, store_at

EMPTY_CALENDAR = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//made-up//tests//EN\r\nEND:VCALENDAR\r\n"


def second_meeting(start):
    """A calendar event that is NOT the one the world starts with (another UID), so it is genuinely new."""
    return ics_for(start, "A new meeting").replace(b"cal-1@example.invalid", b"cal-2@example.invalid")


def reasons(store):
    return {event_id: reason for event_id, _, reason, _, _ in store.decisions()}


def test_the_very_first_run_records_everything_and_alerts_about_nothing(tmp_path):
    w, store, haiku, out = world(), store_at(tmp_path, baselined=False), FakeAnthropic(), []
    code, _ = run(["--once"], w, store, haiku=haiku, out=out)
    assert code == 0 and w.unexpected == []
    # the same 6 events the normal end-to-end test sees (3 of which would have alerted): all seen, all logged, none sent
    decisions = store.decisions()
    assert store.seen_count() == 6 and len(decisions) == 6
    assert [d[1] for d in decisions] == ["log_only"] * 6 and {d[2] for d in decisions} == {FIRST_RUN_REASON}
    assert [d[3] for d in decisions] == [None] * 6, "no probability: the Decider was not asked"
    assert w.decider_requests == [] and haiku.calls == [] and w.sent() == [] and w.methods().count("sendMessage") == 0
    assert any(line.startswith("first run: 6 existing event(s) were recorded and logged only") for line in out)
    assert out[-1] == "done: collected 6, new 6, interrupt 0, log_only 6, sent 0, taps 0"
    assert store.get_kv(baseline_key("github")) is not None and store.get_kv(baseline_key("ical")) is not None


def test_events_that_appear_after_the_first_run_may_interrupt(tmp_path):
    w, store, clock, out = world(), store_at(tmp_path, baselined=False), Clock(), []
    run(["--once"], w, store, clock=clock)
    assert w.sent() == []
    # nothing changed: nothing is new, nothing alerts
    _, again = run(["--once"], w, store, clock=clock)
    assert again[-1] == "done: collected 0, new 0, interrupt 0, log_only 0, sent 0, taps 0"
    # a new meeting shows up three hours later, 20 minutes before it starts
    clock.advance(hours=3)
    w.calendar = second_meeting(clock() + timedelta(minutes=20))
    code, out = run(["--once"], w, store, clock=clock, out=out)
    assert code == 0 and out[-1].startswith("done: collected 1, new 1, interrupt 1, log_only 0, sent 1")
    assert len(w.sent()) == 1, "exactly one alert, for the one new event"
    newest = [d for d in store.decisions() if d[0].startswith("ical:cal-2")]
    assert newest and newest[0][1] == "interrupt" and newest[0][3] is not None, "scored by the Decider like any new event"
    assert not any("first run" in line for line in out)


def test_the_first_run_is_not_repeated_and_a_second_run_never_logs_new_events_as_baseline(tmp_path):
    w, store = world(), store_at(tmp_path, baselined=False)
    run(["--once"], w, store)
    before = [d for d in store.decisions()]
    run(["--once"], w, store)
    assert [d for d in store.decisions()] == before, "the second pass added nothing and changed nothing"
    assert all(reason == FIRST_RUN_REASON for reason in reasons(store).values())


def test_a_source_that_failed_on_its_first_run_is_baselined_later_and_the_other_is_not_held_back(tmp_path):
    w, store, clock = world(), store_at(tmp_path, baselined=False), Clock()
    w.calendar_status = 500  # the calendar is down on the very first run
    _, out = run(["--once"], w, store, clock=clock)
    assert store.get_kv(baseline_key("github")) is not None and store.get_kv(baseline_key("ical")) is None, "only the source that answered is done"
    assert any("ical: error" in line for line in out)
    # next run: the calendar answers. Its events were already there at setup, so they are logged only; GitHub is unaffected
    w.calendar_status = 200
    clock.advance(minutes=5)
    _, out = run(["--once"], w, store, clock=clock)
    calendar = {e: r for e, r in reasons(store).items() if e.startswith("ical:")}
    assert calendar and set(calendar.values()) == {FIRST_RUN_REASON}
    assert store.get_kv(baseline_key("ical")) is not None
    assert w.sent() == [] and any(line.startswith("first run: 1 existing event(s)") for line in out)


def test_a_source_that_held_nothing_on_the_first_run_is_still_done_so_its_later_events_alert(tmp_path):
    w = world()
    w.calendar = EMPTY_CALENDAR
    store, clock = store_at(tmp_path, baselined=False), Clock()
    cfg = settings(github_repos=())  # only the calendar, and it is empty
    _, out = run(["--once"], w, store, clock=clock, cfg=cfg)
    assert out[-1] == "done: collected 0, new 0, interrupt 0, log_only 0, sent 0, taps 0"
    assert store.get_kv(baseline_key("ical")) is not None, "an empty first poll is a first poll"
    clock.advance(hours=1)
    w.calendar = second_meeting(clock() + timedelta(minutes=20))
    _, out = run(["--once"], w, store, clock=clock, cfg=cfg)
    assert out[-1].startswith("done: collected 1, new 1, interrupt 1") and len(w.sent()) == 1


def test_an_install_that_already_has_events_is_not_treated_as_a_first_run(tmp_path):
    """An update from before v0.3: GitHub events are stored but no flag exists. GitHub continues normally; the calendar has never
    been seen, so its first pass is still the baseline."""
    w, store = world(), store_at(tmp_path, baselined=False)
    from headsup.schema import Event
    store.add_if_new(Event(id="github:old", source="github", kind="issue_opened", title="old", body="", url="", occurred_at=NOW, fetched_at=NOW,
                           content_hash="old-hash"))
    haiku = FakeAnthropic()
    run(["--once"], w, store, haiku=haiku)
    by_source = {}
    for event_id, verdict, reason, prob, _ in store.decisions():
        by_source.setdefault(event_id.split(":")[0], []).append((verdict, reason))
    assert {r for _, r in by_source["ical"]} == {FIRST_RUN_REASON}, "never-seen calendar: baseline"
    assert not any(r == FIRST_RUN_REASON for _, r in by_source["github"]) and any(v == "interrupt" for v, _ in by_source["github"]), "GitHub carries on as before"
    assert store.get_kv(baseline_key("github")) is not None, "flagged now, so this can never be mistaken for a first run later"


def test_a_dry_run_shows_the_first_run_and_leaves_the_real_database_untouched(tmp_path):
    w, store, out = world(), store_at(tmp_path, baselined=False), []
    code, out = run(["--once", "--dry-run"], w, store, out=out)
    assert code == 0 and any("first run: 6 existing event(s)" in line for line in out)
    assert not any("would send" in line for line in out), "no alert is previewed either"
    assert store.seen_count() == 0 and store.get_kv(baseline_key("github")) is None, "the preview did not use up the real first run"
    assert w.sent() == []
    run(["--once"], w, store)
    assert store.seen_count() == 6 and set(reasons(store).values()) == {FIRST_RUN_REASON}


def test_nothing_is_sent_and_nothing_is_scored_on_the_first_run_even_with_every_service_ready(tmp_path):
    w, store, haiku = world(), store_at(tmp_path, baselined=False), FakeAnthropic()
    w.default_score = 0.99  # everything would alert
    run(["--once"], w, store, haiku=haiku)
    assert w.sent() == [] and haiku.calls == [] and w.decider_requests == [] and w.github_writes == []


def test_only_real_runs_baseline_and_a_hand_built_app_is_unchanged(tmp_path):
    assert pipeline.App.__dataclass_fields__["baseline"].default is False
    w = world()
    app = build_app(settings(), client=w.client, anthropic_client=None, now=Clock(), dry_run=False, out=lambda s: None, store=store_at(tmp_path))
    assert app.baseline is True


def test_the_dashboard_labels_the_reason_plainly():
    assert human_reason("log_only", FIRST_RUN_REASON) == "Already there at setup"
    assert decision_kind({"verdict": "log_only", "reason": FIRST_RUN_REASON}) == "logged"
