"""tools/make_starter.py: the learner parts are marked on main, and blanking them breaks exactly the expected tests."""

import importlib.util
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FILES = ("src/headsup/policy.py", "src/headsup/actions.py")
# On a starter checkout (parts already blank) these checks are meaningless, and the nested run below must not recurse.
ALREADY_STARTER = any('raise NotImplementedError("LEARNER PART' in (ROOT / rel).read_text() for rel in FILES)
pytestmark = pytest.mark.skipif(ALREADY_STARTER, reason="the learner parts are already blank")

spec = importlib.util.spec_from_file_location("make_starter", ROOT / "tools" / "make_starter.py")
make_starter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_starter)

# Tests that test a part directly (they carry @pytest.mark.learner_partN).
GUIDING = {
    "test_policy::test_policy_interrupts_above_threshold_else_logs_only",
    "test_policy::test_passes_threshold_is_part_one_on_its_own",
    "test_policy::test_the_reason_says_where_the_threshold_came_from_when_it_is_the_chosen_one",
    "test_policy::test_policy_stops_interrupting_after_five_per_day",
    "test_policy::test_daily_limit_resets_at_local_midnight_not_utc_midnight",
    "test_policy::test_below_threshold_events_do_not_count_toward_the_limit",
    "test_actions::test_no_write_request_without_allowlisted_callback",
    "test_actions::test_valid_rerun_tap_makes_exactly_one_post_with_the_write_token",
    "test_actions::test_rerun_refused_for_repos_not_in_github_write_repos",
    "test_actions::test_token_lifetime_boundary_is_one_hour",
    "test_actions::test_failed_rerun_leaves_the_button_usable",
    "test_actions::test_wrong_chat_taps_are_logged_answered_and_leave_the_keyboard_alone",
    "test_actions::test_snooze_blocks_interrupts_for_one_hour_and_not_longer",
    "test_actions::test_remind_later_resends_once_after_two_hours_without_using_the_daily_limit",
    "test_actions::test_a_due_reminder_is_held_while_snoozed_then_sent",
    "test_actions::test_secrets_stay_out_of_logs_and_output",
}
# Other tests that fail on the starter only because they run a whole pass through a blank part.
DOWNSTREAM = {
    # a button tap is handled by the learner part, so the test that sends one after a message cannot pass on the starter copy
    "test_seen_chats::test_a_button_tap_after_a_message_no_longer_makes_the_message_disappear",
    # the first-run tests need the real interrupt decision to see an alert, so they fail on the starter copy too
    "test_first_run::test_events_that_appear_after_the_first_run_may_interrupt",
    "test_first_run::test_a_source_that_held_nothing_on_the_first_run_is_still_done_so_its_later_events_alert",
    "test_first_run::test_an_install_that_already_has_events_is_not_treated_as_a_first_run",
    "test_ci_collapse::test_morning_batch_gives_one_interrupt_for_the_branch_and_still_sends_the_sandbox_failure",
    "test_ci_collapse::test_a_failure_with_an_earlier_unrecovered_alert_is_logged_only",
    "test_ci_collapse::test_after_a_recovery_a_new_failure_alerts_again",
    "test_ci_collapse::test_a_failure_with_a_later_recovery_in_the_same_batch_is_already_recovered",
    "test_ci_collapse::test_a_failure_with_a_later_recovery_already_in_the_store_is_already_recovered",
    "test_ci_collapse::test_other_branches_workflows_and_repos_are_separate_keys",
    "test_ci_collapse::test_collapsed_events_are_logged_without_asking_the_decider",
    "test_cli::test_once_end_to_end_collect_dedupe_decide_write_send_then_taps",
    "test_cli::test_taps_are_processed_before_new_events_are_collected",
    "test_cli::test_dry_run_makes_no_sends_and_no_writes_and_leaves_the_database_alone",
    "test_cli::test_a_failed_send_stays_pending_and_is_retried_next_run",
    "test_cli::test_one_failing_source_does_not_stop_the_other_and_no_url_is_printed",
    "test_cli::test_every_run_prints_one_line_per_source",
    "test_cli::test_a_failing_source_shows_its_error_and_the_other_still_reports",
    "test_cli::test_calendar_errors_show_the_message_but_never_the_address",
    "test_cli::test_github_warnings_are_shown_next_to_the_count",
    "test_cli::test_dry_run_says_haiku_skipped_and_not_no_api_key",
    "test_cli::test_no_api_key_is_shown_only_when_the_key_is_really_missing",
    "test_cli::test_github_and_calendar_requests_use_connect_10_read_15",
    "test_cli::test_writer_errors_are_flagged_for_dev",
    "test_cli::test_listen_long_polls_handles_taps_and_stops_after_the_time",
    "test_cli::test_secrets_never_reach_output_or_logs",
    "test_writer::test_writer_is_not_called_for_log_only_events_in_a_full_pass",
}


