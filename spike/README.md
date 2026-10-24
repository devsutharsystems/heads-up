# Spike: can the Decider run on this laptop?

# Ollama fallback pre-registration (set before any Ollama data; Fri Oct 23 19:33:44 IST 2026)
- Same three criteria as before, unchanged (swap does not grow; warm p95 <= 5 s; Mac usable), judged on the clean run.
- Fidelity check: the same 30 states and question through Ollama, compared with the official MLX probabilities already in spike/runs/clean/mlx/. Pass if max absolute difference <= 0.05. If fidelity fails, the Ollama build is rejected even if memory passes.
- Ollama passes clean AND fidelity -> chosen design: Ollama MXFP8 build of Strands Decider (third-party build; Dev tells Prateek).
- Ollama fails -> STOP. No further fallback is chosen until Dev sets the next rule.
- The `idle` control run is an observation only, never a rule input.

## Decision rule (set 23 Oct 2026, BEFORE the measured runs)
- clean passes and normal passes -> keep the Decider loaded as a server.
- clean passes, normal fails -> on-demand loading (load, decide all pending events, unload). Pass if load + 30 decisions <= 2 min, swap returns to roughly its starting level after unload, and the Mac stays usable during load.
- clean fails -> third-party MXFP8 build via Ollama (needs Dev's approval; tell Prateek it is not the official weights).
- on-demand also fails -> remote/Haiku decider, which breaks requirement 7; tell Prateek the same day.
Pass/fail is judged on MLX only; MPS is reference only.

Question: does the official Strands Decider 2B serve on this Mac with warm p95 <= 5 s, no swap growth, and a usable machine?
Pass = swap does not grow AND warm p95 <= 5 s AND Dev confirms the Mac stayed usable.

Status: installed and smoke-tested (MLX and MPS answer). Measured run is Dev's, after a reboot, with Claude Code closed.

## Run (from the project folder)
```bash
cd "$HOME/Documents/Lets Data Science/week6/heads-up"
# after a fresh reboot, nothing else open:
./spike/run_spike.sh clean
# later, with your normal apps open:
./spike/run_spike.sh normal
.venv/bin/python -I spike/analyze_spike.py
```
Variants run in order: mlx, then mps. Each takes about 3 to 6 minutes (30 requests plus a 30 s settle). Everything stays in this folder; offline (`HF_HUB_OFFLINE=1`), localhost only, no sudo.

Files: `states.jsonl` (30 synthetic states), `run_spike.sh`, `analyze_spike.py`, `runs/<label>/<variant>/` (raw data). Kept after the spike: this README and `SPIKE_REPORT.md`.
Note: RSS (`ps`) undercounts Metal/MLX memory; the analyzer reports phys footprint as well.

## Ollama fallback: files, run order, uninstall (23 Oct 2026)

### What exists
- `spike/fidelity.py`: 30 states one at a time through Ollama vs `runs/clean/mlx/latency.csv` (MLX probabilities are stored rounded to 4 decimals).
- `run_spike.sh <idle|clean|normal> [mlx,mps,ollama]`: uses only `/opt/homebrew/bin/ollama` (aborts below 0.40.0 or if any ollama process is already running; a trap stops `ollama serve` on exit and prints whether anything is left). Memory for ollama = SUM of `footprint` over `ollama serve` plus every descendant PID (the model runs in a child, `ollama runner`, found with `pgrep -P` recursively; the PIDs are in the `pids` column of `mem.csv`). The summed "peak" is the sum of per-process peaks, so it may slightly overstate the true joint peak. Model files: `.cache/ollama` (1.8 GB). Server: `127.0.0.1:11436`, `OLLAMA_KEEP_ALIVE=-1`.
- Outside this folder, Ollama created `~/.ollama/` (`id_ed25519`, `id_ed25519.pub`, `cache/model-recommendations.json`) when `ollama serve` first started. Nothing else outside the folder was touched by me.

### Uninstall
Inside this folder (safe): `rm -rf .cache/ollama spike/ollama_work`.
Ollama itself (only when Dev decides): `brew uninstall ollama` (https://docs.brew.sh/Manpage, "brew uninstall"); `rm -rf ~/.ollama` removes the key and cache. Do not touch `/Applications/Ollama.app` or `/usr/local/bin/ollama` (the older v0.35.1 install).
Dependencies installed with Ollama (`openssl@4`, `python@3.15`, `mlx`, `mlx-c`): check first with `brew uses --installed <name>`, then `brew autoremove --dry-run` (shows what would go), then `brew autoremove`. Do not remove `python@3.12`: `.venv` may depend on it (UNVERIFIED which Python the venv was built from; check `readlink .venv/bin/python`).

Restore the previous `python3` (3.14) and `openssl@3` links. Commands are from Homebrew's manpage (https://docs.brew.sh/Manpage: "brew link", "--overwrite", "--dry-run", "brew unlink"); that they give exactly your old state is **UNVERIFIED**. Not run:
```bash
brew link --overwrite --dry-run python@3.14   # lists what would be (re)linked or deleted
brew link --overwrite python@3.14             # python3, pip3, idle3, pydoc3, python3-config back to 3.14
brew link --overwrite --dry-run openssl@3
brew link --overwrite openssl@3
```
The overwritten originals were backed up by Homebrew to `~/Library/Caches/Homebrew/Backup` (as reported by Dev). `openssl@3` shows two installed versions (3.6.4, 3.6.3) and `python@3.14` two (3.14.7, 3.14.6); `brew link` links the latest installed. If a keg-only formula refuses, `--force` is documented for that case; UNVERIFIED that it applies here. Check afterwards with `python3 --version` and `brew doctor`.
