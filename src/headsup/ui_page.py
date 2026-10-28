"""The dashboard page. Everything that came from an event, a reason or the pipeline is escaped here with html.escape,
so no event text is ever interpreted as HTML. One inline script (CSP nonce): it refreshes the live region and sends the
button POSTs. No external script, stylesheet, font or image."""

from html import escape as _e

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="csrf" content="{token}">
<title>Heads-Up</title>
<style>
  :root {{ --bg:#fbfaf8; --fg:#1d1d1f; --muted:#5b5b61; --line:#d9d6cf; --card:#fff; --ok:#1a7f37; --bad:#b42318; --warn:#9a6700; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#17171a; --fg:#ececef; --muted:#a1a1a8; --line:#34343a; --card:#202024; --ok:#3fb950; --bad:#f97066; --warn:#e3b341; }} }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system, system-ui, sans-serif; }}
  main {{ max-width:60rem; margin:0 auto; padding:1rem; }}
  h1 {{ font-size:1.4rem; margin:.2rem 0 1rem; }} h2 {{ font-size:1rem; margin:0 0 .5rem; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:.8rem 1rem; margin:0 0 1rem; }}
  .row {{ display:flex; flex-wrap:wrap; gap:.5rem 1.5rem; }} .muted {{ color:var(--muted); }}
  .ok {{ color:var(--ok); font-weight:600; }} .bad {{ color:var(--bad); font-weight:600; }} .warn {{ color:var(--warn); font-weight:600; }}
  button, input {{ font:inherit; padding:.3rem .7rem; border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--fg); }}
  button {{ cursor:pointer; }} input {{ width:5rem; }}
  .table {{ overflow-x:auto; }} table {{ border-collapse:collapse; width:100%; font-size:.9rem; }}
  th, td {{ text-align:left; padding:.25rem .5rem; border-bottom:1px solid var(--line); vertical-align:top; }}
  pre {{ white-space:pre-wrap; margin:.5rem 0 0; font-size:.85rem; }} code {{ word-break:break-all; }}
</style>
</head>
<body>
<main>
<h1>Heads-Up</h1>
<div class="card">
  <h2>Controls</h2>
  <div class="row">
    <button data-action="check">Check now</button>
    <span id="pause-slot">{pause_button}</span>
    <button data-action="preview">Preview (dry run)</button>
    <label>Check every <input id="interval" type="number" min="1" max="1440" value="{interval}"> minutes</label>
    <button data-action="interval">Set</button>
  </div>
  <div id="message" class="muted" role="status"></div>
  <pre id="preview"></pre>
</div>
<div id="live">{fragment}</div>
<p class="muted">Local only (127.0.0.1). Settings are read from <code>.env</code> when the app starts; changing them needs a restart. Re-run CI is only available from the Telegram buttons.</p>
</main>
<script nonce="{nonce}">
const token = document.querySelector('meta[name=csrf]').content;
const msg = document.getElementById('message');
async function post(path, body) {{
  const r = await fetch(path, {{method:'POST', headers:{{'Content-Type':'application/json','X-CSRF-Token':token}}, body:JSON.stringify(body||{{}})}});
  let data = {{}}; try {{ data = await r.json(); }} catch (e) {{}}
  return [r.status, data];
}}
async function refresh() {{
  try {{ const r = await fetch('/fragment'); if (r.ok) {{ const d = await r.json(); document.getElementById('live').innerHTML = d.html; document.getElementById('pause-slot').innerHTML = d.pause_button; }} }} catch (e) {{}}
}}
document.addEventListener('click', async (ev) => {{
  const b = ev.target.closest('[data-action]'); if (!b) return;
  const a = b.dataset.action; msg.textContent = '';
  if (a === 'interval') {{ const [s, d] = await post('/api/interval', {{minutes: Number(document.getElementById('interval').value)}}); msg.textContent = d.message || ''; }}
  else if (a === 'preview') {{ msg.textContent = 'Running a dry run...'; const [s, d] = await post('/api/preview'); msg.textContent = d.message || ''; document.getElementById('preview').textContent = (d.lines || []).join('\\n'); }}
  else {{ const [s, d] = await post('/api/' + a); msg.textContent = d.message || ''; }}
  refresh();
}});
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def pause_button(paused):
    return ('<button data-action="resume">Resume automatic checks</button>' if paused
            else '<button data-action="pause">Pause automatic checks</button>')


def render_page(snapshot, token, nonce):
    return PAGE.format(token=_e(token), nonce=_e(nonce), interval=int(snapshot["status"]["interval_minutes"]),
                       pause_button=pause_button(snapshot["status"]["paused"]), fragment=render_fragment(snapshot))


def _cell(value):
    return "" if value is None else _e(str(value))


def _score(p):
    return "" if p is None else f"{p:.3f}"


def render_fragment(snap):
    s, h, today = snap["status"], snap["health"], snap["today"]
    out = []
    # status
    judge = '<span class="ok">up</span>' if h["up"] else '<span class="bad">down</span>'
    if h["up"] and h.get("started_by_us"):
        judge += ' <span class="muted">(started by Heads-Up)</span>'
    if h["up"]:
        model = {"present": '<span class="ok">installed</span>', "missing": '<span class="bad">missing</span>'}.get(h["model"], '<span class="warn">unknown</span>')
    else:
        model = "-"
    state = "paused" if s["paused"] else ("running now" if s["running"] else "automatic")
    last = "never" if s["last_check"] is None else _e(snap["fmt"](s["last_check"]))
    if s["last_error"]:
        last += f' <span class="bad">(error: {_e(s["last_error"])})</span>'
    nxt = "-" if s["next_check"] is None else _e(snap["fmt"](s["next_check"]))
    out.append('<div class="card"><h2>Status</h2><div class="row">'
               f'<span>Judge (Decider): {judge}</span><span>Model {_e(h["model_name"])}: {model}</span>'
               f'<span>Checks: {_e(state)}, every {int(s["interval_minutes"])} min</span>'
               f'<span>Last check: {last}</span><span>Next check: {nxt}</span>'
               f'<span>Tap listener: {_e(s["listener"])}</span></div>')
    if not h["up"]:
        out.append('<p class="warn">The Decider is not reachable, so events are only logged. Heads-Up tried to start Ollama; if it cannot, start it yourself.</p>')
    if h["up"] and h["model"] == "missing":
        out.append('<p class="warn">The model is not installed. Run this in a second terminal:</p>'
                   f'<pre>{_e(h["pull_command"])}</pre>')
    if not s["checks_enabled"]:
        out.append('<p class="warn">Telegram is not set up, so automatic checks are off. Only Preview works.</p>')
    out.append("</div>")
    # setup
    rows = "".join(f'<tr><td><code>{_e(n)}</code></td><td>{"<span class=ok>set</span>" if ok else "<span class=warn>missing</span>"}</td></tr>'
                   for n, ok in snap["setup"].items())
    out.append(f'<div class="card"><h2>Setup</h2><div class="table"><table><tr><th>Setting</th><th></th></tr>{rows}</table></div>'
               '<p class="muted">Names only. Some settings have defaults, so "missing" is fine for REMINDER_LEAD_MINUTES and the DECIDER_ ones.</p></div>')
    # today
    out.append('<div class="card"><h2>Today</h2><div class="row">'
               f'<span>Alerts: {int(today["alerts_today"])} of {int(today["limit"])}</span>'
               f'<span>Spend today: ${today["spend_today"]:.7f}</span><span>Spend in total: ${today["spend_total"]:.7f}</span></div></div>')
    # events
    ev = "".join(f'<tr><td>{_cell(snap["fmt"](e["time"]))}</td><td>{_cell(e["source"])}</td><td>{_cell(e["kind"])}</td>'
                 f'<td>{_cell(e["title"])}</td><td>{_score(e["probability"])}</td><td>{_cell(e["verdict"])}</td><td>{_cell(e["reason"])}</td></tr>'
                 for e in snap["events"])
    out.append('<div class="card"><h2>Recent events (last 50)</h2><div class="table"><table>'
               '<tr><th>Time</th><th>Source</th><th>Kind</th><th>Title</th><th>Score</th><th>Decision</th><th>Reason</th></tr>'
               f'{ev}</table></div></div>')
    # alerts
    al = "".join(f'<tr><td>{_cell(snap["fmt"](a["sent_at"]) if a["sent_at"] else "")}</td><td>{_cell(a["kind"])}</td><td>{_cell(a["title"])}</td>'
                 f'<td>{_cell(a["state"])}</td></tr>' for a in snap["alerts"])
    out.append('<div class="card"><h2>Alerts sent and their buttons</h2><div class="table"><table>'
               f'<tr><th>Sent</th><th>Kind</th><th>Title</th><th>Buttons</th></tr>{al}</table></div></div>')
    return "".join(out)
