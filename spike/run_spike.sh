#!/usr/bin/env bash
# Measured spike run. Usage: spike/run_spike.sh <idle|clean|normal> [engines]
#   engines: comma list from mlx,mps,ollama (default: mlx,mps). Run in the order given.
#   idle: records system stats for 5 minutes with no model loaded (observation only); engines ignored.
# Localhost only; weights must already be cached. Never uses sudo.
# Output: spike/runs/<label>/<engine>/... (an existing engine dir is never overwritten)
# Ollama: only /opt/homebrew/bin/ollama (>= 0.40.0), serve started by hand on 127.0.0.1, models in .cache/ollama.
set -euo pipefail

LABEL="${1:-}"
case "$LABEL" in
  idle|clean|normal|trial) ;;   # "trial" is only for checking this script itself
  *) echo "usage: $0 <idle|clean|normal> [mlx,mps,ollama]" >&2; exit 2 ;;
esac
ENGINES="${2:-mlx,mps}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export HF_HOME="$ROOT/.cache/huggingface"
export PIP_CACHE_DIR="$ROOT/.cache/pip"
export HF_HUB_OFFLINE=1

PY="$ROOT/.venv/bin/python"
SERVE="$ROOT/.venv/bin/strands-decider"
MODEL="StrandsAgents/strands-decider-2B-qwen3.5-v1-2610"
STATES="$ROOT/spike/states.jsonl"
QUESTION="Should Dev be interrupted right now for this event?"
IFS=',' read -r -a VARIANTS <<< "$ENGINES"
[ "$LABEL" = "idle" ] && VARIANTS=(system)
for v in "${VARIANTS[@]}"; do
  case "$v" in mlx|mps|ollama|system) ;; *) echo "unknown engine: $v" >&2; exit 2 ;; esac
done
OLLAMA_BIN=/opt/homebrew/bin/ollama
OLLAMA_MODEL="strands-decider:2b-mxfp8"
OLLAMA_PORT=11436
LIMIT="${SPIKE_LIMIT:-30}"       # only lowered for the "trial" label
RUN_DIR="$ROOT/spike/runs/$LABEL"

[ -x "$SERVE" ] || { echo "missing $SERVE" >&2; exit 1; }
[ -f "$STATES" ] || { echo "missing $STATES" >&2; exit 1; }
for v in "${VARIANTS[@]}"; do
  [ ! -e "$RUN_DIR/$v" ] || { echo "$RUN_DIR/$v already exists; move it away first" >&2; exit 1; }
done
if pgrep -f "strands-decider serve" >/dev/null; then
  echo "a strands-decider server is already running; stop it first" >&2; exit 1
fi

# Ollama processes, excluding this script and its own forked subshells (their command line also says "ollama").
ollama_procs() { pgrep -fl ollama | grep -v run_spike.sh || true; }

# Ollama pre-flight (every label): right binary, new enough, and nothing Ollama already running.
[ -x "$OLLAMA_BIN" ] || { echo "missing $OLLAMA_BIN" >&2; exit 1; }
OLLAMA_VER="$("$OLLAMA_BIN" --version 2>&1 | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | tail -1 || true)"
[ -n "$OLLAMA_VER" ] || { echo "could not read the version of $OLLAMA_BIN" >&2; exit 1; }
if [ "$(printf '%s\n0.40.0\n' "$OLLAMA_VER" | sort -V | head -1)" != "0.40.0" ]; then
  echo "$OLLAMA_BIN is version $OLLAMA_VER; the strands-decider model needs 0.40.0 or later" >&2; exit 1
fi
if [ -n "$(ollama_procs)" ]; then
  ollama_procs
  echo "an ollama process is already running (listed above); quit it first so it cannot pollute the numbers" >&2; exit 1
fi

OLLAMA_SPID=""
cleanup() {   # stop anything we started; verify nothing is left
  [ -n "$OLLAMA_SPID" ] && kill "$OLLAMA_SPID" 2>/dev/null || true
  sleep 1
  if [ -n "$(ollama_procs)" ]; then
    pkill -f "$OLLAMA_BIN" 2>/dev/null || true
    sleep 1
  fi
  if [ -n "$(ollama_procs)" ]; then ollama_procs; echo "WARNING: ollama processes still running (listed above)" >&2; else echo "ollama: no processes left"; fi
}
trap cleanup EXIT

now() { perl -MTime::HiRes=time -e 'printf "%.3f", time'; }

# Descendants of a pid (the Ollama runner is a child of `ollama serve`).
tree_pids() {
  local p="$1" c
  echo "$p"
  for c in $(pgrep -P "$p" 2>/dev/null || true); do tree_pids "$c"; done
}

