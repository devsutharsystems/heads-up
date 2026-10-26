"""Fixtures for the main-evaluation tests: a tmp copy of the real registered files and fake Decider / Haiku callables.
Nothing here touches the network, Ollama, Anthropic or .env."""

import csv
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from eval import pilot
from eval import run_main as rm
from headsup.state import build_input

ROOT = Path(__file__).resolve().parent.parent
FILES = ["eval/MAIN_EVAL_PLAN.md", "eval/main_events.jsonl", "eval/main_split.json", "eval/main_label_sheet.csv", "eval/main_pools.py",
         "eval/gen_synthetic.py", "eval/baseline_rules.py", "eval/rubric.md", "eval/f3_check.json", "eval/modelcard_limitations.txt",
         "eval/pilot_results.jsonl", "eval/haiku_call_counter.json"]


def tree(tmp_path):
    """A copy of the registered files (events, split, labelled sheet, plan, rules, rubric, prompts, pilot results)."""
    for rel in FILES:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, tmp_path / rel)
    shutil.copytree(ROOT / "eval" / "prompts", tmp_path / "eval" / "prompts")
    return rm.Paths(tmp_path)


def sheet_labels(paths):
    return {r["id"]: int(r["label"]) for r in csv.DictReader(open(paths.sheet, newline=""))}


def _h(*parts):
    return int(hashlib.md5("|".join(parts).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


class FakeDecider:
    """Stands in for Ollama. Scores follow the labels with some noise, differently per format."""

    def __init__(self, paths, down=False, scorer=None):
        self.events = {build_input(e): e for e in pilot.load_events(paths.events)}
        prompts = pilot.load_prompts(paths.prompts_dir)
        self.fmt_of = {prompts["decider_F1.txt"][0]: "F1", prompts["decider_F2.txt"][0]: "F2", prompts["decider_F3.txt"][0]: "F3"}
        self.labels = sheet_labels(paths)
        self.calls = []  # (event id, format)
        self.down, self.scorer = down, scorer

    def score(self, fmt, e):
        if self.scorer:
            return self.scorer(fmt, e)
        flip = {"F1": 7, "F2": 5, "F3": 9}[fmt]
        label = self.labels[e.id]
        if int(_h(e.id, "flip") * 1000) % flip == 0:
            label = 1 - label
        h = _h(e.id, fmt)
        return 0.5 + 0.45 * h if label else 0.05 + 0.4 * h

    def __call__(self, url, body):
        q = body["questions"]["interrupt"]
        fmt = self.fmt_of[q["instructions"]]
        e = self.events[body["state"]]
        self.calls.append((e.id, fmt))
        if self.down:
            raise OSError("down")
        p = self.score(fmt, e)
        if q["type"] == "noul":
            return 200, {"answers": {"interrupt": {"type": "noul", "noul": p}}}
        return 200, {"answers": {"interrupt": {"type": "choice", "choice": "can wait", "confidence": 0.5,
                                               "probabilities": {"interrupt now": p, "can wait": 1 - p}}}}


class FakeHaiku:
    """A request callable: INTERRUPT for events whose label is 1 except a few, WAIT otherwise."""

    def __init__(self, paths, text=None, fail_after=None):
        self.events = {build_input(e): e for e in pilot.load_events(paths.events)}
        self.labels = sheet_labels(paths)
        self.prompts = []
        self.text, self.fail_after = text, fail_after

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if self.fail_after is not None and len(self.prompts) > self.fail_after:
            raise PermissionError("auth")
        e = next(ev for state, ev in self.events.items() if state in prompt)
        word = self.text or ("INTERRUPT" if self.labels[e.id] and _h(e.id, "haiku") > 0.4 else "WAIT")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=word)],
                               usage=SimpleNamespace(input_tokens=80, output_tokens=2), stop_reason="end_turn")


def run(argv, paths, **kw):
    out = []
    code = rm.main(argv, paths=paths, out=out.append, **kw)
    return code, out


def rows(paths):
    return pilot.read_rows(paths.results)


def split_ids(paths):
    s = json.loads(paths.split.read_text())
    return set(s["tune"]), set(s["test"])
