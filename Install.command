#!/bin/bash
# Heads-Up installer: double-click it once. It is safe to run again: every step checks first and skips what is already done.
#
# What it does: checks for Homebrew, installs Python 3.12 and Ollama with it if they are missing, creates the project's own Python
# folder (.venv), installs the exact package versions pinned in pyproject.toml, and downloads the local judge model (about 2 GB).
# What it never does: ask for administrator rights, install Homebrew itself (Homebrew's own installer asks for your Mac password, so
# you run that yourself), touch your settings, or write a secret anywhere. Its log is data/install.log: step names, versions and exit
# codes only.

set +x
cd "$(dirname "$0")" || exit 1

PREFIX="${HEADSUP_BREW_PREFIX:-/opt/homebrew}"
OS_NAME="${HEADSUP_UNAME_S:-$(uname -s)}"
ARCH="${HEADSUP_UNAME_M:-$(uname -m)}"
MODEL="strands-decider:2b-mxfp8"
HOST_PORT="127.0.0.1:11434"
LOG="data/install.log"
SERVE_PID=""
STARTED_SERVE=0
mkdir -p data

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }
say() { printf '%s\n' "$*"; log "$*"; }
step() { printf '\n== %s\n' "$*"; log "STEP $*"; }
pause() { if [ -t 0 ]; then read -r -p "Press Return to close this window. " _; fi; }

stop_serve() {
  if [ "$STARTED_SERVE" = 1 ] && [ -n "$SERVE_PID" ]; then
    kill "$SERVE_PID" 2>/dev/null
    wait "$SERVE_PID" 2>/dev/null
    log "stopped the Ollama server this script started"
    STARTED_SERVE=0
  fi
}
trap stop_serve EXIT

fail() {
  local where="$1"
  shift
  say ""
  say "STOPPED at: $where"
  for line in "$@"; do say "$line"; done
  log "exit 1 ($where)"
  pause
  exit 1
}

# The path of a tool: on the PATH first, then in Homebrew's folder.
find_tool() {
  local found
  found="$(command -v "$1" 2>/dev/null)"
  if [ -n "$found" ]; then
    printf '%s\n' "$found"
  elif [ -x "$PREFIX/bin/$1" ]; then
    printf '%s\n' "$PREFIX/bin/$1"
  fi
}

log "Install started"
say "Heads-Up installer. It checks each step first, so running it again is safe."

# ------------------------------------------------------------------------------------------------------ this computer
step "Checking this computer"
[ "$OS_NAME" = "Darwin" ] || fail "this computer" "Heads-Up is built for a Mac. This installer stops here."
[ "$ARCH" = "arm64" ] || fail "this computer" \
  "Heads-Up expects an Apple silicon Mac (M1 or newer): the app looks for Ollama in /opt/homebrew." "This Mac reports: $ARCH."
if command -v sw_vers >/dev/null 2>&1; then
  MAC_VERSION="$(sw_vers -productVersion)"
  log "macOS $MAC_VERSION"
  case "${MAC_VERSION%%.*}" in
    ''|*[!0-9]*) ;;
    *) if [ "${MAC_VERSION%%.*}" -lt 15 ]; then
         say "Note: Homebrew officially supports macOS 15 (Sequoia) or newer and this Mac has $MAC_VERSION. The install may still work."
       fi ;;
  esac
fi
say "OK: Apple silicon Mac."

# ------------------------------------------------------------------------------------------------------ Homebrew
step "Homebrew"
BREW=""
if command -v brew >/dev/null 2>&1; then
  BREW="$(command -v brew)"
elif [ -x "$PREFIX/bin/brew" ]; then
  BREW="$PREFIX/bin/brew"
fi
if [ -z "$BREW" ]; then
  fail "Homebrew" \
    "Homebrew (the standard installer for Mac apps) is not installed." \
    "Its own installer asks for your Mac password, so this script does not run it for you." \
    "1. Open https://brew.sh in your browser." \
    "2. Copy the one line shown there into Terminal, press Return and answer its questions." \
    "3. When it has finished, double-click Install.command again."
fi
eval "$("$BREW" shellenv)"
say "OK: Homebrew found."
log "brew: $("$BREW" --version 2>&1 | head -1)"