# sample_mem_tree <root pid> <csv>: phys_footprint, its peak and RSS SUMMED over serve + every descendant.
sample_mem_tree() {
  local root="$1" out="$2" pids pid fp v u rss swap sumfp sumpk sumrss
  echo "ts,rss_kb,phys_footprint,phys_footprint_peak,swap_used,pids" > "$out"
  while kill -0 "$root" 2>/dev/null; do
    pids="$(tree_pids "$root" | tr '\n' ' ')"
    sumfp=0; sumpk=0; sumrss=0
    for pid in $pids; do
      fp="$(footprint "$pid" --noCategories 2>/dev/null || true)"
      v="$(printf '%s\n' "$fp" | awk '/^ *phys_footprint:/ {x=$2; u=$3; if(u=="KB")x/=1024; else if(u=="GB")x*=1024; print x; exit}')"
      sumfp="$(awk -v a="$sumfp" -v b="${v:-0}" 'BEGIN{print a+b}')"
      v="$(printf '%s\n' "$fp" | awk '/^ *phys_footprint_peak:/ {x=$2; u=$3; if(u=="KB")x/=1024; else if(u=="GB")x*=1024; print x; exit}')"
      sumpk="$(awk -v a="$sumpk" -v b="${v:-0}" 'BEGIN{print a+b}')"
      rss="$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
      sumrss=$((sumrss + ${rss:-0}))
    done
    swap="$(sysctl -n vm.swapusage | sed -n 's/.*used = \([0-9.]*[MGK]\).*/\1/p')"
    echo "$(now),${sumrss},${sumfp} MB,${sumpk} MB,${swap},$(echo $pids | tr ' ' '+')" >> "$out"
    sleep 0.5
  done
}

snapshot() {   # snapshot <dir> <tag>   (tag = before|after)
  local dir="$1" tag="$2"
  { date; sysctl vm.swapusage; } > "$dir/swap_$tag.txt"
  vm_stat > "$dir/vm_stat_$tag.txt"
  memory_pressure > "$dir/memory_pressure_$tag.txt" 2>&1 || true   # status only, no simulation flags
}

sample_mem() {   # sample_mem <pid> <csv>
  local pid="$1" out="$2" fp rss swap fpv peak
  echo "ts,rss_kb,phys_footprint,phys_footprint_peak,swap_used" > "$out"
  while kill -0 "$pid" 2>/dev/null; do
    rss="$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
    fp="$(footprint "$pid" --noCategories 2>/dev/null || true)"
    fpv="$(printf '%s\n' "$fp" | sed -n 's/^ *phys_footprint: *//p' | head -1)"
    peak="$(printf '%s\n' "$fp" | sed -n 's/^ *phys_footprint_peak: *//p' | head -1)"
    swap="$(sysctl -n vm.swapusage | sed -n 's/.*used = \([0-9.]*[MGK]\).*/\1/p')"
    echo "$(now),${rss},${fpv},${peak},${swap}" >> "$out"
    sleep 0.5
  done
}

mkdir -p "$RUN_DIR"
{
  echo "label=$LABEL"; date
  uname -m; sysctl -n machdep.cpu.brand_string; sw_vers; sysctl -n hw.memsize
  "$PY" --version
  "$PY" -m pip list 2>/dev/null | grep -i -E '^(strands-decider|torch|transformers|mlx|mlx-lm) ' || true
  [ -x "$OLLAMA_BIN" ] && echo "ollama $OLLAMA_VER ($OLLAMA_BIN)"
} > "$RUN_DIR/meta.txt.new"
if [ -e "$RUN_DIR/meta.txt" ]; then mv "$RUN_DIR/meta.txt.new" "$RUN_DIR/meta_$(date +%H%M%S).txt"; else mv "$RUN_DIR/meta.txt.new" "$RUN_DIR/meta.txt"; fi

