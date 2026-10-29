#!/bin/bash
# Double-click to start Heads-Up. Your browser opens by itself: the setup page the first time, the dashboard after that.
# Closing this window stops it (and the Ollama it started).
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "Heads-Up is not installed yet. Double-click Install.command first, then open this again."
  [ -t 0 ] && read -r -p "Press Return to close this window. " _
  exit 1
fi
echo "Starting Heads-Up. It lives at http://127.0.0.1:8765/ and opens in your browser in a moment."
exec env PYTHONPATH=src .venv/bin/python -m headsup --ui
