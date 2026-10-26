"""The one minimal input every decider and the writer see: source, kind, title, time phrase.
Never the body, URL, organiser or attendees."""


def ahead_text(delta):
    minutes = round(delta.total_seconds() / 60)
    if minutes < 90:
        return f"{minutes} min"
    hours = minutes / 60
    if hours < 36:
        return f"{round(hours)} h"
    return f"{round(hours / 24)} days"


def time_phrase(e):
    if e.kind in ("ci_failed", "ci_recovered"):
        return f"CI on branch {e.branch}"
    if e.kind == "release_published":
        return "just published"
    if e.kind in ("pr_opened", "issue_opened"):
        return "opened just now"
    if e.kind == "event_upcoming":
        if e.all_day:
            return f"all-day on {e.start_time:%a}"
        return f"starts in {ahead_text(e.start_time - e.occurred_at)}"
    if e.kind == "event_moved":
        return f"moved, now starts in {ahead_text(e.start_time - e.occurred_at)}"
    if e.kind == "event_cancelled":
        return f"cancelled, was due in {ahead_text(e.start_time - e.occurred_at)}"
    raise ValueError(f"unknown kind {e.kind!r}")


def build_input(e):
    """The one minimal input all three deciders see: source, kind, title, time phrase."""
    return f"Source: {e.source}. Kind: {e.kind}. Title: {e.title}. When: {time_phrase(e)}."
