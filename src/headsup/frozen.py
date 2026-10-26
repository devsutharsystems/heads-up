"""Loads the frozen prompt files from eval/prompts and refuses if any differs from prompts.sha256."""

import hashlib
from pathlib import Path

EVAL_PROMPT_DIR = Path(__file__).resolve().parents[2] / "eval" / "prompts"
PROMPT_FILES = ["decider_F1.txt", "decider_F2.txt", "decider_F3.txt", "decider_F3_noul.txt", "haiku_decider.txt"]


class PromptFrozenError(Exception):
    pass


def load_frozen(prompt_dir=EVAL_PROMPT_DIR):
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
