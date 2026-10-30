"""The ONLY place that touches .env: it loads it, and (for the /setup page) writes it. Values are never logged or printed.

Writing is atomic and private: a temp file in the same folder (created mode 600), fsync, os.replace, and the result is re-parsed
before the rename, so a bad write can never leave a half-written or unreadable file. Nothing here returns a secret: the form
view says "set" or "missing" for keys, and echoes only the three plain settings (repository lists, reminder lead).
"""

import contextlib
import os
import re
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values, load_dotenv
from dotenv.parser import parse_stream

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV = ROOT / ".env"
TEMPLATE = ROOT / ".env.example"  # a new .env starts from this file (names, empty values, one comment)


# The pair chosen on the tune days in the main evaluation (eval/main_selection.json, eval/EVAL_REPORT.md).
CHOSEN_FORMAT = "F2"
CHOSEN_THRESHOLD = 0.40
CHOSEN_NOTE = "chosen on tune days in the main evaluation (eval/EVAL_REPORT.md)"


# Every setting name, as in .env.example (a test keeps the two in step).
SETTING_NAMES = ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_REPOS", "GITHUB_WRITE_REPOS", "GITHUB_ACTIONS_WRITE_TOKEN",
                 "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ICAL_URL", "REMINDER_LEAD_MINUTES",
                 "DECIDER_FORMAT", "DECIDER_THRESHOLD", "DECIDER_HOST")


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = field(default=None, repr=False)
    github_repos: tuple[str, ...] = ()
    github_token: str | None = field(default=None, repr=False)
    ical_url: str | None = field(default=None, repr=False)  # a secret address
    reminder_lead_minutes: int = 30  # the rubric's "soon"
    decider_format: str = CHOSEN_FORMAT  # chosen on tune days in the main evaluation (eval/EVAL_REPORT.md)
    decider_threshold: float = CHOSEN_THRESHOLD  # chosen on tune days in the main evaluation (eval/EVAL_REPORT.md)
    decider_host: str = "127.0.0.1:11434"
    telegram_bot_token: str | None = field(default=None, repr=False)
    telegram_chat_id: int | None = field(default=None, repr=False)
    github_write_repos: tuple[str, ...] = ()
    github_actions_write_token: str | None = field(default=None, repr=False)  # separate token, one repo, Actions: write


def _shell_overrides(shell_values, env_path):
    """Names set in the shell AND with a different non-empty value in the .env file. Names only: no value is returned."""
    in_file = dotenv_values(env_path)
    return [n for n in SETTING_NAMES
            if shell_values.get(n) is not None and in_file.get(n) and in_file[n] != shell_values[n]]


def setting_status():
    """{name: True if it is set (non-empty) in the process}, for the dashboard. Names and booleans only, never a value."""
    return {n: bool(os.environ.get(n)) for n in SETTING_NAMES}


def ollama_serve_env(host, models_dir):
    """The environment for a child `ollama serve`: a few harmless variables plus the project's Ollama settings.
    No token or key from the process environment is passed on."""
    env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "USER", "LANG") if k in os.environ}
    env.update(OLLAMA_HOST=host, OLLAMA_MODELS=str(models_dir), OLLAMA_KEEP_ALIVE="60s")
    return env


# Names that load_dotenv put into the process (as opposed to names the user's shell already had). reload_settings clears exactly these.
_FROM_FILE: set[str] = set()


