"""Score YOUR labels of the 30 pilot events against the saved decider results. Offline: no API, Ollama or network call.

Usage (from the project folder):
  .venv/bin/python -m eval.score_my_labels path/to/my_sheet.csv

Copy eval/learner_label_sheet.csv, fill the `label` column (1 = waiting until evening would cause real harm or a missed
commitment, 0 = it can wait; see eval/rubric.md), then run this on your copy. The results come from eval/pilot_results.jsonl.
Decider rows use the fixed threshold 0.40; a missing score never counts as an alert.
"""

import csv
import json
import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parent
RESULTS_PATH = EVAL / "pilot_results.jsonl"
DEV_SHEET_PATH = EVAL / "pilot_label_sheet.csv"
THRESHOLD = 0.40
COLUMNS = (("rules", "rules", None), ("Decider F1", "decider", "F1"), ("Decider F2", "decider", "F2"),
           ("Decider F3", "decider", "F3"), ("Haiku", "haiku", None))


def load_labels(path):
    labels = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["label"].strip() not in ("0", "1"):
                raise ValueError(f"{row['id']}: label must be 0 or 1, got {row['label']!r}")
            labels[row["id"]] = int(row["label"])
    return labels


def load_results(path=None):
    """{(id, decider, format): row}; a later row wins unless it is an error and the earlier one is not."""
    out = {}
    for line in Path(path or RESULTS_PATH).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        key = (r["id"], r["decider"], r["format"])
        if key not in out or r["error"] is None or out[key]["error"] is not None:
            out[key] = r
    return out


def verdicts(results, ids, decider, fmt):
    out = {}
    for i in ids:
        r = results.get((i, decider, fmt))
        if r is None or r["error"] is not None:
            out[i] = False
        elif decider == "decider":
            out[i] = r["score"] is not None and r["score"] >= THRESHOLD
        else:
            out[i] = bool(r["verdict"])
    return out


def confusion(alerts, labels):
    c = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for i, label in labels.items():
        v = bool(alerts.get(i))
        c[("t" if v == bool(label) else "f") + ("p" if v else "n")] += 1
    return c


def metrics(c):
    p = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else None
    r = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else None
    d = 2 * c["tp"] + c["fp"] + c["fn"]
    return p, r, (2 * c["tp"] / d if d else 0.0)


def differences(mine, dev):
    return sum(1 for i in mine if mine[i] != dev.get(i))


def _f(x):
    return "n/a" if x is None else f"{x:.3f}"


def render(mine, dev, results):
    ids = list(mine)
    lines = [f"Scored {len(ids)} events against your labels ({sum(mine.values())} labelled 1).",
             f"Your labels differ from Dev's on {differences(mine, dev)} of {len(ids)} events.",
             f"Decider rows use threshold {THRESHOLD:.2f}.", "",
             "| decider | TP | FP | FN | precision | recall | F1 |", "|---|---|---|---|---|---|---|"]
    for name, decider, fmt in COLUMNS:
        c = confusion(verdicts(results, ids, decider, fmt), mine)
        p, r, f1 = metrics(c)
        lines.append(f"| {name} | {c['tp']} | {c['fp']} | {c['fn']} | {_f(p)} | {_f(r)} | {_f(f1)} |")
    lines += ["", "With 30 events these numbers are very uncertain; small differences mean little."]
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m eval.score_my_labels path/to/my_sheet.csv", file=sys.stderr)
        return 2
    try:
        mine = load_labels(argv[0])
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(render(mine, load_labels(DEV_SHEET_PATH), load_results()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
