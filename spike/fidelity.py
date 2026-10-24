# Fidelity: same 30 states + question through Ollama vs official MLX probs in spike/runs/clean/mlx/latency.csv.
# Usage: .venv/bin/python -I spike/fidelity.py [host:port]. Sequential, localhost only. Not a memory measurement.
import csv, json, statistics, sys, time, urllib.request
host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:11434"
Q = "Should Dev be interrupted right now for this event?"
mlx = {r["id"]: float(r["noul"]) for r in csv.DictReader(open("spike/runs/clean/mlx/latency.csv"))}
rows = [json.loads(l) for l in open("spike/states.jsonl") if l.strip()]
def ask(state):
    body = json.dumps({"model": "strands-decider:2b-mxfp8", "state": state,
                       "questions": {"interrupt": {"type": "noul", "instructions": Q}}}).encode()
    req = urllib.request.Request(f"http://{host}/v1/systemone", data=body, headers={"content-type": "application/json"})
    s = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.load(r)
    return d["answers"]["interrupt"]["noul"], time.time() - s
print("| state id | mlx prob | ollama prob | abs diff |\n|---|---|---|---|")
diffs = []
for r in rows:
    p, _ = ask(r["state"])
    d = abs(p - mlx[r["id"]]); diffs.append(d)
    print(f"| {r['id']} | {mlx[r['id']]:.4f} | {p:.4f} | {d:.4f} |")
mx, md = max(diffs), statistics.median(diffs)
print(f"\nmax abs diff {mx:.4f}, median abs diff {md:.4f}, n={len(diffs)}")
print("FIDELITY", "PASS" if mx <= 0.05 else "FAIL", "(rule: max abs diff <= 0.05)")
