# Heads-Up: PLAN (Phase 0)

Status: DRAFT, 23 Oct 2026 (Phase 0 review fixes applied 24 Oct 2026). **Spike done. Official weights on MLX FAILED the swap criterion (history); the Ollama MXFP8 build PASSED clean (swap 0.0 -> 0.0 MB, p95 0.313 s, Dev: usable) and fidelity (max abs diff 0.0392 <= 0.05). Chosen design: that third-party build, NOT the official weights (Dev tells Prateek).** See spike/SPIKE_REPORT.md.
Rule used throughout: anything I could not confirm from an official page is marked **UNVERIFIED**.

**Time log** (`date`): research session earlier today; continuation started `Fri Oct 23 15:59:04 IST 2026`, time-box 75 min (to ~17:14).

### Project facts (decided by Dev)
- Watched repo (read-only): `strands-labs/strands-decider`.
- CI re-run target and demo web page: `devsutharsystems/heads-up-sandbox` (not created yet).
- Calendar: Google Calendar on a personal Gmail account, secret iCal address via the `ICAL_URL` env var.
- Evaluation sources and synthetic share: GitHub = real historical events from `strands-labs/strands-decider` (synthetic share 0%); calendar = synthetic only (100% synthetic, none from the real calendar); web page = replayed historical snapshots of a real public changelog file (to be chosen) plus labelled synthetic ones (share to be stated once the mix is fixed; target at most about 40%, a plan not a result). Overall synthetic share is therefore at least about 1/3 (calendar) and will be reported with the final numbers.
- Cut now: the LaunchAgent (use `--once` from the shell or cron by hand later) and the Haiku-as-decider baseline (the only baselines are the frozen rules baseline and the Decider).

### Preflight (23 Oct 2026, this Mac)
| Check | Result |
|---|---|
| Architecture / CPU | arm64, Apple M2: Apple silicon (MLX path available) |
| macOS | 26.6.2 (25G83) |
| RAM | 8 GB (8,589,934,592 bytes) |
| Free disk | 71 GiB |
| Default python3 | 3.14.7 at preflight (not used); Homebrew relinked python3 to 3.15 on 23 Oct; not used by this project. `.venv` uses python3.12.14 |
| Swap before any work | 3,916 MB used (swap file size varies: 5,120 MB then 4,096 MB) |

### Spike result (measured 23 Oct 2026; details in spike/SPIKE_REPORT.md)
- MLX, clean: cold load 11.0 s, warm p95 0.234 s, swap 0.0 -> 222.8 MB (FAIL), Dev: usable. MLX, normal: cold load 17.3 s, warm p95 0.252 s, swap 252.8 -> 2738.9 MB (FAIL), Dev: usable. Peak phys footprint 5040 MB of 8192 MB.
- MPS (reference only; ran after MLX with its leftover swap): Dev: laggy in both runs.
- Rule set before the runs: clean fails -> third-party MXFP8 build via Ollama.
- **Ollama MXFP8 (measured 23 Oct 2026, rule set 19:33; clean + fidelity decide):** clean: cold load 2.6 s, warm p95 0.313 s, swap 0.0 -> 0.0 MB, peak footprint 3,332 MB, Dev: usable -> PASS. Fidelity vs official MLX on the 30 states: max abs diff 0.0392, median 0.0091 -> PASS. Observations only: idle swap 0 -> 0 MB; normal (38% free before): cold load 3.7 s, p95 2.018 s, swap 0.0 -> 2009.9 MB (MLX normal: +2486.1 MB), Dev: usable.
- **Chosen design: Strands Decider 2B as Ollama's MXFP8 build `strands-decider:2b-mxfp8` (a third-party conversion of the official weights, about 2.0 GB), served by `/opt/homebrew/bin/ollama` on 127.0.0.1 with model files in `.cache/ollama`.** Requirement 7 is met through this build (SPIKE_REPORT: clean PASS, fidelity PASS). Dev tells Prateek it is not the official weights.
- Ollama created `~/.ollama/` outside the project; uninstall steps are in spike/README.md.