# ------------------------------------------------------------------------------------------------------ Python 3.12
step "Python 3.12"
PY="$(find_tool python3.12)"
if [ -z "$PY" ]; then
  say "Installing Python 3.12 with Homebrew (a few minutes)..."
  "$BREW" install python@3.12 || fail "installing Python 3.12" "Homebrew could not install it. Read the message above, then double-click Install.command again."
  PY="$(find_tool python3.12)"
  [ -n "$PY" ] || fail "installing Python 3.12" "It installed, but python3.12 cannot be found. Close this window and double-click Install.command again."
  log "installed python@3.12"
else
  say "Already installed."
fi
log "python: $("$PY" --version 2>&1)"

# ------------------------------------------------------------------------------------------------------ Ollama
step "Ollama (runs the local judge on this Mac)"
OLLAMA="$(find_tool ollama)"
if [ -z "$OLLAMA" ]; then
  say "Installing Ollama with Homebrew (a few minutes)..."
  "$BREW" install ollama || fail "installing Ollama" "Homebrew could not install it. Read the message above, then double-click Install.command again."
  OLLAMA="$(find_tool ollama)"
  [ -n "$OLLAMA" ] || fail "installing Ollama" "It installed, but ollama cannot be found. Close this window and double-click Install.command again."
  log "installed ollama"
else
  say "Already installed."
fi

# ------------------------------------------------------------------------------------------------------ the project's Python folder
step "Heads-Up's own Python folder (.venv)"
if [ -x .venv/bin/python ]; then
  if .venv/bin/python --version 2>&1 | grep -q '^Python 3\.12'; then
    say "Already there (Python 3.12)."
  else
    fail "the .venv folder" \
      "A folder named .venv exists but it does not use Python 3.12." \
      "Move or delete the folder named .venv inside the Heads-Up folder, then double-click Install.command again. (Nothing was changed.)"
  fi
else
  "$PY" -m venv .venv || fail "creating .venv" "Python could not create its folder here. Check that this folder is writable, then try again."
  log "created .venv"
  say "Created."
fi

# ------------------------------------------------------------------------------------------------------ packages
step "Python packages (the exact versions this project is tested with)"
REQS="data/install-requirements.txt"
.venv/bin/python - > "$REQS" <<'PY' || fail "reading the package list" "pyproject.toml could not be read."
import tomllib

project = tomllib.load(open("pyproject.toml", "rb"))["project"]
print("\n".join(project["dependencies"]))
PY
[ -s "$REQS" ] || fail "reading the package list" "pyproject.toml lists no packages."
log "packages: $(wc -l < "$REQS" | tr -d ' ') pinned"
.venv/bin/python -m pip install --disable-pip-version-check -r "$REQS" || fail "installing the packages" \
  "pip could not install them. Check your internet connection, then double-click Install.command again."
log "packages installed"

# ------------------------------------------------------------------------------------------------------ the local judge
step "The local judge ($MODEL, about 2 GB the first time)"
export OLLAMA_HOST="$HOST_PORT" OLLAMA_MODELS="$PWD/.cache/ollama" OLLAMA_KEEP_ALIVE=60s
mkdir -p .cache/ollama
if curl -fs --max-time 2 "http://$HOST_PORT/" >/dev/null 2>&1; then
  say "Ollama is already running on this Mac; using it."
else
  say "Starting Ollama for a moment (this installer stops it again when it is done)..."
  "$OLLAMA" serve >> data/ollama-serve.log 2>&1 &
  SERVE_PID=$!
  STARTED_SERVE=1
  log "started ollama serve"
  READY=0
  for _ in $(seq 1 120); do  # up to about 30 seconds, looking four times a second
    if curl -fs --max-time 2 "http://$HOST_PORT/" >/dev/null 2>&1; then READY=1; break; fi
    sleep 0.25
  done
  [ "$READY" = 1 ] || fail "starting Ollama" "Ollama did not start. Close this window and double-click Install.command again."
fi
if "$OLLAMA" list 2>/dev/null | grep -q "$MODEL"; then
  say "Already downloaded: $MODEL"
else
  say "Downloading $MODEL. Its progress is shown below; keep this window open until it finishes."
  log "model download started"
  "$OLLAMA" pull "$MODEL"
  PULL_RC=$?
  log "model download finished with exit code $PULL_RC"
  [ "$PULL_RC" = 0 ] || fail "downloading the model" \
    "The download did not finish. Check your internet connection and that there are about 3 GB free, then double-click Install.command again." \
    "It carries on where it stopped."
fi
stop_serve

# ------------------------------------------------------------------------------------------------------ done
chmod +x Heads-Up.command 2>/dev/null
step "Done"
say "All done. Double-click Heads-Up.command to start. The first time, your browser opens a setup page."
log "Install finished OK"
pause
exit 0
