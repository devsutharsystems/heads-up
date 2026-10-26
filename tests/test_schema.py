import dataclasses
import json
from pathlib import Path

from eval import gen_synthetic
from headsup import schema

ROOT = Path(__file__).resolve().parent.parent


def test_event_is_shared_with_the_generator():
    assert gen_synthetic.Event is schema.Event


def test_event_fields_keep_their_order_and_the_new_one_is_last_and_optional():
    names = [f.name for f in dataclasses.fields(schema.Event)]
    assert names == ["id", "source", "kind", "title", "body", "url", "occurred_at", "fetched_at", "content_hash",
                     "branch", "start_time", "all_day", "is_default_branch"]
    assert dataclasses.fields(schema.Event)[-1].default is None


def test_frozen_pilot_events_still_load():
    lines = (ROOT / "eval" / "pilot_events.jsonl").read_text().splitlines()
    assert len(lines) == 30
    ev = schema.Event(**json.loads(lines[0]))
    assert ev.is_default_branch is None


def test_decision_defaults():
    d = schema.Decision("e1", "log_only", "why", None)
    assert d.probability is None and d.threshold is None


def test_build_input_lives_in_headsup_state_and_eval_imports_it_back():
    from eval import pilot
    from headsup import state
    assert pilot.build_input is state.build_input
