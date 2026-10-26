"""Drop events already seen (PLAN.md section 3: hash of source + id + content)."""


def fresh(events, store):
    """The events whose content_hash is new; each one is recorded as seen."""
    return [e for e in events if store.add_if_new(e)]
