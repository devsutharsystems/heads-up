"""Client for the local Strands Decider (Ollama build) at /v1/systemone.

Uses the frozen prompt texts in eval/prompts (verified against prompts.sha256). Any failure
(timeout, server down, bad answer) returns None: the caller must log only, never interrupt blind.
"""

import logging

import httpx

from headsup.frozen import load_frozen
from headsup.state import build_input

DECIDER_MODEL = "strands-decider:2b-mxfp8"
F3_OPTIONS = ("interrupt now", "can wait")
FORMATS = ("F1", "F2", "F3")
TIMEOUT_S = 30.0  # PLAN.md measured a cold load at 2.6 to 3.7 s; the rest is inference
KEEP_ALIVE = "60s"

log = logging.getLogger(__name__)


class _BadAnswer(Exception):
    pass


def check_local_host(host):
    name = host.rsplit(":", 1)[0] if ":" in host else host
    if name not in ("127.0.0.1", "localhost"):
        raise ValueError("the Decider host must be 127.0.0.1 or localhost")


def question_for(fmt, prompts):
    """Same question shapes as the pilot (eval.pilot.decider_question with the choice type accepted)."""
    if fmt == "F1":
        return {"type": "noul", "instructions": prompts["decider_F1.txt"][0]}
    if fmt == "F2":
        return {"type": "noul", "instructions": prompts["decider_F2.txt"][0]}
    if fmt == "F3":
        return {"type": "choice", "instructions": prompts["decider_F3.txt"][0], "criteria": {o: None for o in F3_OPTIONS}}
    raise ValueError(f"unknown decider format {fmt!r}")


def _score(answer, qtype):
    if answer.get("type") != qtype:
        raise _BadAnswer("answer type does not match the question")
    value = float(answer["noul"] if qtype == "noul" else answer["probabilities"][F3_OPTIONS[0]])
    if not 0.0 <= value <= 1.0:
        raise _BadAnswer("probability outside 0..1")
    return value


class Decider:
    def __init__(self, client, host, fmt, prompts=None, keep_alive=KEEP_ALIVE, timeout=TIMEOUT_S):
        check_local_host(host)
        self.client = client
        self.url = f"http://{host}/v1/systemone"
        self.fmt = fmt
        self.question = question_for(fmt, prompts if prompts is not None else load_frozen())
        self.keep_alive = keep_alive
        self.keep_alive_enabled = keep_alive is not None
        self.timeout = timeout
        self.notes = []
        self.last_error = None

    def _post(self, body):
        resp = self.client.post(self.url, json=body, timeout=self.timeout)
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        return resp.status_code, payload

    def score(self, event):
        """Probability that Dev should be interrupted, or None if the Decider could not answer."""
        self.last_error = None
        body = {"model": DECIDER_MODEL, "state": build_input(event), "questions": {"interrupt": self.question}}
        if self.keep_alive_enabled:
            body["keep_alive"] = self.keep_alive
        try:
            status, payload = self._post(body)
            if status in (400, 422) and "keep_alive" in body:
                del body["keep_alive"]
                status, payload = self._post(body)
                if status == 200:  # only now do we know keep_alive was the problem
                    self.keep_alive_enabled = False
                    self.notes.append("keep_alive was not accepted by /v1/systemone; the server default applies")
                    log.warning(self.notes[-1])
            if status != 200 or not isinstance(payload, dict):
                raise _BadAnswer(f"HTTP {status}")
            return _score(payload["answers"]["interrupt"], self.question["type"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, _BadAnswer) as exc:
            self.last_error = type(exc).__name__
            log.warning("decider unavailable: %s", self.last_error)  # class name only
            return None