def copy_repo(dest):
    """A copy of the repository for the check. Never takes .env (only .env.example), the venv, git or caches."""
    def ignore(directory, names):
        here, skip = Path(directory), set()
        for name in names:
            if name in {".git", ".venv", ".cache", ".claude", "__pycache__", ".pytest_cache", "vendor", "ollama_work"}:
                skip.add(name)
            elif name == ".env" or (name.startswith(".env.") and name != ".env.example"):
                skip.add(name)
            elif name.endswith((".db", ".db-wal", ".db-shm")):
                skip.add(name)
            elif here == ROOT and name == "data":
                skip.add(name)
            elif here == ROOT / "spike" and name == "runs":
                skip.add(name)
        return skip
    shutil.copytree(ROOT, dest, ignore=ignore)
    assert not (dest / ".env").exists()


def run_script(root, *extra):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "make_starter.py"), "--root", str(root), *extra],
                          capture_output=True, text=True)


def mini_repo(tmp_path):
    """Only the two files the script edits."""
    for rel in FILES:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, tmp_path / rel)
    return tmp_path


def snapshot(root):
    return {rel: (root / rel).read_text() for rel in FILES}


# ---------------------------------------------------------------- the markers on main

def test_all_six_markers_are_present_balanced_and_in_the_right_files():
    for rel, parts in (("src/headsup/policy.py", {1, 2}), ("src/headsup/actions.py", {3})):
        lines, regions = make_starter.find_regions(rel, (ROOT / rel).read_text(), {n: make_starter.PARTS[n] for n in parts})
        assert set(regions) == parts
        for a, b, indent in regions.values():
            assert b > a + 1 and indent  # something sits between the markers, inside a function


def test_guiding_tests_are_exactly_the_marked_tests():
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                          "-m", "learner_part1 or learner_part2 or learner_part3"], cwd=ROOT, capture_output=True, text=True).stdout
    marked = {line.split("/")[-1].replace(".py", "") for line in out.splitlines() if "::" in line}
    assert marked == GUIDING


# ---------------------------------------------------------------- the check on a copy

def test_blanking_the_parts_on_a_copy_fails_exactly_the_expected_tests(tmp_path):
    copy = tmp_path / "copy"
    copy_repo(copy)
    done = run_script(copy)
    assert done.returncode == 0, done.stdout + done.stderr
    report = tmp_path / "report.xml"
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}"],
                   cwd=copy, capture_output=True, text=True)
    failed, passed = set(), 0
    for case in ET.parse(report).getroot().iter("testcase"):
        test_id = f"{case.get('classname').split('.')[-1]}::{case.get('name')}"
        problem = case.find("failure") if case.find("failure") is not None else case.find("error")
        if problem is None:
            passed += 1
            continue
        failed.add(test_id)
        assert "LEARNER PART" in (problem.get("message", "") + (problem.text or "")), test_id
    assert failed == GUIDING | DOWNSTREAM
    assert len(GUIDING) == 16 and len(DOWNSTREAM) == 27 and passed > 200


