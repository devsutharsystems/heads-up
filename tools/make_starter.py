"""Turn this repository into the learner `starter`: blank the three LEARNER PARTs.

Run it on the `starter` branch, never on main:  python tools/make_starter.py

For each part, the lines between `# LEARNER PART n START` and `# LEARNER PART n END` are replaced with a docstring
hint and `raise NotImplementedError("LEARNER PART n: ...")`. The marker lines stay, so running it again changes
nothing. Everything else is left untouched. If any marker is missing, duplicated, nested or out of order, it prints
what is wrong and writes nothing. Standard library only; no git command is run (it only reads .git/HEAD).
"""

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# part -> (file, one line for the error, hint lines for the docstring)
PARTS = {
    1: ("src/headsup/policy.py", "the interrupt decision (passes_threshold)",
        ["Return True only when the Decider answered and probability >= threshold.",
         "No answer (None) is never an interrupt: the Decider being down means log only."]),
    2: ("src/headsup/policy.py", "the daily limit (under_daily_limit)",
        ["Return True while fewer than self.limit interrupts are stored for the local day.",
         "Hint: self.interrupts_today(now) already counts them."]),
    3: ("src/headsup/actions.py", "tap-to-approve (handle_update)",
        ["Decide what one Telegram tap may do, then do it. Refuse and log (self._refuse) a tap from the wrong chat or",
         "sender, an unknown token, an action outside ALLOWED_ACTIONS, a token older than TOKEN_TTL; answer",
         "'already_done' for a repeated (event, action). Otherwise act (snooze, remind, or the one GitHub re-run",
         "through self.writer), record_tap, log_tap('done'), answer the callback, drop the keyboard, return 'done'."]),
}

MARKER = re.compile(r"^(?P<indent>[ \t]*)# LEARNER PART (?P<n>\d+) (?P<kind>START|END)[ \t]*$")


class StarterError(Exception):
    pass


def find_regions(path, text, parts):
    """{n: (start_line_index, end_line_index, indent)} for the parts expected in this file; raises StarterError."""
    lines = text.splitlines(keepends=True)
    seen = {}
    for i, line in enumerate(lines):
        m = MARKER.match(line.rstrip("\r\n"))
        if not m:
            continue
        n, kind = int(m["n"]), m["kind"]
        if n not in parts:
            raise StarterError(f"{path}: marker for LEARNER PART {n} does not belong in this file (line {i + 1})")
        slot = seen.setdefault(n, {})
        if kind in slot:
            raise StarterError(f"{path}: LEARNER PART {n} {kind} appears twice (line {i + 1})")
        slot[kind] = (i, m["indent"])
    regions = {}
    for n in parts:
        slot = seen.get(n, {})
        for kind in ("START", "END"):
            if kind not in slot:
                raise StarterError(f"{path}: missing '# LEARNER PART {n} {kind}'")
        (a, indent), (b, end_indent) = slot["START"], slot["END"]
        if b < a:
            raise StarterError(f"{path}: LEARNER PART {n} END comes before START")
        if indent != end_indent:
            raise StarterError(f"{path}: LEARNER PART {n} START and END are indented differently")
        regions[n] = (a, b, indent)
    ordered = sorted(regions.items(), key=lambda kv: kv[1][0])
    for (n1, (_, end1, _)), (n2, (start2, _, _)) in zip(ordered, ordered[1:]):
        if start2 < end1:
            raise StarterError(f"{path}: LEARNER PART {n1} and LEARNER PART {n2} overlap")
    return lines, regions


def stub(n, indent):
    _, one_line, hint = PARTS[n]
    body = [f'{indent}"""LEARNER PART {n}: ' + hint[0]] + [f"{indent}{h}" for h in hint[1:]] + [f'{indent}"""']
    body.append(f'{indent}raise NotImplementedError("LEARNER PART {n}: {one_line}")')
    return [line + "\n" for line in body]


def make_starter_text(path, text, parts):
    lines, regions = find_regions(path, text, parts)
    for n, (a, b, indent) in sorted(regions.items(), key=lambda kv: -kv[1][0]):  # bottom first: indexes stay valid
        lines[a + 1:b] = stub(n, indent)
    new = "".join(lines)
    try:
        ast.parse(new)
    except SyntaxError as exc:
        raise StarterError(f"{path}: the result would not be valid Python ({exc.msg}, line {exc.lineno})") from None
    return new


def on_main_branch(root):
    head = Path(root, ".git", "HEAD")
    if not head.is_file():
        return False
    return head.read_text(encoding="utf-8").strip() in ("ref: refs/heads/main", "ref: refs/heads/master")


def run(root, allow_main=False, out=print):
    """Returns 0 (done, or nothing to change) or 2 (refused). Nothing is written unless every file checks out."""
    root = Path(root)
    if on_main_branch(root) and not allow_main:
        out("refused: this looks like the main branch. Switch to the starter branch first (or pass --allow-main).")
        return 2
    by_file = {}
    for n, (rel, _, _) in PARTS.items():
        by_file.setdefault(rel, {})[n] = PARTS[n]
    results = {}
    try:
        for rel, parts in by_file.items():
            path = root / rel
            if not path.is_file():
                raise StarterError(f"{rel}: file not found")
            old = path.read_text(encoding="utf-8")
            results[path] = (old, make_starter_text(rel, old, parts))
    except StarterError as exc:
        out(f"refused: {exc}")
        return 2
    changed = [p for p, (old, new) in results.items() if old != new]
    for path in changed:
        path.write_text(results[path][1], encoding="utf-8")
    if changed:
        out("blanked LEARNER PARTs 1 to 3 in: " + ", ".join(str(p.relative_to(root)) for p in changed))
    else:
        out("already a starter: no change")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Blank the three LEARNER PARTs (run on the starter branch).")
    ap.add_argument("--root", default=str(ROOT), help="repository folder (default: this repository)")
    ap.add_argument("--allow-main", action="store_true", help="run even though .git/HEAD says main or master")
    args = ap.parse_args(argv)
    return run(args.root, allow_main=args.allow_main)


if __name__ == "__main__":
    sys.exit(main())