def load_settings(env_path=DEFAULT_ENV) -> Settings:
    before = {n: os.environ.get(n) for n in SETTING_NAMES}  # before load_dotenv; it never overrides these
    shell_values = {n: v for n, v in before.items() if n not in _FROM_FILE}  # a value an earlier load added is not the shell's
    load_dotenv(env_path, override=False)
    _FROM_FILE.update(n for n in SETTING_NAMES if before[n] is None and os.environ.get(n) is not None)
    overridden = _shell_overrides(shell_values, env_path)
    if overridden:
        print(f"warning: {', '.join(overridden)} {'is' if len(overridden) == 1 else 'are'} set in your shell and in .env with "
              "different values; the shell values override .env. Run in a fresh terminal (or unset them) to use .env.",
              file=sys.stderr)
    repos = _repo_list(os.environ.get("GITHUB_REPOS"))
    return Settings(
        reminder_lead_minutes=_lead_minutes(os.environ.get("REMINDER_LEAD_MINUTES")),
        decider_format=_decider_format(os.environ.get("DECIDER_FORMAT")),
        decider_threshold=_decider_threshold(os.environ.get("DECIDER_THRESHOLD")),
        decider_host=_decider_host(os.environ.get("DECIDER_HOST")),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        github_repos=repos,
        github_write_repos=_repo_list(os.environ.get("GITHUB_WRITE_REPOS")),
        github_actions_write_token=os.environ.get("GITHUB_ACTIONS_WRITE_TOKEN") or None,
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=_chat_id(os.environ.get("TELEGRAM_CHAT_ID")),
        github_token=os.environ.get("GITHUB_TOKEN") or None,
        ical_url=os.environ.get("ICAL_URL") or None,
    )


def _lead_minutes(raw):
    if raw is None or not raw.strip():
        return 30
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("REMINDER_LEAD_MINUTES must be a whole number of minutes") from None
    if not 0 < value <= 48 * 60:
        raise ValueError("REMINDER_LEAD_MINUTES must be between 1 and 2880")
    return value


def _decider_format(raw):
    if raw is None or not raw.strip():
        return CHOSEN_FORMAT
    if raw.strip() not in ("F1", "F2", "F3"):
        raise ValueError("DECIDER_FORMAT must be F1, F2 or F3")
    return raw.strip()


def _decider_threshold(raw):
    if raw is None or not raw.strip():
        return CHOSEN_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        raise ValueError("DECIDER_THRESHOLD must be a number between 0 and 1") from None
    if not 0.0 <= value <= 1.0:
        raise ValueError("DECIDER_THRESHOLD must be a number between 0 and 1")
    return value


def _decider_host(raw):
    if raw is None or not raw.strip():
        return "127.0.0.1:11434"
    host = raw.strip()
    name, sep, port = host.rpartition(":")
    if not sep or name not in ("127.0.0.1", "localhost") or not port.isdigit():
        raise ValueError("DECIDER_HOST must be 127.0.0.1:PORT or localhost:PORT")
    return host


def _repo_list(raw):
    return tuple(r.strip() for r in (raw or "").split(",") if r.strip())


def _chat_id(raw):
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValueError("TELEGRAM_CHAT_ID must be a whole number") from None


# ---------------------------------------------------------------------------------------------------------------- the /setup page
@dataclass(frozen=True)
class SettingField:
    name: str
    label: str
    group: str
    help: str
    kind: str  # "secret" and "id" are never shown again, "list" and "int" are plain settings, "readonly" cannot be edited
    required: bool = False

    @property
    def editable(self):
        return self.kind != "readonly"

    @property
    def echo(self):
        """Only the plain settings may be sent back to the browser (a user has to be able to edit a repository list)."""
        return self.kind in ("list", "int")


