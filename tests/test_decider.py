import json
import shutil
from datetime import datetime, timezone

import httpx
import pytest

from eval import pilot
from headsup import decider as dec
from headsup import haiku, state
from headsup.decider import DECIDER_MODEL, F3_OPTIONS, FORMATS, Decider, question_for
from headsup.frozen import EVAL_PROMPT_DIR, PromptFrozenError, load_frozen
from headsup.policy import Policy
from headsup.schema import Event
from headsup.store import Store

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
HOST = "127.0.0.1:11434"


def ev(i=1, kind="issue_opened"):
    return Event(id=f"e{i}", source="github", kind=kind, title="Crash on empty config", body="BODY", url="https://example.invalid/x",
                 occurred_at=NOW, fetched_at=NOW, content_hash=f"h{i}")


class Server:
    """Stands in for Ollama: records requests, answers from a function. Used via httpx.MockTransport."""

    def __init__(self, respond):
        self.respond = respond
        self.requests = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append((request, body))
        return self.respond(body)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


def noul(p):
    return lambda body: httpx.Response(200, json={"answers": {"interrupt": {"type": "noul", "noul": p}}})


def test_f1_request_and_answer():
    server = Server(noul(0.42))
    d = Decider(server.client(), HOST, "F1")
    assert d.score(ev()) == 0.42
    request, body = server.requests[0]
    assert str(request.url) == f"http://{HOST}/v1/systemone"
    assert body["model"] == DECIDER_MODEL == "strands-decider:2b-mxfp8"
    assert body["state"] == state.build_input(ev())
    assert body["questions"]["interrupt"] == {"type": "noul", "instructions": load_frozen()["decider_F1.txt"][0]}
    assert body["keep_alive"] == "60s"
    assert request.extensions["timeout"]["read"] == 30.0


def test_f3_is_a_choice_question_scored_by_interrupt_now():
    answer = {"type": "choice", "choice": "can wait", "confidence": 0.1,
              "probabilities": {"can wait": 0.7, "interrupt now": 0.3}}
    server = Server(lambda body: httpx.Response(200, json={"answers": {"interrupt": answer}}))
    assert Decider(server.client(), HOST, "F3").score(ev()) == 0.3
    q = server.requests[0][1]["questions"]["interrupt"]
    assert q["type"] == "choice" and q["criteria"] == {"interrupt now": None, "can wait": None}


def test_decider_client_local_only_and_log_only_when_down(tmp_path):
    for host in ("example.com:11434", "10.0.0.5:11434", "127.0.0.1.evil.example:11434"):
        with pytest.raises(ValueError):
            Decider(Server(noul(0.5)).client(), host, "F1")

    def down(request):
        raise httpx.ConnectError("refused")

    d = Decider(httpx.Client(transport=httpx.MockTransport(down)), HOST, "F1")
    probability = d.score(ev())
    assert probability is None and d.last_error == "ConnectError"
    decision = Policy(Store(tmp_path / "t.db"), 0.35, now=lambda: NOW).decide(ev(), probability)
    assert decision.verdict == "log_only" and "unavailable" in decision.reason  # never interrupts blind


def test_timeout_gives_none():
    def slow(request):
        raise httpx.ReadTimeout("too slow")

    d = Decider(httpx.Client(transport=httpx.MockTransport(slow)), HOST, "F1")
    assert d.score(ev()) is None and d.last_error == "ReadTimeout"


@pytest.mark.parametrize("response", [
    httpx.Response(500, json={"error": "boom"}),
    httpx.Response(200, content=b"not json"),
    httpx.Response(200, json={"answers": {}}),
    httpx.Response(200, json={"answers": {"interrupt": {"type": "choice", "probabilities": {"interrupt now": 0.5}}}}),  # wrong type for F1
    httpx.Response(200, json={"answers": {"interrupt": {"type": "noul", "noul": 1.5}}}),
    httpx.Response(200, json={"answers": {"interrupt": {"type": "noul", "noul": "high"}}}),
    httpx.Response(200, json=["unexpected"]),
])
def test_bad_answers_give_none(response):
    d = Decider(Server(lambda body: response).client(), HOST, "F1")
    assert d.score(ev()) is None and d.last_error


def test_keep_alive_rejected_falls_back_to_none_and_is_noted():
    def respond(body):
        if "keep_alive" in body:
            return httpx.Response(400, json={"error": "unknown field keep_alive"})
        return noul(0.6)(body)

    server = Server(respond)
    d = Decider(server.client(), HOST, "F1")
    assert d.score(ev()) == 0.6
    assert [("keep_alive" in b) for _, b in server.requests] == [True, False]
    assert d.keep_alive_enabled is False and len(d.notes) == 1 and "keep_alive" in d.notes[0]
    assert d.score(ev(2)) == 0.6
    assert [("keep_alive" in b) for _, b in server.requests] == [True, False, False]  # no more attempts with it


def test_keep_alive_stays_on_when_the_retry_also_fails():
    server = Server(lambda body: httpx.Response(400, json={"error": "bad question"}))
    d = Decider(server.client(), HOST, "F1")
    assert d.score(ev()) is None
    assert len(server.requests) == 2 and d.keep_alive_enabled is True and d.notes == []


def test_a_changed_frozen_prompt_refuses_to_load(tmp_path):
    copy = tmp_path / "prompts"
    shutil.copytree(EVAL_PROMPT_DIR, copy)
    (copy / "decider_F1.txt").write_text("Should Dev be interrupted?\n")
    with pytest.raises(PromptFrozenError):
        load_frozen(copy)
    with pytest.raises(PromptFrozenError):
        Decider(Server(noul(0.5)).client(), HOST, "F1", prompts=load_frozen(copy))


# ---- drift guards: the runtime copies must equal the pilot's, which produced the evidence

def test_runtime_copies_match_the_pilot():
    prompts = load_frozen()
    assert prompts == pilot.load_prompts()
    for fmt in FORMATS:
        assert question_for(fmt, prompts) == pilot.decider_question(fmt, prompts, True)
    assert dec.DECIDER_MODEL == pilot.DECIDER_MODEL and dec.F3_OPTIONS == pilot.F3_OPTIONS
    assert haiku.HAIKU_MODEL == pilot.HAIKU_MODEL == "claude-haiku-5-5"
    assert (haiku.PRICE_IN, haiku.PRICE_OUT) == (pilot.PRICE_IN, pilot.PRICE_OUT)
    assert haiku.request_kwargs("x", pilot.HAIKU_MAX_TOKENS) == pilot.haiku_kwargs("x")
    assert pilot.build_input is state.build_input and pilot.PromptFrozenError is PromptFrozenError


def test_classify_error_matches_the_pilot():
    class E(Exception):
        def __init__(self, status):
            self.status_code = status

    for status in (401, 403, 402, 429, 500, None):
        assert haiku.classify_error(E(status)) == pilot.classify_error(E(status))