### Spike setup result (installed, smoke-tested; NOT measurements)
- `.venv` (Python 3.12.14) + `pip install "strands-decider[mlx]"` from PyPI worked, so **no clone of the repo was needed**: the PyPI 0.2.0 wheel does ship the `mlx` extra (the README/docs sentence "ships with the next release" is stale for 0.2.0).
- Smoke test, MLX: first request 26.4 s (includes lazy load), second 3.2 s, noul 0.2405. Smoke test, MPS: 34.0 s then 24.2 s, noul 0.2386. Single requests only; the Mac was also running Claude Code, so none of this counts.
- **RSS undercounts GPU memory on Apple silicon**: MLX server RSS 76 MB while `footprint` reported 4,215 MB (peak 4,379 MB, 3,747 MB in "IOAccelerator (graphics)"). MPS: RSS 47 MB vs footprint 4,807 MB (peak 4,809 MB). `footprint <pid>` works without sudo. Use phys footprint as the memory number; RSS is logged only because it was asked for.
- Strands' own MLX numbers: peak 3.8 to 4.9 GiB per request, 3.7 GiB at load; MPS driver 4.2 to 6.3 GiB (evaluation/results.md, "Serving on a Mac through MLX: accuracy and latency"). Our footprints (4.2 to 4.8 GB) are in line.
- The weights are two downloads: base `Qwen/Qwen3.5-2B-Base` (about 4.5 GB) plus the Decider adapter (adapter_model.safetensors 201,878,888 bytes, head.safetensors 12,605,928 bytes per the Hugging Face file list). `.cache` is 4.7 GB after download.

---

## 1. What it is
A local Python assistant that watches (a) one GitHub repo, (b) one static web page, (c) one iCal calendar. For each change it asks a small local model, Strands Decider 2B, "should Dev be interrupted right now?". Worth it: a short Telegram message (written by Claude Haiku 5.5, template fallback) with allowlisted buttons. Not worth it: goes into a daily digest.

## 2. Why now
Anthropic opened a research preview of Claude Duet (a proactive agent) on 21 Oct 2026 by waitlist, for Pro and Max users in the US only. Heads-Up is a small local project inspired by that idea, usable without waiting. (Sources: [A18], [A19]. No logos or branding used.)

