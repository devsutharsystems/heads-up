"""SQLite state: seen events, decisions, button taps, cost log, calendar starts, ETags, CI state.

All SQL is parameterised. Datetimes are stored as ISO-8601 UTC strings.
"""

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from headsup.schema import Decision, Event

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "headsup.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_events (
    content_hash TEXT PRIMARY KEY, event_id TEXT NOT NULL, source TEXT NOT NULL,
    kind TEXT NOT NULL, first_seen_at TEXT NOT NULL, event_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, verdict TEXT NOT NULL,
    reason TEXT NOT NULL, probability REAL, threshold REAL, decided_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS button_taps (
    callback_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, action TEXT NOT NULL, tapped_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cost_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, purpose TEXT NOT NULL, model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cost_usd REAL NOT NULL);
CREATE TABLE IF NOT EXISTS calendar_starts (
    key TEXT PRIMARY KEY, start_utc TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calendar_reminded (key TEXT PRIMARY KEY, reminded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS etags (url TEXT PRIMARY KEY, etag TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ci_state (
    repo TEXT NOT NULL, branch TEXT NOT NULL, last_conclusion TEXT NOT NULL, PRIMARY KEY (repo, branch));
"""


def _iso(dt):
    """ISO-8601 in UTC (aware datetimes are converted; naive ones are assumed to be UTC already)."""
    if dt is None:
        return None
    return (dt.astimezone(timezone.utc) if dt.tzinfo else dt).isoformat()


def _now():
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, path=DEFAULT_PATH):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self):
        self._db.close()

    # ------------------------------------------------------------ seen events
    def add_if_new(self, event: Event) -> bool:
        """Record the event; False if its content_hash was already seen (the dedupe)."""
        d = asdict(event)
        for k in ("occurred_at", "fetched_at", "start_time"):
            d[k] = _iso(d[k])
        cur = self._db.execute(
            "INSERT OR IGNORE INTO seen_events VALUES (?,?,?,?,?,?)",
            (event.content_hash, event.id, event.source, event.kind, _iso(_now()), json.dumps(d)))
        self._db.commit()
        return cur.rowcount == 1

    def seen_count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM seen_events").fetchone()[0]

    # -------------------------------------------------------------- decisions
    def record_decision(self, d: Decision):
        self._db.execute(
            "INSERT INTO decisions (event_id, verdict, reason, probability, threshold, decided_at) VALUES (?,?,?,?,?,?)",
            (d.event_id, d.verdict, d.reason, d.probability, d.threshold, _iso(d.decided_at)))
        self._db.commit()

    def decisions(self, event_id=None):
        q, args = "SELECT event_id, verdict, reason, probability, threshold FROM decisions", ()
        if event_id is not None:
            q, args = q + " WHERE event_id = ?", (event_id,)
        return self._db.execute(q + " ORDER BY id", args).fetchall()

    def interrupts_between(self, start, end) -> int:
        """Number of stored 'interrupt' decisions with start <= decided_at < end (aware datetimes)."""
        rows = self._db.execute("SELECT decided_at FROM decisions WHERE verdict = 'interrupt'").fetchall()
        return sum(1 for (t,) in rows if start <= datetime.fromisoformat(t) < end)

    # ----------------------------------------------------------------- taps
    def record_tap(self, callback_id, event_id, action, tapped_at=None) -> bool:
        """Idempotent on callback_id: a repeated tap returns False and changes nothing."""
        cur = self._db.execute("INSERT OR IGNORE INTO button_taps VALUES (?,?,?,?)",
                               (callback_id, event_id, action, _iso(tapped_at or _now())))
        self._db.commit()
        return cur.rowcount == 1

    def taps(self, event_id):
        return self._db.execute("SELECT callback_id, action FROM button_taps WHERE event_id = ? ORDER BY tapped_at",
                                (event_id,)).fetchall()

    # ------------------------------------------------------------------ cost
    def log_cost(self, purpose, model, input_tokens, output_tokens, cost_usd, at=None):
        self._db.execute("INSERT INTO cost_log (at, purpose, model, input_tokens, output_tokens, cost_usd) VALUES (?,?,?,?,?,?)",
                         (_iso(at or _now()), purpose, model, input_tokens, output_tokens, cost_usd))
        self._db.commit()

    def cost_for_day(self, day: str, purpose=None) -> float:
        q, args = "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_log WHERE substr(at, 1, 10) = ?", [day]
        if purpose is not None:
            q, args = q + " AND purpose = ?", args + [purpose]
        return self._db.execute(q, args).fetchone()[0]

    def costs_between(self, start, end):
        """[(purpose, cost_usd)] for cost rows with start <= at < end (aware datetimes)."""
        rows = self._db.execute("SELECT at, purpose, cost_usd FROM cost_log").fetchall()
        return [(p, c) for t, p, c in rows if start <= datetime.fromisoformat(t) < end]

    # ------------------------------------------------------- calendar starts
    def get_calendar_start(self, key):
        row = self._db.execute("SELECT start_utc FROM calendar_starts WHERE key = ?", (key,)).fetchone()
        return None if row is None else datetime.fromisoformat(row[0])

    def set_calendar_start(self, key, start: datetime):
        self._db.execute("INSERT OR REPLACE INTO calendar_starts VALUES (?,?,?)", (key, _iso(start), _iso(_now())))
        self._db.commit()

    def was_reminded(self, key) -> bool:
        return self._db.execute("SELECT 1 FROM calendar_reminded WHERE key = ?", (key,)).fetchone() is not None

    def mark_reminded(self, key):
        self._db.execute("INSERT OR IGNORE INTO calendar_reminded VALUES (?,?)", (key, _iso(_now())))
        self._db.commit()

    # ---------------------------------------------------------------- etags
    def get_etag(self, url):
        row = self._db.execute("SELECT etag FROM etags WHERE url = ?", (url,)).fetchone()
        return None if row is None else row[0]

    def set_etag(self, url, etag):
        self._db.execute("INSERT OR REPLACE INTO etags VALUES (?,?)", (url, etag))
        self._db.commit()

    # ------------------------------------------------------------- CI state
    def get_ci_state(self, repo, branch):
        row = self._db.execute("SELECT last_conclusion FROM ci_state WHERE repo = ? AND branch = ?",
                               (repo, branch)).fetchone()
        return None if row is None else row[0]

    def set_ci_state(self, repo, branch, conclusion):
        self._db.execute("INSERT OR REPLACE INTO ci_state VALUES (?,?,?)", (repo, branch, conclusion))
        self._db.commit()
