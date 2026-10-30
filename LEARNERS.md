# For learners: the starter branch

The `starter` branch is Heads-Up with three parts left blank. You write them; the tests tell you when each one is right.
Run every command from the project folder, with the virtual environment from the README.

**Get the starter branch:** `git switch starter`.

**How the branch was made** (only needed if you want to rebuild it: run this on a branch called `starter`, never on `main`):

```sh
python tools/make_starter.py
```

The script replaces the code between each pair of `# LEARNER PART n START` / `END` comments with a hint and
`raise NotImplementedError("LEARNER PART n: ...")`. Run it again and nothing changes. It refuses to run if a marker
is missing or unbalanced, or if the checkout is on `main`.

Until all three parts are written, the tests that run a whole pass through them fail on purpose (for example the end-to-end
tests in `tests/test_cli.py`). That is expected. Work through the parts **in order**: the tests for part 2 call part 1.

## Task 1: the interrupt decision (`src/headsup/policy.py`, `passes_threshold`)

Return `True` only when the Decider answered and `probability >= threshold`. If there is no answer (`None`) the answer is
never "interrupt": a Decider that is down means log only, not a blind alert.

```sh
.venv/bin/python -m pytest -m learner_part1 -q
```

Done when all 3 tests pass.

## Task 2: the daily limit (`src/headsup/policy.py`, `under_daily_limit`)

Return `True` while fewer than `self.limit` interrupts are stored for today. "Today" is the local day (Asia/Kolkata), not
the UTC day, and events that were only logged do not count. `self.interrupts_today(now)` already counts them for you.

```sh
.venv/bin/python -m pytest -m learner_part2 -q
```

Done when all 3 tests pass (task 1 must already be written).

## Task 3: tap-to-approve (`src/headsup/actions.py`, `ActionHandler.handle_update`)

Decide what one Telegram button tap may do, then do it. A tap is refused, logged and answered if it comes from the wrong
chat or sender, has an unknown token, names an action outside `ALLOWED_ACTIONS`, or is older than `TOKEN_TTL`. A second tap
on the same action for the same event answers "already done". Otherwise do the action (snooze, remind, or the one GitHub
re-run), record it, log it as `done`, answer the tap and remove the buttons. The text of an event must never be able to
add or trigger an action.

```sh
.venv/bin/python -m pytest -m learner_part3 -q
```

Done when all 10 tests pass, including `test_no_write_request_without_allowlisted_callback`: with no valid tap there must
be zero write requests to GitHub.

## When all three are written

```sh
.venv/bin/python -m pytest -q
```

Everything should pass. Then run the secrets scan on the files you changed:
`.venv/bin/detect-secrets-hook --baseline .secrets.baseline src/headsup/policy.py src/headsup/actions.py`.

## Exercise: label the 30 pilot events yourself (about 30 minutes)

The evaluation in this repo uses synthetic events labelled by one person (Dev). Label the same 30 events yourself and see how the deciders look against your labels.

1. Copy the blank sheet: `cp eval/learner_label_sheet.csv my_labels.csv`. It has the same 30 events and columns as the pilot sheet, with the `label` column empty.
2. Read `eval/rubric.md` first. Write your own persona (who you are for these repos) at the top of your notes before you start. Do not open `eval/pilot_label_sheet.csv` or `eval/pilot_results.jsonl` until you are done: they hold Dev's labels and the deciders' answers.
3. Fill in `label` for every row: **1** if waiting until evening would cause real harm or a missed commitment, **0** if it can wait. Use the rubric's tie-breaker (when unsure, label 0).
4. Score your labels. This is offline: it reads the saved results in `eval/pilot_results.jsonl` and makes no API, Ollama or network call.

   ```sh
   .venv/bin/python -m eval.score_my_labels my_labels.csv
   ```

   It prints precision, recall and F1 against your labels for the frozen rules, the Decider (formats F1, F2 and F3, at the threshold 0.40) and Haiku, and how many of your labels differ from Dev's. A blank or invalid label stops it with the row's id.
5. Write down what you notice, for example which events you and Dev labelled differently. With 30 events your numbers are very uncertain: say so, as the report does. Compare with [`eval/EVAL_REPORT.md`](eval/EVAL_REPORT.md).

### Optional extension: label 30 to 50 of your own real events

Run Heads-Up for a few days on your own repos and calendar (`--once`), so that `data/headsup.db` fills up. The very first run after setup only records what is already there and logs it without scoring it (reason "first run", no probability), so let some days of new events arrive and export from those. Export 30 to 50 events with their Decider probability. This snippet writes `my_real_labels.csv` with an empty `label` column:

```python
import csv, json, sqlite3
db = sqlite3.connect("file:data/headsup.db?mode=ro", uri=True)
rows = db.execute("SELECT event_id, event_json FROM seen_events WHERE event_id IN (SELECT event_id FROM decisions WHERE probability IS NOT NULL) "
                  "ORDER BY first_seen_at DESC LIMIT 50").fetchall()  # scored events only: the first run's are not scored
prob = dict(db.execute("SELECT event_id, probability FROM decisions"))
with open("my_real_labels.csv", "w", newline="") as f:
    out = csv.writer(f)
    out.writerow(["id", "source", "kind", "title", "probability", "label"])
    for event_id, raw in rows:
        e = json.loads(raw)
        out.writerow([event_id, e["source"], e["kind"], e["title"], prob.get(event_id), ""])
```

Do not share the CSV or the database: titles can be private. Label with the same rubric, without looking at the probability column. Then count, at a threshold of 0.40: true positives (label 1 and probability at least 0.40), false positives and false negatives. Precision is TP / (TP + FP), recall is TP / (TP + FN). Compare with the rules in `eval/baseline_rules.py`. The scorer above does not read this file.
