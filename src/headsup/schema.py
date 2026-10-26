"""One shape for every source (PLAN.md section 3). `body` is untrusted text."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Event:
    id: str
    source: str
    kind: str
    title: str
    body: str
    url: str
    occurred_at: datetime
    fetched_at: datetime
    content_hash: str
    branch: str | None = None
    start_time: datetime | None = None
    all_day: bool = False
    is_default_branch: bool | None = None  # ci_failed / ci_recovered only


@dataclass
class Decision:
    event_id: str
    verdict: str  # "interrupt" or "log_only"
    reason: str
    decided_at: datetime
    probability: float | None = None
    threshold: float | None = None
