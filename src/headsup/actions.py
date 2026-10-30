"""LEARNER PART 3: ask before acting. Tap-to-approve buttons and the only write the app can make.

Everything that decides what a tap can do is fixed in code, not in config and not in LLM output:
- ALLOWED_ACTIONS is a fixed tuple; buttons_for() picks buttons from the event kind alone, so event
  text and Haiku text can never add, rename or trigger a button;
- callback_data is a random 16-hex token that maps to a database row, never an action name;
- a tap is refused (and logged) if it comes from the wrong chat or sender, the token is unknown, the
  token is older than 1 hour, or the action was already done for that event (idempotent);
- rerun_failed_ci is offered and accepted only for ci_failed events in GITHUB_WRITE_REPOS, uses the
  separate GITHUB_ACTIONS_WRITE_TOKEN, and is the ONLY GitHub write in the app.
Every tap, accepted or refused, is written to tap_log.
"""

import re
import secrets
from datetime import datetime, timedelta, timezone

import httpx

from headsup.telegram import TelegramError, chat_from_update

ALLOWED_ACTIONS = ("snooze_1h", "remind_later", "rerun_failed_ci")
LABELS = {"snooze_1h": "Snooze 1h", "remind_later": "Remind in 2h", "rerun_failed_ci": "Re-run CI"}
DONE_TEXT = {"snooze_1h": "Snoozed for 1 hour", "remind_later": "I'll remind you in 2 hours", "rerun_failed_ci": "Re-run requested"}
TOKEN_TTL = timedelta(hours=1)  # Telegram states no button expiry (PLAN.md A5), so we enforce one
SNOOZE_FOR = timedelta(hours=1)
REMIND_AFTER = timedelta(hours=2)
REFUSED_TEXT = "Not available"

_RUN_ID = re.compile(r"github:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+):ci_failed:([0-9]+)")
_GITHUB_API = "https://api.github.com"


def new_token():
    return secrets.token_hex(8)  # 16 hex characters, random


def rerun_target(event, write_repos):
    """(repo, run_id) if this event may be re-run: a ci_failed event from our own watcher in a write repo."""
    if event.kind != "ci_failed":
        return None
    m = _RUN_ID.fullmatch(event.id)
    if m is None:
        return None
    repo, run_id = m.groups()
    if repo.lower() not in {r.lower() for r in write_repos}:
        return None
    return repo, run_id


def buttons_for(event, write_repos, include_remind=True):
    """The actions offered for this alert, chosen by code from the event kind only."""
    actions = ["snooze_1h"]
    if include_remind:
        actions.append("remind_later")
    if rerun_target(event, write_repos) is not None:
        actions.append("rerun_failed_ci")
    return actions


class GitHubWriter:
    """The only GitHub write in the app: POST /repos/{owner}/{repo}/actions/runs/{run_id}/rerun-failed-jobs.
    Needs a separate fine-grained token with Actions: write on one repo (PLAN.md A8, A8b)."""

    def __init__(self, client, token):
        self.client = client
        self._token = token

    def rerun_failed_jobs(self, repo, run_id):
        if not self._token:
            return False
        url = f"{_GITHUB_API}/repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs"
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "heads-up"}
        try:
            return self.client.post(url, headers=headers).status_code == 201
        except httpx.HTTPError:
            return False


class DryRunWriter:
    """Stands in for GitHubWriter in --dry-run: records what would have been written, writes nothing."""

    def __init__(self):
        self.would = []

    def rerun_failed_jobs(self, repo, run_id):
        self.would.append((repo, run_id))
        return True


class ActionHandler:
    def __init__(self, store, telegram, writer, chat_id, write_repos, now=None, out=print):
        self.store = store
        self.telegram = telegram
        self.writer = writer
        self.chat_id = chat_id
        self.write_repos = tuple(write_repos)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.out = out

    def _safe(self, fn, *args):
        try:
            return fn(*args)
        except TelegramError as exc:
            self.out(f"warning: {exc}")
            return None

    def _refuse(self, outcome, cq_id, **log):
        self.store.log_tap(outcome, at=self._now(), **log)
        self._safe(self.telegram.answer_callback, cq_id, REFUSED_TEXT)  # every tap gets an answer
        return outcome

    def process_updates(self, updates):
        """Handle each update, then confirm it so Telegram does not send it again. Returns the outcomes."""
        outcomes = []
        for update in updates:
            outcome = self.handle_update(update)
            if outcome is not None:
                outcomes.append(outcome)
            self.telegram.confirm(update["update_id"])
        return outcomes

    def _note_sender(self, update):
        """A message to the bot: keep who wrote (id, first name, time) for ten minutes so the setup page can offer them. The text is
        never read, stored, logged or acted on, and the message gets no answer."""
        seen = chat_from_update(update)
        if seen is not None:
            chat_id, name, sent = seen
            now = self._now()
            self.store.note_chat(chat_id, name, sent or now, now)

    def handle_update(self, update):
        if isinstance(update, dict) and "message" in update:
            self._note_sender(update)
            return None
        cq = update.get("callback_query")
        if not isinstance(cq, dict):
            return None
        cq_id = cq.get("id")
        sender = (cq.get("from") or {}).get("id")
        message = cq.get("message") or {}
        chat = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")
        data = cq.get("data")
        token = data if isinstance(data, str) else None
        base = {"token": token, "chat_id": chat, "sender_id": sender}

        # LEARNER PART 3 START
        """LEARNER PART 3: Decide what one Telegram tap may do, then do it. Refuse and log (self._refuse) a tap from the wrong chat or
        sender, an unknown token, an action outside ALLOWED_ACTIONS, a token older than TOKEN_TTL; answer
        'already_done' for a repeated (event, action). Otherwise act (snooze, remind, or the one GitHub re-run
        through self.writer), record_tap, log_tap('done'), answer the callback, drop the keyboard, return 'done'.
        """
        raise NotImplementedError("LEARNER PART 3: tap-to-approve (handle_update)")
        # LEARNER PART 3 END

    def _drop_keyboard(self, message_id):
        if message_id is None:
            return
        try:
            self.telegram.remove_keyboard(message_id)
        except TelegramError as exc:
            if exc.kind != "HTTP 400":  # 400 = already gone or not editable: nothing more to do
                self.out(f"warning: {exc}")
                return
        self.store.mark_keyboard_removed(message_id)

    def sweep_expired(self):
        """Remove the buttons from alerts older than the token lifetime."""
        now = self._now()
        for message_id, created in self.store.live_keyboards():
            if now - created > TOKEN_TTL:
                self._drop_keyboard(message_id)
