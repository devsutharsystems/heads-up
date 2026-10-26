"""Pilot runner (PLAN.md section 9.0): rules baseline vs Strands Decider (F1, F2, F3)
vs Haiku as a decider, on the 30 pilot events.

Usage (from the project folder):
  PYTHONPATH=src .venv/bin/python -m eval.pilot --check-f3
  PYTHONPATH=src .venv/bin/python -m eval.pilot --local
  PYTHONPATH=src .venv/bin/python -m eval.pilot --haiku [--limit N]

Nothing here reads the label sheet. Only --haiku loads .env (through headsup.config).
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from eval.baseline_rules import decide
from eval.gen_synthetic import Event

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
PROMPT_DIR = EVAL / "prompts"
EVENTS_PATH = EVAL / "pilot_events.jsonl"
RESULTS_PATH = EVAL / "pilot_results.jsonl"
F3_CHECK_PATH = EVAL / "f3_check.json"
COUNTER_PATH = EVAL / "haiku_call_counter.json"

PROMPT_FILES = ["decider_F1.txt", "decider_F2.txt", "decider_F3.txt", "decider_F3_noul.txt", "haiku_decider.txt"]

DECIDER_MODEL = "strands-decider:2b-mxfp8"
DEFAULT_HOST = "127.0.0.1:11436"  # the spike's Ollama port
F3_OPTIONS = ("interrupt now", "can wait")
F3_PROBE_STATE = "Source: github. Kind: issue_opened. Title: Typo in sample config comment. When: opened just now."
FORMATS = ("F1", "F2", "F3")

# Haiku. Model id is fixed (PLAN 8b). Prices: https://platform.claude.com/docs/en/about-claude/pricing
# ($0.10 in / $0.50 out per MTok for prompts up to 100K tokens). Cost = usage x listed price.
HAIKU_MODEL = "claude-haiku-5-5"
HAIKU_MAX_TOKENS = 8
PRICE_IN = 0.10 / 1_000_000
PRICE_OUT = 0.50 / 1_000_000
CALL_CAP = 200


class PromptFrozenError(Exception):
    pass


class CapReached(Exception):
    pass


class CounterError(Exception):
    pass


# ---------------------------------------------------------------- events and input

def load_events(path=EVENTS_PATH):
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            for k in ("occurred_at", "fetched_at", "start_time"):
                if d[k] is not None:
                    d[k] = datetime.fromisoformat(d[k])
            events.append(Event(**d))
    return events


def _ahead_text(delta):
    minutes = round(delta.total_seconds() / 60)
    if minutes < 90:
        return f"{minutes} min"
    hours = minutes / 60
    if hours < 36:
        return f"{round(hours)} h"
    return f"{round(hours / 24)} days"


def _time_phrase(e):
    if e.kind in ("ci_failed", "ci_recovered"):
        return f"CI on branch {e.branch}"
    if e.kind == "release_published":
        return "just published"
    if e.kind in ("pr_opened", "issue_opened"):
        return "opened just now"
    if e.kind == "event_upcoming":
        if e.all_day:
            return f"all-day on {e.start_time:%a}"
        return f"starts in {_ahead_text(e.start_time - e.occurred_at)}"
    if e.kind == "event_moved":
        return f"moved, now starts in {_ahead_text(e.start_time - e.occurred_at)}"
    if e.kind == "event_cancelled":
        return f"cancelled, was due in {_ahead_text(e.start_time - e.occurred_at)}"
    raise ValueError(f"unknown kind {e.kind!r}")


def build_input(e):
    """The one minimal input all three deciders see: source, kind, title, time phrase."""
    return f"Source: {e.source}. Kind: {e.kind}. Title: {e.title}. When: {_time_phrase(e)}."


# ---------------------------------------------------------------- frozen prompts

def load_prompts(prompt_dir=PROMPT_DIR):
    """Returns {file name: (text, sha256)}; refuses if any file differs from prompts.sha256."""
    prompt_dir = Path(prompt_dir)
    expected = {}
    for line in (prompt_dir / "prompts.sha256").read_text().splitlines():
        digest, name = line.split(None, 1)
        expected[name.strip()] = digest
    out = {}
    for name in PROMPT_FILES:
        data = (prompt_dir / name).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if expected.get(name) != digest:
            raise PromptFrozenError(f"{name} does not match prompts.sha256; frozen prompts must not change")
        out[name] = (data.decode("utf-8").rstrip("\n"), digest)
    return out


# ---------------------------------------------------------------- results file

def read_rows(path=RESULTS_PATH):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_row(path, row):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def write_rows(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- local Decider

def check_local_host(host):
    name = host.rsplit(":", 1)[0] if ":" in host else host
    if name not in ("127.0.0.1", "localhost"):
        raise ValueError("the Decider host must be 127.0.0.1 or localhost")


def http_post(url, body, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read())
        except ValueError:
            payload = None
        return e.code, payload


def decider_question(fmt, prompts, f3_accepted):
    if fmt == "F1":
        return {"type": "noul", "instructions": prompts["decider_F1.txt"][0]}
    if fmt == "F2":
        return {"type": "noul", "instructions": prompts["decider_F2.txt"][0]}
    if fmt == "F3" and f3_accepted:
        return {"type": "choice", "instructions": prompts["decider_F3.txt"][0],
                "criteria": {o: None for o in F3_OPTIONS}}
    if fmt == "F3":
        return {"type": "noul", "instructions": prompts["decider_F3_noul.txt"][0]}
    raise ValueError(fmt)


def _prompt_hash(fmt, qtype, prompts):
    name = {"F1": "decider_F1.txt", "F2": "decider_F2.txt",
            "F3": "decider_F3.txt" if qtype == "choice" else "decider_F3_noul.txt"}[fmt]
    return prompts[name][1]


def _score(answer):
    if answer["type"] == "noul":
        return float(answer["noul"])
    return float(answer["probabilities"][F3_OPTIONS[0]])


def ask_decider(post, host, state, question):
    body = {"model": DECIDER_MODEL, "state": state, "questions": {"interrupt": question}}
    status, payload = post(f"http://{host}/v1/systemone", body)
    if status != 200 or not payload:
        raise RuntimeError(f"HTTP {status}")
    return _score(payload["answers"]["interrupt"])


def check_f3(post, host, prompts, path=F3_CHECK_PATH):
    """One call with a made-up state (not a pilot event) to see whether the build accepts `choice`."""
    check_local_host(host)
    question = decider_question("F3", prompts, True)
    body = {"model": DECIDER_MODEL, "state": F3_PROBE_STATE, "questions": {"interrupt": question}}
    try:
        status, payload = post(f"http://{host}/v1/systemone", body)
    except (OSError, ValueError) as exc:
        raise ConnectionError(f"Decider server unreachable ({type(exc).__name__}); nothing recorded") from exc
    accepted = False
    detail = None
    if status == 200 and payload:
        ans = payload.get("answers", {}).get("interrupt", {})
        accepted = ans.get("type") == "choice" and set(ans.get("probabilities", {})) == set(F3_OPTIONS)
        detail = ans
    else:
        detail = payload
    result = {"accepted": accepted, "http_status": status, "response": detail}
    Path(path).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def load_f3_accepted(path=F3_CHECK_PATH):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError("run --check-f3 first")
    return bool(json.loads(path.read_text())["accepted"])


def run_local(events, prompts, f3_accepted, post, host, results_path=RESULTS_PATH, force=False):
    check_local_host(host)
    existing = read_rows(results_path)
    local = [r for r in existing if r["decider"] in ("rules", "decider")]
    if local and not force:
        raise FileExistsError("local results already exist; pass --force to replace them")
    write_rows(results_path, [r for r in existing if r["decider"] not in ("rules", "decider")])
    errors = 0
    for e in events:
        append_row(results_path, {"id": e.id, "decider": "rules", "format": None, "score": None,
                                  "verdict": bool(decide(e)), "error": None})
        state = build_input(e)
        for fmt in FORMATS:
            q = decider_question(fmt, prompts, f3_accepted)
            row = {"id": e.id, "decider": "decider", "format": fmt, "qtype": q["type"], "score": None,
                   "verdict": None, "error": None, "prompt_sha256": _prompt_hash(fmt, q["type"], prompts)}
            start = time.monotonic()
            try:
                row["score"] = ask_decider(post, host, state, q)
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                row["error"] = type(exc).__name__
                errors += 1
            row["latency_s"] = round(time.monotonic() - start, 3)
            append_row(results_path, row)
    return {"events": len(events), "errors": errors}


# ---------------------------------------------------------------- Haiku decider

class CallCounter:
    """Persistent call counter. Reserved BEFORE each request; fails closed on a corrupt file."""

    def __init__(self, path=COUNTER_PATH, cap=CALL_CAP):
        self.path = Path(path)
        self.cap = cap

    def total(self):
        if not self.path.exists():
            return 0
        try:
            total = json.loads(self.path.read_text())["total"]
            if not isinstance(total, int) or total < 0:
                raise ValueError
            return total
        except (ValueError, KeyError, TypeError) as exc:
            raise CounterError(f"{self.path} is unreadable; refusing to call") from exc

    def reserve(self, floor=0):
        total = max(self.total(), floor)
        if total >= self.cap:
            raise CapReached(f"call cap of {self.cap} reached")
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w") as f:
            f.write(json.dumps({"total": total + 1, "cap": self.cap}))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        return total + 1


def haiku_kwargs(prompt):
    # No temperature/top_p/top_k (non-default values return 400 on Haiku 5.5) and no effort:
    # thinking "disabled" is accepted at effort high or below, and the default is medium.
    # Source: https://platform.claude.com/docs/en/build-with-claude/thinking
    return {"model": HAIKU_MODEL, "max_tokens": HAIKU_MAX_TOKENS, "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": prompt}]}


def build_haiku_client(api_key):
    import anthropic  # imported lazily: only --haiku needs the SDK

    return anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=30.0)


def make_haiku_request(client):
    return lambda prompt: client.messages.create(**haiku_kwargs(prompt))


def classify_error(exc):
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    if status in (401, 403) or name in ("AuthenticationError", "PermissionDeniedError"):
        return "auth"
    if status in (402, 429) or name == "RateLimitError" or (status == 400 and "credit" in str(exc).lower()):
        return "quota"
    return "other"


def _response_text(resp):
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


def run_haiku(events, prompts, request, counter, results_path=RESULTS_PATH, limit=None):
    template, digest = prompts["haiku_decider.txt"]
    rows = read_rows(results_path)
    done = {r["id"] for r in rows if r["decider"] == "haiku" and r["error"] is None}
    logged = sum(1 for r in rows if r["decider"] == "haiku")
    made = consecutive = 0
    stopped = None
    for e in events:
        if e.id in done:
            continue
        if limit is not None and made >= limit:
            break
        try:
            counter.reserve(floor=logged)
        except CapReached as exc:
            stopped = str(exc)
            break
        made += 1
        logged += 1
        prompt = template.replace("{event}", build_input(e))
        row = {"id": e.id, "decider": "haiku", "format": None, "score": None, "verdict": None,
               "error": None, "prompt_sha256": digest}
        try:
            resp = request(prompt)
        except Exception as exc:  # any SDK error: log class and status only, never the message
            row["error"] = type(exc).__name__
            row["status"] = getattr(exc, "status_code", None)
            append_row(results_path, row)
            consecutive += 1
            kind = classify_error(exc)
            if kind in ("auth", "quota"):
                stopped = f"{kind} error ({row['error']}); stopping"
                break
            if consecutive >= 2:
                stopped = "two consecutive errors; stopping"
                break
            continue
        consecutive = 0
        text = _response_text(resp).strip()
        word = text.upper()
        row["raw"] = text
        row["invalid"] = word not in ("INTERRUPT", "WAIT")
        row["verdict"] = word == "INTERRUPT"
        row["input_tokens"] = resp.usage.input_tokens
        row["output_tokens"] = resp.usage.output_tokens
        row["cost_usd"] = resp.usage.input_tokens * PRICE_IN + resp.usage.output_tokens * PRICE_OUT
        row["stop_reason"] = resp.stop_reason
        append_row(results_path, row)
    return {"calls": made, "stopped": stopped}


# ---------------------------------------------------------------- CLI

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-f3", action="store_true")
    mode.add_argument("--local", action="store_true")
    mode.add_argument("--haiku", action="store_true")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--limit", type=int)
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    prompts = load_prompts()
    if args.check_f3:
        r = check_f3(http_post, args.host, prompts)
        print(f"choice question accepted: {r['accepted']} (HTTP {r['http_status']}); written to {F3_CHECK_PATH.name}")
        if not r["accepted"]:
            print("F3 will be asked as the frozen noul fallback (decider_F3_noul.txt).")
        return 0
    events = load_events()
    if args.local:
        r = run_local(events, prompts, load_f3_accepted(), http_post, args.host, force=args.force)
        print(f"local run done: {r['events']} events, {r['errors']} Decider errors; results in {RESULTS_PATH.name}")
        return 0

    from headsup.config import load_settings

    key = load_settings().anthropic_api_key
    if not key:
        print("ANTHROPIC_API_KEY is not set (checked via headsup.config)", file=sys.stderr)
        return 2
    counter = CallCounter()
    r = run_haiku(events, prompts, make_haiku_request(build_haiku_client(key)), counter, limit=args.limit)
    rows = [x for x in read_rows() if x["decider"] == "haiku"]
    cost = sum(x.get("cost_usd", 0) for x in rows)
    print(f"haiku calls this run: {r['calls']}; calls used in total: {counter.total()}/{CALL_CAP}; "
          f"logged cost so far: ${cost:.6f}")
    if r["stopped"]:
        print(f"STOPPED: {r['stopped']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
