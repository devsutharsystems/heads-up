# Spike report: can the Decider run on this laptop?

**Outcome (final, Ollama fallback, rule set 23 Oct 2026 19:33): clean/ollama PASSES (swap 0.0 -> 0.0 MB, warm p95 0.313 s, Dev: usable) and fidelity PASSES (max abs diff 0.0392, median 0.0091, limit 0.05), so the chosen design is the third-party Ollama MXFP8 build (`strands-decider:2b-mxfp8`; not the official weights; Prateek must be told). Idle and normal are observations only.**

## History: earlier MLX outcome (superseded by the line above)
**Outcome (decision rule of 23 Oct 2026 applied to MLX results): clean FAILS (swap grew 0.0 -> 222.8 MB), so the rule row is "clean fails -> third-party MXFP8 build via Ollama"; this needs Dev's approval first, and Prateek must be told it is not the official weights. Nothing has been installed or changed.**

Machine: Apple M2, 8 GB RAM, macOS 26.6.2 (25G83), Python 3.12.14, strands-decider 0.2.0, mlx 0.32.4, mlx-lm 0.32.2, torch 2.14.1, transformers 5.20.2. Model: `StrandsAgents/strands-decider-2B-qwen3.5-v1-2610` (official weights). 30 synthetic states per engine, one request at a time, localhost, offline.

Run times (from meta.txt): clean started Fri 23 Oct 2026 19:14:29 IST; normal started 19:21:09 IST.

## Commands (exact)
```bash
cd "$HOME/Documents/Lets Data Science/week6/heads-up"
./spike/run_spike.sh clean      # fresh reboot, nothing else open
./spike/run_spike.sh normal     # normal apps open
.venv/bin/python -I spike/analyze_spike.py
```
Inside the script, per engine (mlx, then mps; ports 8031, 8032):
`strands-decider serve StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 --device <mlx|mps> --host 127.0.0.1 --port <port>` with `HF_HUB_OFFLINE=1`; question: "Should Dev be interrupted right now for this event?" (type `noul`); then 30 s settle before the "after" snapshot.

## Criteria and how they are judged
1. Swap does not grow: swap used "after" (30 s after server stop) must not exceed "before".
2. Warm p95 <= 5 s (nearest rank, 30 requests, first request counted as cold).
3. Mac usable: Dev's notes below.
Pass/fail is judged on **MLX only**. **MPS is reference only**: it ran after MLX in the same run, so it started with MLX's leftover swap and is not a fair test.

## Dev's usability notes
| Run | MLX | MPS (reference) |
|---|---|---|
| clean | usable | laggy |
| normal | usable | laggy |

## clean / MLX (judged)
| metric | value |
|---|---|
| cold load (launch -> first answer) | 11.0 s |
| requests ok / errors | 30 / 0 |
| warm median | 0.208 s |
| warm p95 (nearest rank) | 0.234 s |
| warm max | 1.036 s |
| peak RSS (ps; may undercount GPU memory) | 1914 MB |
| peak phys footprint (sampled) | 5040 MB |
| peak phys footprint (kernel lifetime peak) | 5040 MB |
| memory samples | 26 |
| swap used before / after | 0.0 / 222.8 MB |
| swap used, max during run | 446.8 MB |
| swapouts delta (pages, vm_stat) | 29616 |
| memory_pressure free % before / after | 79 / 75 |
| noul min / median / max | 0.1213 / 0.2362 / 0.6040 |
| **Swap does not grow** | **FAIL** (+222.8 MB) |
| **Warm p95 <= 5 s** | **PASS** (0.234 s) |
| **Mac usable** | **PASS** (Dev: usable) |

