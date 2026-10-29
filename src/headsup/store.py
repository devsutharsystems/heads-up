"""SQLite state: seen events, decisions, alerts and button tokens, taps, reminders, cost log, calendar starts, ETags, CI state.

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
CREATE TABLE IF NOT EXISTS ci_runs (
    repo TEXT NOT NULL, workflow TEXT NOT NULL, branch TEXT NOT NULL, failed_at TEXT NOT NULL,
    open INTEGER NOT NULL, recovered_at TEXT, PRIMARY KEY (repo, workflow, branch));
CREATE UNIQUE INDEX IF NOT EXISTS ux_button_taps_event_action ON button_taps (event_id, action);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (
    event_hash TEXT PRIMARY KEY, event_id TEXT NOT NULL, text TEXT NOT NULL,
    status TEXT NOT NULL, sent_at TEXT);
CREATE TABLE IF NOT EXISTS alert_tokens (
    token TEXT PRIMARY KEY, event_hash TEXT NOT NULL, action TEXT NOT NULL, message_id INTEGER,
    created_at TEXT NOT NULL, keyboard_removed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS reminders (event_hash TEXT PRIMARY KEY, due_at TEXT NOT NULL, sent_at TEXT);
CREATE TABLE IF NOT EXISTS tap_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, token TEXT, chat_id INTEGER, sender_id INTEGER,
    event_id TEXT, action TEXT, outcome TEXT NOT NULL);
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

    def copy_to_memory(self):
        """An in-memory copy (for --dry-run): changes to it never reach the file."""
        other = Store(":memory:")
        self._db.backup(other._db)
        return other

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

    @staticmethod
    def _event_from_json(text):
        d = json.loads(text)
        for k in ("occurred_at", "fetched_at", "start_time"):
            if d[k] is not None:
                d[k] = datetime.fromisoformat(d[k])
        return Event(**d)

    def get_event(self, event_hash):
        row = self._db.execute("SELECT event_json FROM seen_events WHERE content_hash = ?", (event_hash,)).fetchone()
        return None if row is None else self._event_from_json(row[0])

    def events_of_kind(self, kind):
        rows = self._db.execute("SELECT event_json FROM seen_events WHERE kind = ?", (kind,)).fetchall()
        return [self._event_from_json(r[0]) for r in rows]

    def alerted_events(self, kind):
        """Events of `kind` that have an alert row (sent or still pending)."""
        rows = self._db.execute("SELECT s.event_json FROM seen_events s JOIN alerts a ON a.event_hash = s.content_hash "
                                "WHERE s.kind = ?", (kind,)).fetchall()
        return [self._event_from_json(r[0]) for r in rows]

    def has_events_from(self, source) -> bool:
        """True if any event of this Event.source ("github", "calendar") was ever stored."""
        return self._db.execute("SELECT 1 FROM seen_events WHERE source = ? LIMIT 1", (source,)).fetchone() is not None

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

    # ------------------------------------------------------------------- kv
    def get_kv(self, key):
        row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else row[0]

    def set_kv(self, key, value):
        self._db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, str(value)))
        self._db.commit()

    def snooze_until(self):
        raw = self.get_kv("snooze_until")
        return None if raw is None else datetime.fromisoformat(raw)

    def set_snooze_until(self, when):
        self.set_kv("snooze_until", _iso(when))

    # --------------------------------------------------------------- alerts
    def save_alert(self, event_hash, event_id, text) -> bool:
        """One alert per event content; False if it already exists (its status is kept)."""
        cur = self._db.execute("INSERT OR IGNORE INTO alerts VALUES (?,?,?,'pending',NULL)", (event_hash, event_id, text))
        self._db.commit()
        return cur.rowcount == 1

    def get_alert(self, event_hash):
        return self._db.execute("SELECT event_id, text, status FROM alerts WHERE event_hash = ?", (event_hash,)).fetchone()

    def mark_alert_sent(self, event_hash, at=None):
        self._db.execute("UPDATE alerts SET status = 'sent', sent_at = ? WHERE event_hash = ?", (_iso(at or _now()), event_hash))
        self._db.commit()

    def pending_alerts(self):
        return [r[0] for r in self._db.execute("SELECT event_hash FROM alerts WHERE status = 'pending' ORDER BY rowid")]

    def add_token(self, token, event_hash, action, created_at):
        self._db.execute("INSERT INTO alert_tokens (token, event_hash, action, created_at) VALUES (?,?,?,?)",
                         (token, event_hash, action, _iso(created_at)))
        self._db.commit()

    def discard_unsent_tokens(self, event_hash):
        self._db.execute("DELETE FROM alert_tokens WHERE event_hash = ? AND message_id IS NULL", (event_hash,))
        self._db.commit()

    def attach_message(self, event_hash, message_id):
        """Tokens created for a send that has no message yet now belong to that message."""
        self._db.execute("UPDATE alert_tokens SET message_id = ? WHERE event_hash = ? AND message_id IS NULL",
                         (message_id, event_hash))
        self._db.commit()

    def get_token(self, token):
        row = self._db.execute("SELECT token, event_hash, action, message_id, created_at, keyboard_removed FROM alert_tokens "
                               "WHERE token = ?", (token,)).fetchone()
        if row is None:
            return None
        return {"token": row[0], "event_hash": row[1], "action": row[2], "message_id": row[3],
                "created_at": datetime.fromisoformat(row[4]), "keyboard_removed": bool(row[5])}

    def mark_keyboard_removed(self, message_id):
        self._db.execute("UPDATE alert_tokens SET keyboard_removed = 1 WHERE message_id = ?", (message_id,))
        self._db.commit()

    def live_keyboards(self):
        """[(message_id, created_at)] for messages whose buttons have not been removed yet."""
        rows = self._db.execute("SELECT message_id, MIN(created_at) FROM alert_tokens WHERE message_id IS NOT NULL "
                                "AND keyboard_removed = 0 GROUP BY message_id").fetchall()
        return [(m, datetime.fromisoformat(c)) for m, c in rows]

    # ------------------------------------------------------------ reminders
    def add_reminder(self, event_hash, due_at) -> bool:
        """At most one reminder per event: False if one was already requested."""
        cur = self._db.execute("INSERT OR IGNORE INTO reminders VALUES (?,?,NULL)", (event_hash, _iso(due_at)))
        self._db.commit()
        return cur.rowcount == 1

    def due_reminders(self, now):
        rows = self._db.execute("SELECT event_hash, due_at FROM reminders WHERE sent_at IS NULL ORDER BY due_at").fetchall()
        return [h for h, due in rows if datetime.fromisoformat(due) <= now]

    def mark_reminder_sent(self, event_hash, at=None):
        self._db.execute("UPDATE reminders SET sent_at = ? WHERE event_hash = ?", (_iso(at or _now()), event_hash))
        self._db.commit()

    # -------------------------------------------------------------- tap log
    def log_tap(self, outcome, token=None, chat_id=None, sender_id=None, event_id=None, action=None, at=None):
        self._db.execute("INSERT INTO tap_log (at, token, chat_id, sender_id, event_id, action, outcome) VALUES (?,?,?,?,?,?,?)",
                         (_iso(at or _now()), token, chat_id, sender_id, event_id, action, outcome))
        self._db.commit()

    def tap_log(self):
        return self._db.execute("SELECT outcome, token, chat_id, sender_id, event_id, action FROM tap_log ORDER BY id").fetchall()

    def has_tap(self, event_id, action) -> bool:
        return self._db.execute("SELECT 1 FROM button_taps WHERE event_id = ? AND action = ?", (event_id, action)).fetchone() is not None

    # ----------------------------------------------------------------- taps
    def record_tap(self, callback_id, event_id, action, tapped_at=None) -> bool:
        """Idempotent on callback_id and on (event_id, action): a repeat returns False and changes nothing."""
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

    def cost_total(self) -> float:
        return self._db.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM cost_log").fetchone()[0]

    # ------------------------------------------------------- dashboard reads
    def recent_decisions(self, limit=50):
        """Newest decisions first: dicts with time, source, kind, title, probability, verdict, reason (no event bodies)."""
        rows = self._db.execute(
            "SELECT d.decided_at, s.source, s.kind, json_extract(s.event_json, '$.title'), d.probability, d.verdict, d.reason "
            "FROM decisions d LEFT JOIN seen_events s ON s.content_hash = (SELECT content_hash FROM seen_events "
            "WHERE event_id = d.event_id ORDER BY first_seen_at DESC LIMIT 1) ORDER BY d.id DESC LIMIT ?", (int(limit),)).fetchall()
        keys = ("time", "source", "kind", "title", "probability", "verdict", "reason")
        return [dict(zip(keys, r)) for r in rows]

    def alert_rows(self, limit=20):
        """Newest alerts first, with button facts and no token values: status, sent_at, title, newest button time,
        whether every keyboard was removed, and the actions tapped for the event."""
        rows = self._db.execute(
            "SELECT a.event_hash, a.event_id, a.status, a.sent_at, json_extract(s.event_json, '$.title'), s.kind, "
            "(SELECT MAX(created_at) FROM alert_tokens t WHERE t.event_hash = a.event_hash), "
            "(SELECT MIN(keyboard_removed) FROM alert_tokens t WHERE t.event_hash = a.event_hash AND t.message_id IS NOT NULL), "
            "(SELECT group_concat(action) FROM button_taps b WHERE b.event_id = a.event_id) "
            "FROM alerts a LEFT JOIN seen_events s ON s.content_hash = a.event_hash ORDER BY a.rowid DESC LIMIT ?",
            (int(limit),)).fetchall()
        keys = ("event_hash", "event_id", "status", "sent_at", "title", "kind", "buttons_at", "keyboards_removed", "taps")
        out = []
        for r in rows:
            d = dict(zip(keys, r))
            d["taps"] = d["taps"].split(",") if d["taps"] else []
            d["keyboards_removed"] = bool(d["keyboards_removed"])
            out.append(d)
        return out

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

    # ------------------------------------------- CI failures per (repo, workflow, branch)
    def get_ci_run(self, repo, workflow, branch):
        """(failed_at, is_open, recovered_at) for the key, or None. failed_at is the time of the last failing run seen;
        is_open is True until a newer success was reported as the recovery."""
        row = self._db.execute("SELECT failed_at, open, recovered_at FROM ci_runs WHERE repo = ? AND workflow = ? AND branch = ?",
                               (repo, workflow, branch)).fetchone()
        if row is None:
            return None
        return (datetime.fromisoformat(row[0]), bool(row[1]),
                None if row[2] is None else datetime.fromisoformat(row[2]))

    def set_ci_run(self, repo, workflow, branch, failed_at, is_open, recovered_at=None):
        self._db.execute("INSERT OR REPLACE INTO ci_runs VALUES (?,?,?,?,?,?)",
                         (repo, workflow, branch, _iso(failed_at), int(is_open), _iso(recovered_at)))
        self._db.commit()
