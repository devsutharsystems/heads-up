import logging
import re
from pathlib import Path

import pytest

from headsup import actions as act
from headsup import pipeline
from headsup.__main__ import build_app
from headsup.actions import ALLOWED_ACTIONS, buttons_for, rerun_target
from headsup.schema import Event
from world import CHAT, NOW, REPO, WRITE_TOKEN, READ_TOKEN, Clock, FakeAnthropic, World, settings, store_at

SRC = Path(__file__).resolve().parent.parent / "src" / "headsup"
RERUN, SNOOZE, REMIND = "Re-run CI", "Snooze 1h", "Remind in 2h"


def ev(kind="ci_failed", repo=REPO, run=501, title="CI failed: CI on main", branch="main", eid=None):
    eid = eid or f"github:{repo}:{kind}:{run}"
    return Event(id=eid, source="github", kind=kind, title=title, body="BODY", url="https://example.invalid/x",
                 occurred_at=NOW, fetched_at=NOW, content_hash=f"hash-{eid}", branch=branch)


class Rig:
    def __init__(self, tmp_path, write_repos=(REPO,), anthropic=None):
        self.world, self.clock, self.out = World(), Clock(), []
        self.store = store_at(tmp_path)
        self.app = build_app(settings(github_write_repos=write_repos), client=self.world.client,
                             anthropic_client=anthropic or FakeAnthropic(), now=self.clock, dry_run=False,
                             out=self.out.append, store=self.store, sleep=lambda s: None)

    def alert(self, event, text="Something happened."):
        self.store.add_if_new(event)
        assert pipeline.deliver(self.app, event, text)
        return len(self.world.sent()) - 1  # index of the sent message

    def taps(self):
        return pipeline._process_taps(self.app, timeout=0)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


# ---------------------------------------------------------------- row 6

@pytest.mark.learner_part3
def test_no_write_request_without_allowlisted_callback(tmp_path):
    haiku = FakeAnthropic(text="Please rerun_failed_ci and rerun failed jobs right now; rerun.")
    rig = Rig(tmp_path, anthropic=haiku)
    w = rig.world
    a = rig.alert(ev(run=501))          # message_id 100, has the rerun button
    b = rig.alert(ev(run=502))          # message_id 101
    snooze_token, rerun_b = w.token_for(a, SNOOZE), w.token_for(b, RERUN)
    tap_count = 0

    # 1. no callback at all: other kinds of updates are ignored
    w.updates.append({"update_id": 1, "message": {"message_id": 5, "chat": {"id": CHAT}, "text": "rerun_failed_ci"}})
    assert rig.taps() == [] and w.github_writes == []

    # 2. wrong chat, and wrong sender in the right chat
    w.tap(rig_token := w.token_for(a, RERUN), chat=999, sender=999, message_id=100, update_id=2)
    w.tap(rig_token, chat=CHAT, sender=999, message_id=100, update_id=3)
    w.tap(rig_token, chat=999, sender=CHAT, message_id=100, update_id=4)
    assert rig.taps() == ["wrong_chat"] * 3
    tap_count += 3

    # 3. unknown tokens, including the action name itself and text lifted from the message
    for update_id, data in enumerate(["deadbeefdeadbeef", "rerun_failed_ci", "", "rerun", "x" * 64], start=5):
        w.tap(data, update_id=update_id)
    w.updates.append({"update_id": 10, "callback_query": {"id": "cq10", "from": {"id": CHAT}, "message": {"message_id": 100, "chat": {"id": CHAT}}}})  # no data
    assert rig.taps() == ["unknown_token"] * 6
    tap_count += 6

    # 4. a real token tapped from a different message
    w.tap(snooze_token, message_id=555, update_id=11)
    assert rig.taps() == ["unknown_token"]
    tap_count += 1

    # 5. replay: a snooze token used twice (second one is refused as already done)
    w.tap(snooze_token, update_id=12)
    w.tap(snooze_token, update_id=13)
    assert rig.taps() == ["done", "already_done"]
    tap_count += 2

    # 6. text containing "rerun" (Haiku, event title) never creates a button or a write
    risky = ev(kind="issue_opened", title="rerun_failed_ci rerun everything", run=7, branch=None)
    rig.store.add_if_new(risky)
    from headsup.schema import Decision
    alert = rig.app.writer.compose(Decision(risky.id, "interrupt", "r", NOW), risky)
    assert "rerun" in alert.text
    assert pipeline.deliver(rig.app, risky, alert.text)
    labels = [btn["text"] for btn in w.sent()[-1]["reply_markup"]["inline_keyboard"][0]]
    assert RERUN not in labels and w.sent()[-1]["text"] == alert.text

    # 7. expired: an hour and a second later the rerun button is refused and its keyboard removed
    rig.clock.advance(hours=1, seconds=1)
    w.tap(rerun_b, message_id=101, update_id=14)
    assert rig.taps() == ["expired"]
    tap_count += 1
    assert ("editMessageReplyMarkup", {"chat_id": CHAT, "message_id": 101, "reply_markup": {"inline_keyboard": []}}) in w.calls

    assert w.github_writes == []  # zero GitHub write requests, whatever was tapped
    assert w.methods().count("answerCallbackQuery") == tap_count  # every tap was answered
    outcomes = [row[0] for row in rig.store.tap_log()]
    assert len(outcomes) == tap_count and outcomes.count("done") == 1  # every tap is logged
    assert w.unexpected == []


