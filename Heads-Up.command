#!/bin/bash
# Double-click to start Heads-Up. Closing this window stops it (and the Ollama it started).
cd "$(dirname "$0")" || exit 1
( sleep 2; open "http://127.0.0.1:8765/" ) &
exec env PYTHONPATH=src .venv/bin/python -m headsup --ui