FIELDS = (
    SettingField("ANTHROPIC_API_KEY", "Anthropic key (optional)", "Optional extras",
                 "Optional: Claude Haiku writes friendlier alert text. Without it, everything still works; alerts use a plain template. "
                 "Costs a fraction of a cent a day on your own Anthropic account.", "secret"),
    SettingField("GITHUB_TOKEN", "GitHub read token", "GitHub",
                 "A fine-grained token with read-only access. Public repositories also work without one, with lower rate limits.", "secret"),
    SettingField("GITHUB_REPOS", "Repositories to watch", "GitHub",
                 "owner/repository, one per line or separated by commas.", "list"),
    SettingField("GITHUB_WRITE_REPOS", "Repositories you may re-run", "GitHub",
                 "The Re-run CI button is offered only for these.", "list"),
    SettingField("GITHUB_ACTIONS_WRITE_TOKEN", "GitHub write token (optional)", "GitHub",
                 "Only for Re-run CI: a separate fine-grained token for one repository with Actions: Read and write.", "secret"),
    SettingField("TELEGRAM_BOT_TOKEN", "Telegram bot token", "Telegram",
                 "In Telegram, message @BotFather, send /newbot and follow the prompts; paste the token it gives you.", "secret", required=True),
    SettingField("TELEGRAM_CHAT_ID", "Telegram chat ID", "Telegram",
                 "Save the bot token, send your bot any message in Telegram, then press Find my chat ID.", "id", required=True),
    SettingField("ICAL_URL", "Calendar address (iCal)", "Calendar",
                 "Google Calendar: Settings, your calendar, Integrate calendar, Secret address in iCal format. Treat it like a password.", "secret"),
    SettingField("REMINDER_LEAD_MINUTES", "Meeting reminder lead (minutes)", "Timing",
                 "Alert this many minutes before a meeting starts (1 to 2880). Empty means 30.", "int"),
    SettingField("DECIDER_FORMAT", "Judge question format", "Judge",
                 "Fixed to the evaluated choice. To change it, edit the settings file by hand.", "readonly"),
    SettingField("DECIDER_THRESHOLD", "Judge threshold", "Judge",
                 "Fixed to the evaluated choice. To change it, edit the settings file by hand.", "readonly"),
    SettingField("DECIDER_HOST", "Judge host", "Judge",
                 "Always this computer (127.0.0.1). To change the port, edit the settings file by hand.", "readonly"),
)
_BY_NAME = {f.name: f for f in FIELDS}
assert tuple(f.name for f in FIELDS) == SETTING_NAMES  # one descriptor per setting, in .env.example order

_SAVE_LOCK = threading.Lock()  # one save or reload at a time
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_BOT_TOKEN = re.compile(r"^\d{3,}:[A-Za-z0-9_-]{20,}$")  # a SHAPE check: Telegram documents only an example, getMe is the real test
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CHAT = re.compile(r"^-?\d{1,20}$")
MAX_REPOS = 50


class SettingError(ValueError):
    """A name or a value was refused. The message names the setting only, never the value."""

    def __init__(self, name, reason=None):
        super().__init__(f"invalid {name}")
        self.name = name
        self.reason = reason  # a short code the page turns into plain words, e.g. "bot_own_id"


class EnvFileError(Exception):
    """The settings file could not be read or written safely. The message never contains a value or a path."""


def _bot_id(token):
    """The bot's own numeric ID: the digits before the colon in its token (None if there is no usable token)."""
    head = (token or "").partition(":")[0]
    return int(head) if head.isdigit() else None


def validate_value(name, raw):
    """The normalised text to store for `name`, or SettingError(name). Shape checks only: the connection tests are the real check."""
    field = _BY_NAME.get(name)
    if field is None or not field.editable or not isinstance(raw, str):
        raise SettingError(name)
    if field.kind == "list":
        raw = re.sub(r"[\r\n]+", ",", raw)
    value = raw.strip()
    if not value or len(value) > 2000 or _CONTROL.search(value) or "${" in value:  # dotenv expands ${X} even inside quotes
        raise SettingError(name)
    if name in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_ACTIONS_WRITE_TOKEN"):
        if len(value) > 600 or re.search(r"\s", value):
            raise SettingError(name)
        return value
    if name == "TELEGRAM_BOT_TOKEN":
        if not _BOT_TOKEN.match(value):
            raise SettingError(name)
        return value
    if name == "TELEGRAM_CHAT_ID":
        if not _CHAT.match(value):
            raise SettingError(name)
        return str(int(value))
    if name in ("GITHUB_REPOS", "GITHUB_WRITE_REPOS"):
        repos = []
        for item in re.split(r"[,\s]+", value):
            if not item:
                continue
            if not _REPO.match(item):
                raise SettingError(name)
            if item.lower() not in [r.lower() for r in repos]:
                repos.append(item)
        if not repos or len(repos) > MAX_REPOS:
            raise SettingError(name)
        return ",".join(repos)
    if name == "ICAL_URL":  # the same rule IcalSource enforces
        if re.search(r"\s", value):
            raise SettingError(name)
        try:
            parts = urlsplit(value)
            if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
                raise SettingError(name)
        except ValueError:
            raise SettingError(name) from None
        return value
    if name == "REMINDER_LEAD_MINUTES":
        try:
            return str(_lead_minutes(value))
        except ValueError:
            raise SettingError(name) from None
    raise SettingError(name)