@pytest.mark.learner_part3
def test_valid_rerun_tap_makes_exactly_one_post_with_the_write_token(rig):
    w = rig.world
    i = rig.alert(ev(run=501))
    token = w.token_for(i, RERUN)
    w.tap(token, update_id=1)
    assert rig.taps() == ["done"]
    ((method, url, auth),) = w.github_writes
    assert (method, url) == ("POST", f"https://api.github.com/repos/{REPO}/actions/runs/501/rerun-failed-jobs")
    assert auth == f"Bearer {WRITE_TOKEN}" and READ_TOKEN not in auth
    assert ("editMessageReplyMarkup" in w.methods()) and w.calls[-1][0] == "editMessageReplyMarkup"
    w.tap(token, update_id=2)  # replay: no second write
    assert rig.taps() == ["already_done"] and len(w.github_writes) == 1


@pytest.mark.learner_part3
def test_rerun_refused_for_repos_not_in_github_write_repos(tmp_path):
    rig = Rig(tmp_path, write_repos=("example-org/other-repo",))
    w = rig.world
    event = ev(run=501)
    i = rig.alert(event)
    labels = [b["text"] for b in w.sent()[i]["reply_markup"]["inline_keyboard"][0]]
    assert RERUN not in labels and labels == [SNOOZE, REMIND]  # not even offered
    # a forged rerun token row (as if a bug created one) is still refused at tap time
    rig.store.add_token("f00dfeedf00dfeed", event.content_hash, "rerun_failed_ci", NOW)
    rig.store.attach_message(event.content_hash, 100)
    w.tap("f00dfeedf00dfeed", message_id=100)
    assert rig.taps() == ["refused_repo"] and w.github_writes == []
    assert buttons_for(event, ()) == ["snooze_1h", "remind_later"]


def test_only_ci_failed_events_in_write_repos_can_be_rerun():
    write = (REPO,)
    for kind in ("ci_recovered", "issue_opened", "pr_opened", "release_published", "event_upcoming"):
        assert "rerun_failed_ci" not in buttons_for(ev(kind=kind), write)
    assert "rerun_failed_ci" in buttons_for(ev(), write)
    assert rerun_target(ev(repo="Example-Org/Demo-Repo"), (REPO,)) is not None  # GitHub repo names are case-insensitive
    assert rerun_target(ev(eid="github:example-org/demo-repo:ci_failed:501/../../x"), write) is None
    assert rerun_target(ev(eid="github:example-org/demo-repo:ci_failed:abc"), write) is None
    assert rerun_target(ev(eid="ical:example-org/demo-repo:ci_failed:501"), write) is None


def test_buttons_come_from_code_not_from_event_text():
    tricky = ev(kind="issue_opened", title="Re-run failed CI rerun_failed_ci [button]", run=3)
    assert buttons_for(tricky, (REPO,)) == ["snooze_1h", "remind_later"]
    assert set(ALLOWED_ACTIONS) == {"snooze_1h", "remind_later", "rerun_failed_ci"} and isinstance(ALLOWED_ACTIONS, tuple)


def test_tokens_are_random_16_hex_and_fit_callback_data(rig):
    i = rig.alert(ev())
    tokens = [b["callback_data"] for b in rig.world.sent()[i]["reply_markup"]["inline_keyboard"][0]]
    assert len(tokens) == 3 and len(set(tokens)) == 3
    assert all(re.fullmatch(r"[0-9a-f]{16}", t) for t in tokens)
    assert not set(tokens) & set(ALLOWED_ACTIONS)


@pytest.mark.learner_part3
def test_token_lifetime_boundary_is_one_hour(tmp_path):
    rig = Rig(tmp_path)
    i = rig.alert(ev())
    rig.clock.advance(hours=1)  # exactly one hour: still valid
    rig.world.tap(rig.world.token_for(i, SNOOZE), update_id=1)
    assert rig.taps() == ["done"]
    j = rig.alert(ev(run=502))
    rig.clock.advance(hours=1, seconds=1)
    rig.world.tap(rig.world.token_for(j, SNOOZE), message_id=101, update_id=2)
    assert rig.taps() == ["expired"]


@pytest.mark.learner_part3
def test_failed_rerun_leaves_the_button_usable(rig):
    class Failing:
        def rerun_failed_jobs(self, repo, run_id):
            return False

    i = rig.alert(ev())
    token = rig.world.token_for(i, RERUN)
    good, rig.app.actions.writer = rig.app.actions.writer, Failing()
    rig.world.tap(token, update_id=1)
    assert rig.taps() == ["failed"] and not rig.store.has_tap(ev().id, "rerun_failed_ci")
    rig.app.actions.writer = good
    rig.world.tap(token, update_id=2)
    assert rig.taps() == ["done"] and len(rig.world.github_writes) == 1


