"""Collapse repeated CI failures before the policy sees them.

Failures are grouped by (repo, workflow, branch). A failure is logged only when
  - a later ci_recovered exists for the same key, in this batch or already stored ("already recovered"), or
  - a newer failure with the same key is in this batch (only the newest of a batch may interrupt), or
  - an earlier failure with the same key was already alerted and nothing recovered since ("already alerted ...").
Only the log_only reason is decided here; the Decider, the threshold and the daily limit are untouched.
"""

ALREADY_RECOVERED = "already recovered"
ALREADY_ALERTED = "already alerted for this failing workflow"
NEWER_IN_BATCH = "a newer failure of this workflow is in the same batch"

_PREFIXES = {"ci_failed": "CI failed: ", "ci_recovered": "CI recovered: "}


def ci_key(event):
    """(repo, workflow, branch) for a ci_failed or ci_recovered event, else None.
    The source puts the workflow in the title as '<label>: <workflow> on <branch>'."""
    prefix = _PREFIXES.get(event.kind)
    if prefix is None:
        return None
    name = event.title[len(prefix):] if event.title.startswith(prefix) else event.title
    suffix = f" on {event.branch}"
    if event.branch and name.endswith(suffix):
        name = name[: -len(suffix)]
    repo = event.id.split(":")[1] if event.id.count(":") >= 3 else ""
    return repo, name, event.branch


def collapse_reasons(batch, store):
    """{content_hash: reason} for the ci_failed events of `batch` that must be logged only."""
    failures = [e for e in batch if e.kind == "ci_failed"]
    if not failures:
        return {}
    recovered = {}
    for e in list(store.events_of_kind("ci_recovered")) + [e for e in batch if e.kind == "ci_recovered"]:
        recovered.setdefault(ci_key(e), []).append(e.occurred_at)
    alerted = {}
    for e in store.alerted_events("ci_failed"):
        alerted.setdefault(ci_key(e), []).append(e.occurred_at)
    in_batch = {}
    for e in failures:
        in_batch.setdefault(ci_key(e), []).append(e)

    reasons = {}
    for e in failures:
        key = ci_key(e)
        recoveries = recovered.get(key, [])
        if any(r > e.occurred_at for r in recoveries):
            reasons[e.content_hash] = ALREADY_RECOVERED
        elif any((o.occurred_at, o.content_hash) > (e.occurred_at, e.content_hash) for o in in_batch[key]):
            reasons[e.content_hash] = NEWER_IN_BATCH
        else:
            earlier = [t for t in alerted.get(key, []) if t <= e.occurred_at]
            if earlier and not any(max(earlier) < r < e.occurred_at for r in recoveries):
                reasons[e.content_hash] = ALREADY_ALERTED
    return reasons