# ---------------------------------------------------------------- the script itself

def test_running_twice_changes_nothing_the_second_time(tmp_path):
    root = mini_repo(tmp_path)
    first = run_script(root)
    after_first = snapshot(root)
    second = run_script(root)
    assert first.returncode == 0 and second.returncode == 0 and "no change" in second.stdout
    assert snapshot(root) == after_first
    text = after_first["src/headsup/policy.py"] + after_first["src/headsup/actions.py"]
    for n in (1, 2, 3):
        assert f'raise NotImplementedError("LEARNER PART {n}:' in text
    assert "return probability is not None" not in text and "self.interrupts_today(now) < self.limit" not in text
    assert "self.store.set_snooze_until" not in after_first["src/headsup/actions.py"]  # part 3's body is gone


def test_everything_outside_the_markers_is_untouched(tmp_path):
    root = mini_repo(tmp_path)
    before = snapshot(root)
    run_script(root)
    after = snapshot(root)
    for rel in FILES:
        keep = lambda text: [l for l in text.splitlines() if l.strip().startswith(("def ", "class ", "import ", "from ", "ALLOWED", "LABELS"))]
        assert keep(before[rel]) == keep(after[rel])
    assert "def sweep_expired" in after["src/headsup/actions.py"] and "def interrupts_today" in after["src/headsup/policy.py"]


@pytest.mark.parametrize("damage", [
    ("src/headsup/policy.py", "    # LEARNER PART 1 END\n", ""),                                  # START with no END
    ("src/headsup/policy.py", "    # LEARNER PART 1 START\n", ""),                                # END with no START
    ("src/headsup/actions.py", "        # LEARNER PART 3 START\n", "        # LEARNER PART 3 START\n        # LEARNER PART 3 START\n"),  # duplicate
    ("src/headsup/actions.py", "        # LEARNER PART 3 START\n", "        # LEARNER PART 4 START\n"),  # unknown part
    ("src/headsup/policy.py", "        # LEARNER PART 2 START", "      # LEARNER PART 2 START"),    # indentation differs from END
])
def test_missing_duplicate_unknown_or_unbalanced_markers_are_refused_and_nothing_is_written(tmp_path, damage):
    root = mini_repo(tmp_path)
    rel, old, new = damage
    path = root / rel
    assert old in path.read_text()
    path.write_text(path.read_text().replace(old, new, 1))
    before = snapshot(root)
    done = run_script(root)
    assert done.returncode == 2 and done.stdout.startswith("refused:")
    assert snapshot(root) == before


def test_end_before_start_is_refused(tmp_path):
    root = mini_repo(tmp_path)
    path = root / "src/headsup/actions.py"
    text = path.read_text().replace("# LEARNER PART 3 START", "# LEARNER PART 3 TMP").replace("# LEARNER PART 3 END", "# LEARNER PART 3 START")
    path.write_text(text.replace("# LEARNER PART 3 TMP", "# LEARNER PART 3 END"))
    before = snapshot(root)
    done = run_script(root)
    assert done.returncode == 2 and "before START" in done.stdout and snapshot(root) == before


def test_a_checkout_on_main_is_refused_unless_allowed(tmp_path):
    root = mini_repo(tmp_path)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    before = snapshot(root)
    done = run_script(root)
    assert done.returncode == 2 and "main branch" in done.stdout and snapshot(root) == before
    (root / ".git" / "HEAD").write_text("ref: refs/heads/starter\n")
    assert run_script(root).returncode == 0 and snapshot(root) != before


def test_the_copy_helper_never_takes_env_files(tmp_path):
    copy = tmp_path / "copy"
    copy_repo(copy)
    names = {p.name for p in copy.rglob(".env*")}
    assert names <= {".env.example"}