## clean / MPS (reference only)
| metric | value |
|---|---|
| cold load (launch -> first answer) | 28.7 s |
| requests ok / errors | 30 / 0 |
| warm median | 0.371 s |
| warm p95 (nearest rank) | 0.471 s |
| warm max | 0.981 s |
| peak RSS (ps; may undercount GPU memory) | 1006 MB |
| peak phys footprint (sampled) | 4891 MB |
| peak phys footprint (kernel lifetime peak) | 4891 MB |
| memory samples | 42 |
| swap used before / after | 222.8 / 506.1 MB |
| swap used, max during run | 1688.9 MB |
| swapouts delta (pages, vm_stat) | 214036 |
| memory_pressure free % before / after | 74 / 79 |
| noul min / median / max | 0.1209 / 0.2368 / 0.6052 |
| Swap does not grow | FAIL (analyzer; reference only, started with MLX's leftover 222.8 MB) |
| Warm p95 <= 5 s | PASS (analyzer; reference only) |
| Mac usable | Dev: laggy (reference only) |

## normal / MLX (judged)
| metric | value |
|---|---|
| cold load (launch -> first answer) | 17.3 s |
| requests ok / errors | 30 / 0 |
| warm median | 0.206 s |
| warm p95 (nearest rank) | 0.252 s |
| warm max | 0.721 s |
| peak RSS (ps; may undercount GPU memory) | 1421 MB |
| peak phys footprint (sampled) | 5040 MB |
| peak phys footprint (kernel lifetime peak) | 5040 MB |
| memory samples | 32 |
| swap used before / after | 252.8 / 2738.9 MB |
| swap used, max during run | 3268.6 MB |
| swapouts delta (pages, vm_stat) | 211848 |
| memory_pressure free % before / after | 44 / 74 |
| noul min / median / max | 0.1213 / 0.2362 / 0.6040 |
| **Swap does not grow** | **FAIL** (+2486.1 MB) |
| **Warm p95 <= 5 s** | **PASS** (0.252 s) |
| **Mac usable** | **PASS** (Dev: usable) |

## normal / MPS (reference only)
| metric | value |
|---|---|
| cold load (launch -> first answer) | 66.2 s |
| requests ok / errors | 30 / 0 |
| warm median | 0.407 s |
| warm p95 (nearest rank) | 1.155 s |
| warm max | 5.976 s |
| peak RSS (ps; may undercount GPU memory) | 1207 MB |
| peak phys footprint (sampled) | 4885 MB |
| peak phys footprint (kernel lifetime peak) | 4887 MB |
| memory samples | 76 |
| swap used before / after | 2738.9 / 2696.6 MB |
| swap used, max during run | 6537.5 MB |
| swapouts delta (pages, vm_stat) | 525103 |
| memory_pressure free % before / after | 74 / 72 |
| noul min / median / max | 0.1209 / 0.2368 / 0.6052 |
| Swap does not grow | PASS (analyzer; reference only, it started with 2738.9 MB already used by the MLX run) |
| Warm p95 <= 5 s | PASS (analyzer; reference only; warm max 5.976 s) |
| Mac usable | Dev: laggy (reference only) |

## Summary (MLX only)
| Run | Swap does not grow | Warm p95 <= 5 s | Mac usable | Run result |
|---|---|---|---|---|
| clean | FAIL | PASS | PASS | **FAIL** |
| normal | FAIL | PASS | PASS | **FAIL** |

## Applying the decision rule (set 23 Oct 2026, before the measured runs)
- Clean does not pass (swap criterion), so the "clean fails" row applies: **third-party MXFP8 build via Ollama**. It runs on the laptop (requirement 7 holds) but is not the official weights, needs Ollama installed outside this folder, and its accuracy must be re-checked on the tune split. **Needs Dev's approval; tell Prateek it is not the official weights.**
- The on-demand row does NOT apply (it needs clean to pass). No load-decide-unload cycle has been measured.

## Observations (not rule inputs)
- Latency is not the problem: warm p95 0.234 s (clean) and 0.252 s (normal) against a 5 s limit; 30/30 requests ok.
- Memory is the problem: peak phys footprint 5040 MB of 8192 MB on MLX. Swap rose 222.8 MB in clean and 2486.1 MB in normal (252.8 -> 2738.9 MB).
- The clean run's "before" swap was 0.0 MB, so the clean run is a clean test. The normal run began 6 minutes after clean started (19:21 vs 19:14), and its MLX "before" swap was 252.8 MB, so it is a "normal apps open" run rather than a fresh-boot one.
- The clean failure margin is small in absolute terms (222.8 MB) but the criterion as set is "does not grow", and I have not relaxed it. If Dev wants to reconsider the criterion, that is Dev's decision and would change the rule after seeing results.
- Unmeasured and still to test if the on-demand route is ever considered: load + 30 decisions time (cold load alone was 11.0 s clean / 17.3 s normal on MLX, and warm 30 requests are about 6 s of compute, so the 2-minute limit looks plausible but is not measured); swap after unload; usability during load.

## Ollama fallback (rule set Fri 23 Oct 2026 19:33:44 IST, before any Ollama data)
Rule: same three criteria on the clean run, plus fidelity (30 states through Ollama vs the official MLX probabilities; pass if max abs diff <= 0.05). Clean + fidelity decide; idle and normal are observations only. Ollama 0.40.9 (`/opt/homebrew/bin/ollama`), model `strands-decider:2b-mxfp8`, model files in `.cache/ollama`. Run times: idle 20:04:27 IST, clean 20:10:02 IST, normal 20:11:22 IST (from the meta files). After the runs `pgrep -fl ollama` printed nothing.

### Dev's usability notes (Ollama)
| Run | Ollama |
|---|---|
| clean | usable |
| normal | usable |

### Fidelity (Ollama MXFP8 vs official MLX, `spike/fidelity.py`)
max abs diff 0.0392, median abs diff 0.0091, n=30. **FIDELITY PASS** (rule: max abs diff <= 0.05).

### clean / ollama (judged)
| metric | value |
|---|---|
| cold load (launch -> first answer) | 2.6 s |
| requests ok / errors | 30 / 0 |
| warm median | 0.228 s |
| warm p95 (nearest rank) | 0.313 s |
| warm max | 0.410 s |
| peak RSS (ps; may undercount GPU memory) | 322 MB |
| peak phys footprint (sampled) | 3229 MB |
| peak phys footprint (kernel lifetime peak) | 3332 MB |
| memory samples | 15 |
| swap used before / after | 0.0 / 0.0 MB |
| swap used, max during run | 0.0 MB |
| swapouts delta (pages, vm_stat) | 0 |
| memory_pressure free % before / after | 77 / 72 |
| noul min / median / max | 0.1274 / 0.2409 / 0.6104 |
| CRITERION swap does not grow | PASS |
| CRITERION warm p95 <= 5 s | PASS |
| CRITERION Mac stayed usable | ____ (Dev fills in) |

| Rule result | |
|---|---|
| Swap does not grow | **PASS** (0.0 -> 0.0 MB) |
| Warm p95 <= 5 s | **PASS** (0.313 s) |
| Mac usable | **PASS** (Dev: usable) |
| Fidelity | **PASS** (max 0.0392) |
| **Ollama result** | **PASS** |

### idle / system (observation only, not a rule input)
| metric | value |
|---|---|
| cold load (launch -> first answer) | n/a |
| requests ok / errors | 0 / 0 |
| warm median | n/a |
| warm p95 (nearest rank) | n/a |
| warm max | n/a |
| peak RSS (ps; may undercount GPU memory) | n/a |
| peak phys footprint (sampled) | n/a |
| peak phys footprint (kernel lifetime peak) | n/a |
| memory samples | 60 |
| swap used before / after | 0.0 / 0.0 MB |
| swap used, max during run | 0.0 MB |
| swapouts delta (pages, vm_stat) | 0 |
| memory_pressure free % before / after | 79 / 77 |
| noul min / median / max | n/a / n/a / n/a |
| CRITERION swap does not grow | PASS |
| CRITERION warm p95 <= 5 s | n/a |
| CRITERION Mac stayed usable | ____ (Dev fills in) |

### normal / ollama (observation only, not a rule input)
| metric | value |
|---|---|
| cold load (launch -> first answer) | 3.7 s |
| requests ok / errors | 30 / 0 |
| warm median | 0.247 s |
| warm p95 (nearest rank) | 2.018 s |
| warm max | 2.544 s |
| peak RSS (ps; may undercount GPU memory) | 181 MB |
| peak phys footprint (sampled) | 3169 MB |
| peak phys footprint (kernel lifetime peak) | 3299 MB |
| memory samples | 10 |
| swap used before / after | 0.0 / 2009.9 MB |
| swap used, max during run | 2545.9 MB |
| swapouts delta (pages, vm_stat) | 195360 |
| memory_pressure free % before / after | 38 / 70 |
| noul min / median / max | 0.1274 / 0.2409 / 0.6104 |
| CRITERION swap does not grow | FAIL |
| CRITERION warm p95 <= 5 s | PASS |
| CRITERION Mac stayed usable | ____ (Dev fills in) |

Dev: usable. By the analyzer this run's swap criterion reads FAIL (0.0 -> 2009.9 MB); under the 19:33 rule it is an observation, not a rule input.

## Observations (not rule inputs)
- **idle:** swap 0 -> 0 MB and 0 swapouts in 5 min on a fresh boot, consistent with the MLX clean growth (+222.8 MB) being caused by the model rather than by macOS on its own.
- **clean:** Ollama peak footprint 3,332 MB vs MLX 5,040 MB; cold load 2.6 s vs 11.0 s.
- **normal:** with my usual apps open (38% memory free before), Ollama still pushed ~2.0 GB to swap (MLX ~2.5 GB). A smaller build alone does not make an always-loaded judge comfortable on 8 GB.
- Analyzer fix: "swap used, max during run" showed n/a for idle although samples exist (`runs/idle/system/system.csv`, 60 rows). The analyzer now reads that file; the idle table above shows 0.0 MB. The analyzer also skips empty folders (the empty `runs/trial` folder was deleted).