port=8030
for variant in "${VARIANTS[@]}"; do
  port=$((port + 1))
  VDIR="$RUN_DIR/$variant"
  mkdir -p "$VDIR"
  [ "$variant" = "ollama" ] && port=$OLLAMA_PORT
  echo "=== $variant (port $port) ==="
  snapshot "$VDIR" before

  if [ "$variant" = "system" ]; then   # idle: no model; 5 minutes of system stats every 5 s
    echo "ts,swap_used,swapouts,free_pct" > "$VDIR/system.csv"
    for _ in $(seq "${SPIKE_IDLE_ITERS:-60}"); do
      echo "$(now),$(sysctl -n vm.swapusage | sed -n 's/.*used = \([0-9.]*[MGK]\).*/\1/p'),$(vm_stat | sed -n 's/^Swapouts: *\([0-9]*\).*/\1/p'),$(memory_pressure 2>&1 | sed -n 's/.*free percentage: *\([0-9]*\)%.*/\1/p')" >> "$VDIR/system.csv"
      sleep 5
    done
    sleep 30
    snapshot "$VDIR" after
    echo "done: $VDIR (idle, observation only)"
    continue
  fi

  MODEL_FIELD=""
  T0="$(now)"
  if [ "$variant" = "ollama" ]; then
    port=$OLLAMA_PORT
    MODEL_FIELD="$OLLAMA_MODEL"
    OLLAMA_MODELS="$ROOT/.cache/ollama" OLLAMA_HOST="127.0.0.1:$port" OLLAMA_KEEP_ALIVE=-1 \
      "$OLLAMA_BIN" serve > "$VDIR/server.log" 2>&1 &
    SPID=$!
    OLLAMA_SPID=$SPID
    sample_mem_tree "$SPID" "$VDIR/mem.csv" &
  else
    "$SERVE" serve "$MODEL" --device "$variant" --host 127.0.0.1 --port "$port" > "$VDIR/server.log" 2>&1 &
    SPID=$!
    sample_mem "$SPID" "$VDIR/mem.csv" &
  fi
  MPID=$!
  echo "$SPID" > "$VDIR/server.pid"

  # Cold load = launch -> first successful /v1/systemone answer. Then 30 timed requests.
  "$PY" -I - "$port" "$T0" "$STATES" "$VDIR" "$LIMIT" "$QUESTION" "$MODEL_FIELD" <<'PY' || echo "client reported errors (see latency.csv)" >&2
import csv, json, sys, time, urllib.request, urllib.error
port, t0, states, outdir, limit, question, model = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4], int(sys.argv[5]), sys.argv[6], sys.argv[7]
url = f"http://127.0.0.1:{port}/v1/systemone"

def ask(state, timeout):
    payload = {"state": state, "questions": {"interrupt": {"type": "noul", "instructions": question}}}
    if model:
        payload["model"] = model
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    s = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return (time.time() - s) * 1000, data

rows = [json.loads(l) for l in open(states) if l.strip()][:limit]

# Cold: retry the first state until the server answers (up to 10 min).
cold = None
while time.time() - t0 < 600:
    try:
        ms, data = ask(rows[0]["state"], 300)
        cold = time.time() - t0
        break
    except (urllib.error.URLError, ConnectionError, OSError):
        time.sleep(0.25)
with open(f"{outdir}/cold_load.txt", "w") as f:
    f.write(f"{cold if cold is not None else 'FAILED'}\n")
if cold is None:
    sys.exit(1)
print(f"cold load (launch -> first answer): {cold:.1f}s")

errors = 0
with open(f"{outdir}/latency.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["idx", "id", "source", "client_ms", "server_ms", "noul", "input_tokens", "error"])
    for i, row in enumerate(rows):
        try:
            ms, data = ask(row["state"], 300)
            a = data["answers"]["interrupt"]
            w.writerow([i, row["id"], row["source"], f"{ms:.1f}", data.get("latency_ms"), a["noul"], data.get("usage", {}).get("input_tokens"), ""])
        except Exception as e:
            errors += 1
            w.writerow([i, row["id"], row["source"], "", "", "", "", type(e).__name__])
        f.flush()
sys.exit(1 if errors else 0)
PY

  # Final peak reading, then stop the server and the sampler.
  if [ "$variant" = "ollama" ]; then
    { for pid in $(tree_pids "$SPID"); do echo "--- pid $pid: $(ps -o command= -p "$pid")"; footprint "$pid" --noCategories 2>&1 || true; done; } > "$VDIR/footprint_final.txt"
    ollama_procs > "$VDIR/processes_during.txt"
  else
    footprint "$SPID" --noCategories > "$VDIR/footprint_final.txt" 2>&1 || true
  fi
  kill "$SPID" 2>/dev/null || true
  wait "$SPID" 2>/dev/null || true
  OLLAMA_SPID=""
  if [ "$variant" = "ollama" ]; then pkill -f "$OLLAMA_BIN" 2>/dev/null || true; ollama_procs > "$VDIR/processes_after_stop.txt"; fi
  wait "$MPID" 2>/dev/null || true
  echo "waiting 30 s for the system to settle..."
  sleep 30
  snapshot "$VDIR" after
  echo "done: $VDIR"
done

echo "All variants finished. Next: $PY -I $ROOT/spike/analyze_spike.py"