## 3. How it works
```
collect -> normalize (one Event schema) -> dedupe (hash of source+id+content)
  -> Decider probability (local, noul question)
  -> threshold + daily interrupt budget
       yes -> Haiku writes message text (template if Haiku fails) -> Telegram + buttons
       no  -> daily digest
  -> every step written to SQLite decision log (event, prob, threshold, verdict, tokens, cost)
```
**Memory policy (DESIGN DECISION, not a measured pass): the Decider is NOT kept loaded.** Each request carries a short `keep_alive`, proposed 60 s, so Ollama unloads the model shortly after each batch. `OLLAMA_KEEP_ALIVE` is documented: models stay loaded 5 minutes by default; the env var, set when starting the server, changes that; a per-request `keep_alive` on `/api/generate` and `/api/chat` overrides it (0 unloads right after the response, negative keeps loaded) (Ollama FAQ, https://docs.ollama.com/faq, read 24 Oct 2026). The spike itself used `OLLAMA_KEEP_ALIVE=-1` (spike/README.md). **UNVERIFIED:** only whether `/v1/systemone` honours a per-request `keep_alive` (the FAQ names other endpoints); the server-wide `OLLAMA_KEEP_ALIVE=60s` is the fallback. Why: in the normal run (38% memory free before) the loaded model still pushed 2,009.9 MB to swap, and cold load is only 2.6 s (clean) to 3.7 s (normal), so reloading per batch is cheap. A full load-decide-unload cycle, and swap after unload, are not measured.
Failure modes: Decider down or too slow -> digest-only (never interrupt blind). Haiku down/refusal/timeout -> template text. Telegram down -> retry next run; nothing lost (SQLite).

Event schema (draft): `id, source, kind, title, body(untrusted), url, occurred_at, fetched_at, content_hash`.

## 4. How we build it (planned tree; nothing in it exists yet except .gitignore, PLAN.md and spike/; building waits for Prateek's go-ahead)
```
heads-up/
  pyproject.toml            deps + pinned versions
  .gitignore                keeps secrets, caches, db out of git (exists)
  .env.example              every env var name, empty values (no secrets)
  README.md                 what it is, eval persona, limits, traceability table
  LICENSE                   MIT (pending Prateek's OK)
  PLAN.md                   this file
  src/headsup/
    __main__.py             CLI: --once, --dry-run
    config.py               reads env vars only; refuses to log them
    schema.py               Event + Decision dataclasses (one shape for all sources)
    store.py                SQLite: seen events, decisions, button taps, cost log
    sources/github.py       ETag polling of releases/issues/PRs/CI runs (failed and recovered)
    sources/webpage.py      static page fetch + text diff (If-None-Match)
    sources/ical.py         iCal fetch, recurring expansion, upcoming-event changes
    safe_fetch.py           SSRF-safe HTTP (DNS check, redirect re-check, robots.txt)
    dedupe.py               drop events already seen
    decider.py              client for Ollama's /v1/systemone (with the `model` field); Ollama down or slow => digest-only
    policy.py               threshold + daily budget; "Decider down" => digest
    writer.py               Haiku call (+ usage capture) and template fallback
    telegram.py             sendMessage, getUpdates long poll, answerCallbackQuery
    actions.py              fixed allowlist, expiry, idempotency, the only GitHub write
    digest.py               daily digest builder
    costlog.py              per-call tokens -> dollars -> per-day total
  tests/                    one test file per requirement (section 7)
  eval/
    baseline_rules.py       frozen rules baseline (written BEFORE rubric)
    rubric.md               labelling rubric (written BEFORE labelling)
    events.jsonl, split.json  ~150 labelled events + seeded split
    run_eval.py             metrics + bootstrap CIs
  spike/                    Phase 0. In git (small, synthetic, reproducible): README.md, SPIKE_REPORT.md,
                            run_spike.sh, analyze_spike.py, fidelity.py, states.jsonl. runs/ stays ignored.
```

## 5. Where
Runs on this Mac only (Apple M2, 8 GB RAM; see preflight). No cloud, no public URL, so Telegram uses **getUpdates long polling**, not webhooks. Modes: `--once` (one collect/decide/notify pass, then exit; for cron/launchd) and `--dry-run` (everything except Telegram sends and GitHub writes; prints what it would do).

## 6. Resources

### Libraries (licence : source)
| Library | Purpose | Licence | Source |
|---|---|---|---|
| strands-decider 0.2.0 | local Decider | Apache-2.0 | [A1],[A2] |
| torch 2.14.1 (Decider dep) | model runtime | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT (PyPI `license_expression`) | https://pypi.org/pypi/torch/json |
| transformers 5.20.2 (Decider dep) | model runtime | Apache 2.0 (PyPI `license` field) | https://pypi.org/pypi/transformers/json |
| peft 0.21.2, accelerate 1.15.0 (Decider deps) | adapter + loading | UNVERIFIED (not asked) | - |
| mlx 0.32.4, mlx-lm 0.32.2 (`[mlx]` extra) | faster Mac serving | MIT, MIT | https://pypi.org/pypi/mlx/json ; https://pypi.org/pypi/mlx-lm/json |
| icalendar 7.3.0 | parse iCal | BSD-2-Clause | [A14],[A15] |
| recurring-ical-events 3.8.2 | expand RRULE/EXDATE | LGPL-3.0-or-later (dynamic use as a pip dependency; I am not giving legal advice) | [A16] |
| anthropic SDK 2.1.0 | Haiku calls | Apache-2.0 | https://pypi.org/pypi/anthropic/json |
| httpx 0.28.1 | HTTP (env vars only, no dotenv) | BSD-3-Clause | https://pypi.org/pypi/httpx/json |
| detect-secrets 1.5.0 | secret scan | Apache Software License (PyPI classifier; `license` field empty) | https://pypi.org/pypi/detect-secrets/json |
| Qwen3.5-2B-Base (Decider's base model) | weights | apache-2.0 (Hugging Face model metadata) | https://huggingface.co/api/models/Qwen/Qwen3.5-2B-Base |

### Accounts, tokens, minimum scopes
| Env var | Needed for | Minimum scope |
|---|---|---|
| GITHUB_TOKEN | read repo | CORRECTED: a fine-grained token is limited to ONE resource owner (user or org) and cannot be scoped to a repo we do not own, so it cannot be limited to `strands-labs/strands-decider`. The watched repo is public, and "Tokens always include read-only access to all public repositories" (https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens). So use a fine-grained token with resource owner = Dev's own account and NO extra permissions (public read-only) for authenticated rate limits; do not grant it Contents/Issues/PR/Actions permissions on our own repos. Whether ETag 304s are free when authorized is from [A6],[A7]; unauthenticated is 60/h. Private-repo or user-specific events (e.g. review requests) are not covered: UNVERIFIED |
| GITHUB_ACTIONS_WRITE_TOKEN | "Re-run failed CI" only | separate fine-grained token, ONE repo, **Actions: write** [A8] |
| ANTHROPIC_API_KEY | Haiku writer | API key (workspace spend limit recommended) |
| TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID | bot + allowlisted chat | create bot via BotFather (UNVERIFIED step, not fetched) |
| ICAL_URL | calendar | Google "Secret address in iCal format"; anyone with it can read the calendar; Reset if leaked [A13] |

### Cost estimate (ESTIMATE; my assumptions, to be replaced by logged real cost)
Prices: Haiku 5.5 input $0.10/MTok, output $0.50/MTok for prompts <=100K tokens [A10],[A11]. Thinking tokens bill as output [A12]. Effort set to `low`, so assume ~150 visible + ~300 thinking = 450 output tokens.
- Per message: input 700 tok x $0.10/1e6 = $0.00007; output 450 tok x $0.50/1e6 = $0.000225; total ~ **$0.0003**.
- 10 interrupts/day cap: 10 x $0.0003 = **~$0.003/day** (~$0.09/month).
- Prompts are far below the 100K tier, so the higher tier never applies [A11].
- **Variant with Haiku thinking disabled (proposal, not applied):** per A12, `thinking: {"type":"disabled"}` is allowed at effort high or below, and we use `low`, so it should be allowed (from the docs as recorded in A12; not re-fetched today, UNVERIFIED for this model call). Then output is ~150 visible tokens only: input 700 x $0.10/1e6 = $0.00007; output 150 x $0.50/1e6 = $0.000075; total ~ **$0.00015** per message; 10 x = **~$0.0015/day** (~$0.045/month). Old estimate above (~$0.0003, ~$0.003/day) kept for comparison; both are estimates until the cost log has real data.
- Decider: local, $0. Telegram, iCal: free. GitHub: free within limits.
- Real cost per day is read from the API `usage` fields (input_tokens, output_tokens, cache_*) [A9]; whether `output_tokens` includes thinking is stated on the thinking page [A12] but the Messages response schema page was not fully read: UNVERIFIED field-level detail.

---

## 7. Requirements traceability (draft)
| # | Requirement | Planned test |
|---|---|---|
| 1 | GitHub watcher | `test_github_source_detects_release_issue_pr_ci_failed_and_recovered_and_uses_etag`. Out of scope for v1: "review requested" (not observable on a repo Dev does not own; see GITHUB_TOKEN row) |
| 2 | Web-page watcher (static only) | `test_webpage_source_detects_text_change_and_rejects_private_ips_and_redirects` |
| 3 | iCal watcher | `test_ical_source_expands_recurring_events_with_timezones` |
| 4 | Telegram | `test_telegram_sends_message_with_buttons_and_long_polls` |
| 5 | Interrupt only when worth it | `test_policy_interrupts_above_threshold_within_daily_budget_else_digest` |
| 6 | Ask before acting | `test_no_write_request_without_allowlisted_callback` |
| 7 | Decider on laptop | MET through the Ollama MXFP8 build (not the official weights): SPIKE_REPORT clean PASS + fidelity PASS (max abs diff 0.0392). Test: `test_decider_client_local_only_and_digest_when_down` |
| 8 | Haiku writer + fallback, data minimisation | `test_writer_uses_haiku_then_template_on_error`; `test_writer_sends_only_type_title_time` |
| 9 | Python | `test_package_runs_on_supported_python` (pins the version from the spike) |
| 10 | Logged real cost/day | `test_costlog_records_usage_and_sums_per_day` |

## 8. Ask-before-acting design
- **Fixed allowlist in code** (not config, not LLM): `snooze_1h`, `remind_later`, `draft_reply` (text only, shown in Telegram, never sent), `rerun_failed_ci` (the ONLY GitHub write: POST `/repos/{o}/{r}/actions/runs/{id}/rerun-failed-jobs`, needs Actions:write [A8]).
- Separate write token, one repo; read token has no write rights.
- **Chat-ID allowlist**: callbacks from any other `from.id`/chat are ignored and logged.
- **1-hour expiry enforced by us**: Telegram docs state no expiry for inline buttons (searched; NOT STATED) [A5], so every button carries a created-at in our DB; after 1 h the tap is refused and `editMessageReplyMarkup` removes the keyboard.
- `callback_data` is 1-64 bytes [A4], so it holds only a short opaque token (e.g. 16 hex chars) that maps to a DB row; never action names from event text.
- **Idempotency key** per (event, action); second tap = "already done".
- **Every tap logged** (who, when, action, outcome).
- **Event text is UNTRUSTED** (issue titles, page text, invites). The LLM only writes message text; it cannot choose, create or trigger buttons or actions. Buttons come from code based on event kind.
- **Data minimisation**: only the event type, title and time go to Haiku. Never bodies, page text, attendee lists or descriptions. Test: `test_writer_sends_only_type_title_time` (requirement 8).
- **Required test**: `test_no_write_request_without_allowlisted_callback` (HTTP layer mocked; assert zero write calls for: no callback, wrong chat, expired, unknown token, replayed token, LLM output containing "rerun").

## 9. Evaluation protocol
Order (fixed): 1) write and FREEZE rules baseline -> 2) write rubric -> 3) label -> 4) fix split by seed -> 5) tune -> 6) test once.
- ~150 events, ~50 per source. State the synthetic share explicitly. Calendar events are synthetic or anonymised before anything is published.
- Split fixed by seed before any run: 50 tune / ~100 test (stratified by source).
- Tune split only: Decider question wording AND threshold. The threshold is tuned on the Ollama build only (it reads about 0.01 higher than MLX); never reuse an MLX-derived threshold. Test split: run once, no changes after.
- Metrics: precision, recall, F1, confusion matrix, interrupts/day, bootstrap 95% CIs, and example disagreements (Decider vs label vs baseline).
- Strands' own calibration/JevBench numbers (e.g. Brier 0.3419) are NOT reused as our results [A2b].