def _format_line(name, value):
    """NAME='value' with backslash and quote escaped; python-dotenv reads that back exactly (checked after every write)."""
    return f"{name}=" if value == "" else f"{name}='{value.replace(chr(92), chr(92) * 2).replace(chr(39), chr(92) + chr(39))}'"


def _apply(text, updates):
    """`text` with the updated settings replaced. python-dotenv's own parser says where each setting starts and ends (a quoted value
    may span lines), so a replaced setting is replaced whole; comments, blank lines, unknown and unparsable lines stay verbatim."""
    out, seen = [], set()
    for binding in parse_stream(StringIO(text)):
        raw = binding.original.string
        if binding.key in updates and not binding.error:
            prefix = re.match(r"\s*(?:export\s+)?", raw).group(0)  # keep a leading `export `
            out.append(prefix + _format_line(binding.key, updates[binding.key]) + "\n")
            seen.add(binding.key)
        else:
            out.append(raw)
    result = "".join(out)
    if result and not result.endswith("\n"):
        result += "\n"
    return result + "".join(_format_line(n, updates[n]) + "\n" for n in SETTING_NAMES if n in updates and n not in seen)


def _read_env_text(env_path):
    """The current file text, or the template for a first save. A link is refused: replacing it would not write where it points."""
    path = Path(env_path)
    if path.is_symlink():
        raise EnvFileError("the settings file is a link")
    try:
        return path.read_text(encoding="utf-8") if path.exists() else TEMPLATE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise EnvFileError("the settings file cannot be read") from None


def _fsync_dir(directory):
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _write_atomic(env_path, text):
    """Temp file next to the target (mode 600 from the start), fsync, rename over it. On any failure the original is untouched
    and the temp file is gone. No backup copy is kept: it would duplicate the secrets."""
    path = Path(env_path)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException as exc:
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        if isinstance(exc, OSError):
            raise EnvFileError("the settings file cannot be written") from None
        raise
    _fsync_dir(path.parent)
    os.chmod(path, 0o600)


def _literal_values(text):
    return dotenv_values(stream=StringIO(text), interpolate=False)


def save_env(values=None, clear=(), env_path=DEFAULT_ENV):
    """Update the settings file. `values` {name: text}: an empty or missing value KEEPS what is stored; `clear` names are emptied.
    Any invalid name or value refuses the whole save and writes nothing. Returns the names that were written (names only)."""
    values, clear = dict(values or {}), tuple(clear or ())
    updates = {}
    for name, raw in values.items():
        if name not in _BY_NAME or not _BY_NAME[name].editable:
            raise SettingError(name)
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            continue
        updates[name] = validate_value(name, raw)
    for name in clear:
        if name not in _BY_NAME or not _BY_NAME[name].editable or name in updates:
            raise SettingError(name)
        updates[name] = ""
    if not updates:
        return []
    with _SAVE_LOCK:
        old_text = _read_env_text(env_path)
        new_text = _apply(old_text, updates)
        old, new = _literal_values(old_text), _literal_values(new_text)
        if "TELEGRAM_CHAT_ID" in updates and updates["TELEGRAM_CHAT_ID"]:  # a bot cannot message itself: Telegram answers 403
            bot_id = _bot_id(updates.get("TELEGRAM_BOT_TOKEN") or old.get("TELEGRAM_BOT_TOKEN"))
            if bot_id is not None and int(updates["TELEGRAM_CHAT_ID"]) == bot_id:
                raise SettingError("TELEGRAM_CHAT_ID", "bot_own_id")
        for key in set(old) | set(new):  # the written file must read back exactly: the changed settings, and nothing else moved
            expected = updates[key] if key in updates else old.get(key)
            if (new.get(key) or "") != (expected or ""):
                raise EnvFileError("the settings file would not read back correctly")
        _write_atomic(env_path, new_text)
    return [n for n in SETTING_NAMES if n in updates]