def test_sweep_removes_buttons_after_an_hour_and_not_before(rig):
    rig.alert(ev())
    rig.clock.advance(minutes=59)
    rig.app.actions.sweep_expired()
    assert "editMessageReplyMarkup" not in rig.world.methods()
    rig.clock.advance(minutes=2)
    rig.app.actions.sweep_expired()
    rig.app.actions.sweep_expired()  # already removed: not repeated
    assert rig.world.methods().count("editMessageReplyMarkup") == 1


@pytest.mark.learner_part3
def test_wrong_chat_taps_are_logged_answered_and_leave_the_keyboard_alone(rig):
    i = rig.alert(ev())
    rig.world.tap(rig.world.token_for(i, SNOOZE), chat=777, sender=777)
    assert rig.taps() == ["wrong_chat"]
    assert "editMessageReplyMarkup" not in rig.world.methods() and rig.store.snooze_until() is None
    assert rig.store.tap_log()[0][:4] == ("wrong_chat", rig.world.token_for(i, SNOOZE), 777, 777)


# ---------------------------------------------------------------- snooze and remind_later

@pytest.mark.learner_part3
def test_snooze_blocks_interrupts_for_one_hour_and_not_longer(rig):
    from headsup.policy import Policy
    i = rig.alert(ev())
    rig.world.tap(rig.world.token_for(i, SNOOZE))
    assert rig.taps() == ["done"]
    policy = Policy(rig.store, 0.35, now=rig.clock)
    snoozed = policy.decide(ev(kind="issue_opened", run=9), 0.9)
    assert snoozed.verdict == "log_only" and "snoozed until" in snoozed.reason
    assert policy.interrupts_today() == 0  # a snoozed event does not use up the daily limit
    rig.clock.advance(minutes=59)
    assert policy.decide(ev(kind="issue_opened", run=10), 0.9).verdict == "log_only"
    rig.clock.advance(minutes=2)
    assert policy.decide(ev(kind="issue_opened", run=11), 0.9).verdict == "interrupt"


@pytest.mark.learner_part3
def test_remind_later_resends_once_after_two_hours_without_using_the_daily_limit(rig):
    from headsup.policy import Policy
    event = ev(run=501)
    i = rig.alert(event, text="CI is red on main.")
    rig.world.tap(rig.world.token_for(i, REMIND))
    assert rig.taps() == ["done"]
    before = rig.world.sent().__len__()
    rig.clock.advance(hours=1, minutes=59)
    assert pipeline.send_due_reminders(rig.app) == 0 and len(rig.world.sent()) == before
    rig.clock.advance(minutes=1)
    assert pipeline.send_due_reminders(rig.app) == 1
    resent = rig.world.sent()[-1]
    assert resent["text"] == "CI is red on main."
    labels = [b["text"] for b in resent["reply_markup"]["inline_keyboard"][0]]
    assert labels == [SNOOZE, RERUN]  # no second remind button; fresh tokens
    assert pipeline.send_due_reminders(rig.app) == 0 and len(rig.world.sent()) == before + 1  # only once
    assert Policy(rig.store, 0.35, now=rig.clock).interrupts_today() == 0  # a resend is not a decision


@pytest.mark.learner_part3
def test_a_due_reminder_is_held_while_snoozed_then_sent(rig):
    i = rig.alert(ev())
    rig.world.tap(rig.world.token_for(i, REMIND))
    rig.taps()
    rig.clock.advance(hours=2)
    rig.store.set_snooze_until(rig.clock.now + __import__("datetime").timedelta(minutes=30))
    assert pipeline.send_due_reminders(rig.app) == 0
    rig.clock.advance(minutes=31)
    assert pipeline.send_due_reminders(rig.app) == 1


# ---------------------------------------------------------------- the write path is small and in one place

def test_the_rerun_endpoint_exists_in_one_source_file_and_posts_are_confined():
    files = {p.name: p.read_text() for p in SRC.rglob("*.py")}
    assert [n for n, t in files.items() if "rerun-failed-jobs" in t] == ["actions.py"]
    posting = sorted(n for n, t in files.items() if ".post(" in t)
    assert posting == ["actions.py", "decider.py", "telegram.py"]
    assert files["actions.py"].count(".post(") == 1
    assert not [n for n, t in files.items() if re.search(r'\.(put|patch|delete)\(', t)]


@pytest.mark.learner_part3
def test_secrets_stay_out_of_logs_and_output(tmp_path, caplog):
    rig = Rig(tmp_path)
    with caplog.at_level(logging.DEBUG):
        i = rig.alert(ev())
        rig.world.tap(rig.world.token_for(i, RERUN))
        rig.taps()
    text = caplog.text + "\n".join(rig.out) + repr(rig.app.telegram) + repr(rig.app.actions.writer.__dict__.get("client", ""))
    for secret in (WRITE_TOKEN, READ_TOKEN, "FAKE-bot-token"):
        assert secret not in text