## 10. Day plan
| Day | Work |
|---|---|
| Phase 0 (done 23 Oct) | Plan + feasibility spike (Ollama MXFP8 chosen) |
| Build Day 1 (after Prateek's go-ahead) | Skeleton, Event schema, store, safe_fetch, GitHub source AND iCal source, with recorded fixtures and tests |
| Build Day 2 | Decider client + Ollama startup/health check; policy (threshold + daily budget); digest; rules baseline written and FROZEN before the rubric; rubric; web-page source; start labelling; measure one load-decide-unload cycle (time, and swap after unload) and add it to SPIKE_REPORT.md as an addendum |
| Build Day 3 | Held-out evaluation, Telegram, Haiku writer behind an interface, buttons + CI re-run, `--dry-run`. **Milestone: GitHub and calendar events reach a real Telegram message** |
| Build Day 4 | Live run, README with the traceability table, Pages, tag v0.1.0, LDS write-up |
| Build Day 5 | Buffer |

README limits to state: tested only on an M2 with 8 GB; with heavy apps open, each model load pushes memory to swap.

### Open decisions
a. **Who starts `ollama serve`**: Heads-Up itself (with `OLLAMA_MODELS`, `OLLAMA_HOST` and `OLLAMA_KEEP_ALIVE` set) or the user. Decide on Build Day 2.
b. **Eval persona**: the rubric labels events as if Dev maintains the watched repo; otherwise almost no GitHub event deserves an interrupt and positives are too few. State this persona in the rubric and the README.
c. **Daily interrupt budget value**: set on Build Day 2, before any tuning.

## 11. Risks
1. **8 GB RAM**: Decider peaks ~5.4 GB on MPS in Strands' own M3 Pro run [A2b]; swap was already 3.9 of 5.0 GB used at preflight. **Ollama MXFP8 (measured): peak footprint 3,332 MB; clean swap 0.0 -> 0.0 MB, but with normal apps open it still pushed ~2.0 GB to swap, hence the unload-after-batch policy (section 3).** **Measured (23 Oct 2026): official Decider on MLX peaked at 5040 MB phys footprint and FAILED the swap criterion (clean 0.0 -> 222.8 MB; normal 252.8 -> 2738.9 MB); warm p95 was 0.234 s / 0.252 s.** See spike/SPIKE_REPORT.md.
2. Python 3.14 (default `python3`) not used. `.venv` runs 3.12.14 with torch 2.14.1; the Decider installs and answers there. Pin 3.12 in pyproject.
3. RESOLVED: the `mlx` extra installs from PyPI 0.2.0 (docs are stale on that point).
4. MPS compiles per tensor shape, so p95 can reach ~2.6 s on a 36 GB machine [A2b]. Smoke tests here showed 24 s to 34 s per MPS request on this 8 GB Mac with swap in use (single requests, not measurements).
4b. RSS is misleading on Apple silicon (76 MB vs 4,215 MB footprint); decide on phys footprint, not RSS.
5. Prompt injection via event text -> mitigated by section 8.
6. Rate limits / Telegram 429: one message per second per chat [A5b].
7. Google secret iCal URL leaks -> env only, never logged, Reset if exposed [A13].
8. Haiku 5.5 may return `stop_reason: "refusal"`; must fall back to template [A17].
9. Small eval set -> wide CIs; report them honestly.
10. Decider HTTP server has no authentication [A2]: bind to 127.0.0.1 only.

## 11b. Fallback options if the spike FAILS (in this order; history: step d was taken and passed)
a. **MLX instead of MPS.** Already first in the spike; MLX is 1.4 to 1.6x faster and has a lower peak than MPS [A2b]. Still the official weights; requirement 7 holds.
b. **Shorter states / lower max length.** Truncate event text (title plus first N characters) before asking; latency and memory grow with token count (evaluation/results.md latency table). Requirement 7 holds.
c. **On-demand loading.** Load the model, decide the whole pending batch, unload; events arrive minutes apart, so the model need not stay in memory. Judge it with the measured cold-load time (cold load below ~1 min per batch is tolerable; measured MLX cold loads were 11.0 s (clean) and 17.3 s (normal); a full load-decide-unload cycle is not yet measured). Requirement 7 holds.
d. **Third-party quantized build.** Ollama lists MXFP8 about 2.0 GB, NVFP4 about 1.3 GB, BF16 about 3.8 GB [A2f]. Still runs on the laptop, so requirement 7 holds, BUT it is not the official weights, it needs Ollama installed outside this folder (needs Dev's approval first), and its accuracy must be re-checked on our tune split.
e. **Remote Decider, LAST.** **Breaks requirement 7** (Decider on the laptop). Only with Dev's explicit decision to change the requirement.

## 12. Secrets scan
`detect-secrets` 1.5.0 (pip, inside .venv; Apache Software License classifier on PyPI). Set up 24 Oct 2026: `.secrets.baseline` created and a git pre-commit hook installed (`detect-secrets-hook --baseline .secrets.baseline` on staged files; the hook calls `.venv/bin/detect-secrets-hook`, so it needs `.venv` to exist). Because the repo is fresh, scanning every commit covers the whole history. Also grep for the 6 env var names assigned to literals.

---

## Appendix A: Verification table
Checked on 2026-10-23. "Official" = vendor/standards body domain.

| ID | Claim | Value | Source URL | Official? |
|---|---|---|---|---|
| A1 | Install | `pip install strands-decider`; PyPI 0.2.0, 2026-10-19, Python>=3.10, Apache-2.0 | https://github.com/strands-labs/strands-decider ; https://pypi.org/pypi/strands-decider/json | Yes (repo owner) / PyPI |
| A1b | Devices | `--device cuda\|mps\|cpu\|mlx`; default picks cuda, mps, cpu; mlx only when asked | https://github.com/strands-labs/strands-decider/blob/main/docs/inference.md | Yes |
| A3 | MLX extra | README/docs say "ships with the next release" (stale). Tested 23 Oct 2026: `pip install "strands-decider[mlx]"` from PyPI 0.2.0 installed mlx 0.32.4 and mlx-lm 0.32.2 and `serve --device mlx` answered. No clone needed | docs/inference.md ; PyPI JSON ; own test | Yes |
| A2 | Serve | `strands-decider serve StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 --port 8000` | README, HF card https://huggingface.co/StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 | Yes |
| A2c | /v1/systemone | Request: `{"state": "...", "questions": {"is_urgent": {"type": "noul", "instructions": "..."}}}`; response: `answers.is_urgent.noul` = 0.8581, `usage`, `latency_ms`. (Summarised by fetch tool; exact field names re-checked in spike.) | README | Yes |
| A2d | Max context | Not stated as a limit; eval served at 4096-token window; Ollama page says 4K (third-party). **UNVERIFIED** | HF card, inference.md | Yes |
| A2e | "about 4.5 GB base download" | `Qwen/Qwen3.5-2B-Base` "(about 4.5 GB)". File `docs/inference.md`, section "## Model artifact" (line 134); also `evaluation/results.md`, section "## Summary" ("a download of about 4.5 GB", line 427). Measured on disk here: `.cache` 4.7 GB total. The Decider repo itself is about 0.2 GB (HF file list) | https://github.com/strands-labs/strands-decider/blob/main/docs/inference.md#model-artifact | Yes |
| A2f | Official quantized | None stated -> **no quantized test in spike**. Ollama lists MXFP8 ~2.0 GB, BF16 ~3.8 GB, NVFP4 ~1.3 GB (third-party; installed and tested 23 Oct 2026: Ollama 0.40.9, `strands-decider:2b-mxfp8`) | https://ollama.com/library/strands-decider | No (third-party) |
| A2b | Mac serving setup | M3 Pro 36 GB, macOS 26.6, torch 2.7.1, bf16 on MPS | `evaluation/results.md`, section "## Serving on a Mac: accuracy and latency" | Yes |
| A2b1 | 234 ms warm median (all JevBench tasks) | 234 ms | `docs/inference.md`, section "## Serving on a Mac" (lines 327-328); `evaluation/results.md`, "## Summary" (line 286) and the latency table in "## Serving on a Mac: accuracy and latency" | Yes |
| A2b2 | 2.6 s p95 | 2,628 ms (95th percentile), same places as A2b1 | `docs/inference.md` "## Serving on a Mac"; `evaluation/results.md` "## Summary" | Yes |
| A2b3 | 5.4 GB peak | "Peak memory on MPS is about 5.4 GB, most of it the 3.5 GiB of bf16 torso weights" (line 512); CPU 8.3 GB (line 502) | `evaluation/results.md`, section "## Serving on a Mac: accuracy and latency" | Yes |
| A2b4 | 153 ms median under 300 tokens | README: "153 ms / 234 ms" (warm median, under 300 tokens / all tasks, M3 Pro) in the table in section "## Performance" (line 333); also `docs/inference.md` "## Serving on a Mac" (line 327: 153 ms warm, 310 ms first request of its length) | https://github.com/strands-labs/strands-decider/blob/main/README.md | Yes |
| A2b5 | MLX speed and memory | 1.4 to 1.6x faster than MPS (README, "## How to start using strands-decider", line 54); MLX peak 3.8 to 4.9 GiB per request, load peak 3.7 GiB; MPS driver 4.2 to 6.3 GiB | `evaluation/results.md`, section "## Serving on a Mac through MLX: accuracy and latency" | Yes |
| A2g | Licence | Apache-2.0 (code + model card). Base Qwen3.5-2B-Base: apache-2.0 per Hugging Face model metadata (https://huggingface.co/api/models/Qwen/Qwen3.5-2B-Base; the model card page itself was not read) | HF card | Yes |
| A2h | Server auth | `serve` has no authentication; local experiments only | HF card | Yes |
| A4 | callback_data | "1-64 bytes" | https://core.telegram.org/bots/api#inlinekeyboardbutton | Yes |
| A4b | sendMessage text | "1-4096 characters after entities parsing" | https://core.telegram.org/bots/api#sendmessage | Yes |
| A4c | answerCallbackQuery | `callback_query_id`, `text` 0-200 chars, `cache_time`; clients show progress bar until called | https://core.telegram.org/bots/api#answercallbackquery | Yes |
| A4d | editMessageReplyMarkup | edits only reply markup; `chat_id`+`message_id` or `inline_message_id`; returns Message or True | https://core.telegram.org/bots/api#editmessagereplymarkup | Yes |
| A4e | getUpdates | long polling via `timeout`; update confirmed by offset > update_id; fails if webhook is set; two mutually exclusive ways to get updates | https://core.telegram.org/bots/api#getupdates | Yes |
| A5 | Button expiry | Not stated anywhere I read (parts of the 580K-char page, ~500K chars read). Treated as "no expiry" -> we enforce 1 h. **UNVERIFIED absence** | https://core.telegram.org/bots/api | Yes |
| A5b | Rate limits | 1 msg/s per chat; 20 msg/min in a group; ~30 msg/s broadcast. `retry_after` NOT STATED on the FAQ page | https://core.telegram.org/bots/faq | Yes |
| A6 | GitHub limits | Unauth 60/h; authenticated user 5,000/h; secondary: 100 concurrent, 900 points/min | https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api | Yes |
| A7 | Conditional requests | ETag + If-None-Match; 304 does not count against primary limit only if request is correctly authorized; honour `x-poll-interval` | https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api | Yes |
| A7b | Endpoints | `GET /repos/{o}/{r}/releases`; `/issues` (also returns PRs; `since` param); `/pulls` (has `requested_reviewers`, `requested_teams`; no dedicated review-request endpoint on that page); `/actions/runs` | docs.github.com/en/rest/{releases/releases, issues/issues, pulls/pulls, actions/workflow-runs} | Yes |
| A8 | Token permissions | rerun-failed-jobs: **Actions: write**; actions/runs: Actions: read; releases: Contents: read; issues: Issues: read; pulls + reviews: Pull requests: read | https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens | Yes |
| A8b | Rerun endpoint | `POST /repos/{owner}/{repo}/actions/runs/{run_id}/rerun-failed-jobs` | docs.github.com/en/rest/actions/workflow-runs | Yes |
| A9 | Usage fields | `input_tokens` (uncached), `cache_creation_input_tokens`, `cache_read_input_tokens`; total input = sum. `output_tokens` is used but not defined on the caching page; Messages response schema not fully read -> **UNVERIFIED** | https://platform.claude.com/docs/en/build-with-claude/prompt-caching | Yes |
| A10 | Haiku model id | `claude-haiku-5-5` (no date suffix, no alias); 1M context; 128K max output; released 7 Oct 2026 | https://platform.claude.com/docs/en/models/haiku-5-5/overview ; .../migration-guide | Yes |
| A11 | Haiku price | Prompts <=100K: input $0.10, output $0.50 per MTok (cache read $0.01). >100K: input $0.50, output $2.50. Tier is per request and counts cache tokens too | https://platform.claude.com/docs/en/about-claude/pricing | Yes |
| A12 | Thinking | Adaptive on by default; billed as output tokens; counts toward `max_tokens`. Lower effort via `output_config.effort` (low/medium/high/xhigh/max; default medium). `thinking: {"type":"disabled"}` allowed at high or below; 400 at xhigh/max | https://platform.claude.com/docs/en/build-with-claude/thinking ; .../effort | Yes |
| A12b | Omit params | Omit `temperature`, `top_p`, `top_k`; assistant prefill returns 400; thinking blocks default to empty text | .../models/haiku-5-5/overview ; migration-guide | Yes |
| A13 | Google iCal | Settings > calendar > Integrate calendar > "Secret address in iCal format"; "Do not share"; Reset if shared. Update-delay note NOT STATED | https://support.google.com/calendar/answer/37648 | Yes |
| A14 | RFC 5545 | Read only: TZID rules, floating vs UTC, FREQ required in RRULE. VEVENT required props / EXDATE / RECURRENCE-ID sections not read -> **UNVERIFIED** | https://datatracker.ietf.org/doc/html/rfc5545 | Yes |
| A15 | icalendar | 7.3.0, BSD-2-Clause, Python>=3.10 (release date UNVERIFIED) | https://pypi.org/pypi/icalendar/json ; https://github.com/collective/icalendar | PyPI/repo |
| A16 | recurring-ical-events | 3.8.2 (30 Apr 2026), LGPL-3.0-or-later, Python>=3.8 | https://pypi.org/project/recurring-ical-events/ | PyPI |
| A17 | Refusals | Haiku 5.5 can return `stop_reason: "refusal"`; no server-side fallback | .../models/haiku-5-5/migration-guide | Yes |
| A18 | Duet date/regions/plans | Research preview, 21 Oct 2026, by waitlist, US Pro and Max only, not Team/Enterprise | https://claude.com/resources/articles/claude-duet | Yes (Anthropic) |
| A19 | Duet waitlist | "Join the waitlist"; invitations gradual, by email | https://support.claude.com/en/articles/17631495-get-started-with-claude-duet | Yes (Anthropic) |
| A20 | HTTP validators | RFC 9110 sections 8.8.2/8.8.3/13.1.2/15.4.5 not read (page truncated) -> **UNVERIFIED**; general ETag use is confirmed via GitHub docs [A7] | https://www.rfc-editor.org/rfc/rfc9110.html | Yes |
| A21 | robots.txt | RFC 9309: 2xx follow rules; 4xx = may access anything; 5xx = assume complete disallow; cache <=24 h; case-insensitive UA match; longest match wins | https://www.rfc-editor.org/rfc/rfc9309.html | Yes |
| A22 | SSRF | Check resolved IPs; connect only to validated address (DNS rebinding); handle redirects (disable or re-validate); block localhost, RFC1918, fc00::/7, fe80::/10, 169.254.169.254 | https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html | OWASP (not a vendor, but standards-style) |

### SSRF-safe fetch design (from A22, A21)
1. Only `http`/`https`; no userinfo; ports 80/443 (or explicit allow).
2. Resolve DNS ourselves; reject if ANY address is private, loopback, link-local, multicast, reserved, unspecified, or IPv4-mapped IPv6 of those (`ipaddress` module checks).
3. Connect to the validated IP (keep Host/SNI) so DNS rebinding cannot swap it.
4. Redirects: follow manually, max 3, re-run steps 1-3 each hop.
5. Timeouts, response size cap, content-type check (text/html only for web watcher).
6. robots.txt per RFC 9309 (cache <=24 h; 5xx => skip). Send `If-None-Match`/`If-Modified-Since`; 304 = unchanged. Identify with a clear User-Agent. Poll no faster than every ~15 min.