def _shell_names():
    """Names the user's own shell provided (everything in the process that load_dotenv did not add)."""
    return {n for n in SETTING_NAMES if os.environ.get(n) is not None and n not in _FROM_FILE}


def _file_values(env_path):
    try:
        return {k: v for k, v in dotenv_values(env_path, interpolate=False).items() if k in _BY_NAME and v}
    except (OSError, UnicodeDecodeError):
        return {}


def _effective(env_path):
    """{name: value} as the app will see it: the shell wins over the file, like load_dotenv(override=False). Internal: holds secrets."""
    file_values, shell = _file_values(env_path), _shell_names()
    return {n: (os.environ.get(n) if n in shell else file_values.get(n)) or "" for n in SETTING_NAMES}


def known_secrets(env_path=DEFAULT_ENV):
    """The current values of every key, the chat ID and the calendar address. ONLY for the server's last-line leak check (a response
    that contains one of these is replaced by a generic error): never put the result in a response, a log or a message."""
    have = _effective(env_path)
    return [have[f.name] for f in FIELDS if f.kind in ("secret", "id") and have[f.name]]


def required_missing(env_path=DEFAULT_ENV):
    """Plain-language labels of what is still needed before checks can run. Never a value."""
    have = _effective(env_path)
    missing = [_BY_NAME[n].label for n in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not have[n]]
    if not have["GITHUB_REPOS"] and not have["ICAL_URL"]:
        missing.append("A GitHub repository or a calendar address")
    return missing


def setup_needed(env_path=DEFAULT_ENV):
    return bool(required_missing(env_path))


def shell_overrides(env_path=DEFAULT_ENV):
    """Names set in the shell with a different non-empty value in the file (so the saved value would be ignored). Names only."""
    shell = _shell_names()
    return _shell_overrides({n: os.environ.get(n) for n in shell}, env_path)


def env_form(env_path=DEFAULT_ENV):
    """What the /setup page may know. Keys and the chat ID are only "set" or "missing"; the judge settings only "default" or
    "custom"; the repository lists and the reminder lead are echoed (they are not secrets and must be editable)."""
    have, shell = _effective(env_path), _shell_names()
    fields = []
    for f in FIELDS:
        value = have[f.name]
        entry = {"name": f.name, "label": f.label, "group": f.group, "help": f.help, "kind": f.kind, "required": f.required,
                 "editable": f.editable, "state": ("custom" if value else "default") if f.kind == "readonly" else ("set" if value else "missing"),
                 "from_shell": f.name in shell and bool(value)}
        if f.echo:
            entry["value"] = value
        fields.append(entry)
    return {"fields": fields, "required_missing": required_missing(env_path), "shell_overrides": shell_overrides(env_path),
            "file_exists": Path(env_path).exists()}


def reload_settings(env_path=DEFAULT_ENV) -> Settings:
    """Read the file again after a save. Removes only the values an earlier load added (a value the shell exported is never
    touched), then loads. If the new file is invalid the process is put back as it was and the ValueError is raised."""
    with _SAVE_LOCK:
        snapshot = {n: os.environ.get(n) for n in SETTING_NAMES}
        was_from_file = set(_FROM_FILE)
        for name in was_from_file:
            os.environ.pop(name, None)
        _FROM_FILE.clear()
        try:
            return load_settings(env_path)
        except Exception:
            for name, value in snapshot.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            _FROM_FILE.clear()
            _FROM_FILE.update(was_from_file)
            raise
