"""The dashboard page. Everything that came from an event, a reason or the pipeline is escaped here with html.escape,
so no event text is ever interpreted as HTML. One inline script (CSP nonce): it refreshes the live region, shows relative
times and sends the button POSTs. No external script, stylesheet, font or image. Light theme only.
"""

from datetime import datetime, timezone
from html import escape as _e

from headsup.config import CHOSEN_THRESHOLD
from headsup.localtime import LOCAL_TZ

SOURCES = {"github": "GitHub", "ical": "Calendar"}
CALENDAR_NAMES = ("ical", "calendar", "caldav", "google_calendar")  # however the source is stored, the chip says "Calendar"


def source_key(src):
    s = (src or "").strip().lower()
    return "ical" if s in CALENDAR_NAMES else s

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="csrf" content="@@TOKEN@@">
<title>Heads-Up</title>
<style>
  :root {
    --indigo:#4f46e5; --violet:#8b5cf6; --teal:#14b8a6; --amber:#f59e0b; --sky:#0ea5e9; --green:#22c55e; --rose:#f43f5e;
    --fg:#0f172a; --muted:#64748b; --line:#e2e8f0; --card:#ffffff; --accent:#4f46e5;
    --grad:linear-gradient(135deg,#4f46e5,#7c3aed);  /* text-bearing gradient: the darker violet keeps white text at AA contrast */
    --grad-soft:linear-gradient(135deg,#4f46e5,#8b5cf6);
    --radius:16px; --s1:8px; --s2:16px; --s3:24px; --s4:32px;
    --shadow:0 1px 2px rgba(15,23,42,.05), 0 4px 12px rgba(15,23,42,.05); --shadow-up:0 2px 4px rgba(15,23,42,.06), 0 10px 24px rgba(79,70,229,.10);
    --t-xs:.75rem; --t-sm:.85rem; --t-md:.95rem; --t-lg:1.15rem; --t-num:1.875rem; --t-h1:1.625rem;
    --ease:150ms ease;
    --cols:130px 150px minmax(0,1fr) 190px 150px; --gap:12px;  /* ONE grid template for the header row and every event card */
    --pad-l:36px; --pad-r:16px;  /* left padding holds the status dot; the header adds the card's 3px left and 1px right border */
  }
  * { box-sizing:border-box; }
  html { background:#ffffff; }
  body { margin:0; min-height:100vh; color:var(--fg); background:linear-gradient(180deg,#f5f3ff 0%,#ffffff 50%,#ecfeff 100%) fixed;
         font:var(--t-md)/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, Helvetica, Arial, sans-serif; }
  main { max-width:1100px; margin:0 auto; padding:var(--s3) var(--s2) var(--s4); }
  header { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:var(--s1) var(--s3); margin:0 0 var(--s3); }
  .brand { display:flex; align-items:center; gap:12px; }
  .logo { flex:none; width:36px; height:36px; border-radius:50%; background:var(--grad-soft); display:inline-flex; align-items:center; justify-content:center; color:#fff; box-shadow:0 2px 8px rgba(79,70,229,.35); }
  .logo svg { width:20px; height:20px; }
  h1 { font-size:var(--t-h1); font-weight:600; line-height:1.2; margin:0; letter-spacing:-.01em; }
  .tagline { margin:2px 0 0; color:var(--muted); font-size:var(--t-sm); }
  h2 { font-size:var(--t-xs); font-weight:600; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); margin:0 0 12px; }
  .pills { display:flex; flex-wrap:wrap; gap:var(--s1); }
  .pill { display:inline-flex; align-items:center; gap:8px; padding:4px 12px; border-radius:999px; font-size:var(--t-sm); font-weight:600; }
  .pill::before { content:""; width:8px; height:8px; border-radius:50%; background:var(--dot); }
  .pill.ok { --dot:var(--green); color:#166534; background:#dcfce7; }
  .pill.warn { --dot:var(--amber); color:#92400e; background:#fef3c7; }
  .pill.bad { --dot:var(--rose); color:#9f1239; background:#ffe4e6; }
  .pill.grey { --dot:#94a3b8; color:#334155; background:#e2e8f0; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); padding:20px 24px; margin:0 0 var(--s3); }
  .summary { display:grid; grid-template-columns:repeat(auto-fit, minmax(15rem, 1fr)); gap:var(--s3); margin:0 0 var(--s3); }
  .summary .card { margin:0; display:flex; gap:16px; align-items:flex-start; }
  .summary .body { min-width:0; flex:1; }
  .tile { flex:none; width:40px; height:40px; border-radius:12px; display:inline-flex; align-items:center; justify-content:center; color:var(--c); background:var(--bgc); }
  .tile svg { width:22px; height:22px; }
  .tile.violet { --c:#6d28d9; --bgc:#ede9fe; } .tile.teal { --c:#0f766e; --bgc:#ccfbf1; } .tile.amber { --c:#b45309; --bgc:#fef3c7; }
  .big { font-size:var(--t-num); font-weight:650; line-height:1.15; margin:0; font-variant-numeric:tabular-nums; letter-spacing:-.01em; }
  .sub { color:var(--muted); font-size:var(--t-sm); margin:4px 0 0; }
  .bar { height:8px; background:#ede9fe; border-radius:999px; overflow:hidden; margin:12px 0 0; }
  .bar > span { display:block; height:100%; background:linear-gradient(90deg,#8b5cf6,#6d28d9); border-radius:999px; transition:width var(--ease); }
  .bar.full > span { background:linear-gradient(90deg,#f59e0b,#d97706); }
  .toolbar { display:flex; flex-wrap:nowrap; align-items:center; gap:12px; }
  .toolbar .spacer { flex:1 1 auto; }
  .toolbar .group { display:inline-flex; align-items:center; gap:8px; flex:none; white-space:nowrap; color:var(--muted); font-size:var(--t-sm); }
  #pause-slot { display:inline-flex; }
  button, input { font:inherit; }
  button { padding:8px 16px; border-radius:10px; border:1px solid transparent; background:#fff; color:var(--fg); font-weight:600; font-size:var(--t-md); cursor:pointer; white-space:nowrap; transition:background var(--ease), border-color var(--ease), box-shadow var(--ease), transform var(--ease), filter var(--ease); }
  button.primary { background:var(--grad); color:#fff; box-shadow:0 1px 2px rgba(79,70,229,.3), 0 4px 10px rgba(79,70,229,.2); }
  button.primary:hover:not(:disabled) { filter:brightness(1.08); transform:translateY(-1px); }
  button.secondary { background:#eef2ff; color:#3730a3; border-color:#c7d2fe; }
  button.secondary:hover:not(:disabled) { background:#e0e7ff; }
  button.plain { background:#f1f5f9; color:#334155; border-color:var(--line); }
  button.plain:hover:not(:disabled) { background:#e2e8f0; }
  button:focus-visible, input:focus-visible { outline:3px solid #a5b4fc; outline-offset:2px; }
  button:disabled { opacity:.55; cursor:not-allowed; }
  input[type=number] { width:4.25rem; padding:8px; border:1px solid #cbd5e1; border-radius:10px; background:#fff; color:var(--fg); text-align:center; font-variant-numeric:tabular-nums; }
  #message:empty { margin:0; }
  #message { margin:12px 0 0; color:var(--muted); font-size:var(--t-sm); }
  pre { white-space:pre-wrap; margin:8px 0 0; font-size:.82rem; background:#f8fafc; border:1px solid var(--line); border-radius:10px; padding:8px 12px; }
  pre:empty { display:none; }
  code { font:.85em ui-monospace, SFMono-Regular, Menlo, monospace; word-break:break-all; }
  .notice { border-radius:12px; padding:8px 16px; margin:0 0 var(--s2); font-size:var(--t-sm); background:#fef3c7; color:#92400e; }
  .tabs { display:flex; gap:var(--s1); margin:0 0 var(--s2); flex-wrap:wrap; }
  .tabs button { padding:4px 16px; border-radius:999px; font-size:var(--t-sm); background:#fff; color:var(--muted); border-color:var(--line); }
  .tabs button[aria-pressed=true] { background:#eef2ff; border-color:#a5b4fc; color:#3730a3; }
  .tabs button:hover { background:#f1f5f9; }
  .tabs button[aria-pressed=true]:hover { background:#e0e7ff; }
  .group-h { font-variant-caps:all-small-caps; letter-spacing:.08em; font-weight:700; color:var(--muted); font-size:var(--t-md); margin:var(--s2) 0 var(--s1); }
  .evs > div[data-group]:first-child .group-h { margin-top:0; }
  .ev-head { display:grid; grid-template-columns:var(--cols); gap:var(--gap); padding:0 calc(var(--pad-r) + 1px) 0 calc(var(--pad-l) + 3px); margin:0 0 var(--s1); font-size:var(--t-xs); font-weight:600; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }
  .ev-head > span { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0; }
  .ev-head .hint2 { font-weight:400; text-transform:none; letter-spacing:0; opacity:.9; }
  .ev { position:relative; display:grid; grid-template-columns:var(--cols); align-items:center; gap:var(--gap); background:#fff; border:1px solid var(--line); border-left:3px solid var(--line); border-radius:12px; box-shadow:var(--shadow); padding:12px var(--pad-r) 12px var(--pad-l); margin:0 0 var(--s1); transition:transform var(--ease), box-shadow var(--ease); }
  .ev:hover { transform:translateY(-2px); box-shadow:var(--shadow-up); }
  .ev.alert { border-left:3px solid var(--accent); }
  .dotc { position:absolute; left:14px; top:50%; margin-top:-5px; width:10px; height:10px; border-radius:50%; background:#94a3b8; }
  .dotc.interrupt { background:var(--indigo); } .dotc.limit { background:var(--amber); } .dotc.repeat { background:var(--sky); } .dotc.fixed { background:var(--green); }
  .when { color:var(--muted); font-size:var(--t-sm); white-space:nowrap; }
  .title { overflow-wrap:anywhere; font-weight:500; }
  .why { color:var(--muted); font-size:var(--t-xs); margin-top:2px; cursor:help; overflow-wrap:anywhere; }
  .dec { min-width:0; }
  .label { max-width:100%; display:inline-flex; align-items:center; gap:6px; padding:2px 10px; border-radius:999px; font-size:var(--t-xs); font-weight:600; background:#e2e8f0; color:#334155; }
  .label svg { width:14px; height:14px; flex:none; }
  .label.github { background:#e2e8f0; color:#334155; } .label.ical { background:#ccfbf1; color:#115e59; }
  .badge { display:inline-block; max-width:100%; overflow:hidden; text-overflow:ellipsis; vertical-align:middle; padding:2px 12px; border-radius:999px; font-size:var(--t-xs); font-weight:600; white-space:nowrap; background:#e2e8f0; color:#334155; }
  .badge.interrupt { background:var(--grad); color:#fff; }
  .badge.logged { background:#e2e8f0; color:#334155; }
  .badge.limit, .badge.pending { background:#fef3c7; color:#92400e; }
  .badge.repeat { background:#e0f2fe; color:#075985; }
  .badge.fixed, .badge.tapped { background:#dcfce7; color:#166534; }
  .badge.expired { background:#e2e8f0; color:#334155; }
  .score { display:flex; align-items:center; gap:8px; white-space:nowrap; }
  .track { position:relative; width:5.5rem; height:8px; background:#e2e8f0; border-radius:999px; margin:0; }
  .track > .fill { position:absolute; left:0; top:0; bottom:0; background:#94a3b8; border-radius:999px; }
  .track > .fill.pass { background:linear-gradient(90deg,#8b5cf6,#6d28d9); }
  .track > .mark { position:absolute; top:-4px; bottom:-4px; width:2px; background:var(--fg); border-radius:1px; }
  .num { font-variant-numeric:tabular-nums; color:var(--muted); font-size:var(--t-sm); }
  .checklist { list-style:none; margin:0; padding:0; display:grid; grid-template-columns:repeat(auto-fill, minmax(17rem, 1fr)); gap:var(--s1) var(--s3); }
  .checklist li { display:flex; align-items:center; gap:12px; }
  .dot { flex:none; width:20px; height:20px; border-radius:50%; display:inline-flex; align-items:center; justify-content:center; font-size:var(--t-xs); font-weight:700; }
  .dot.set { background:var(--green); color:#fff; }
  .dot.missing { border:2px solid #cbd5e1; }
  .hint { color:var(--muted); font-size:var(--t-sm); margin:var(--s2) 0 0; }
  .empty { color:var(--muted); padding:var(--s1) 0; }
  .alerts { list-style:none; margin:0; padding:0; }
  .alerts li { display:flex; flex-wrap:wrap; align-items:center; gap:4px 16px; padding:12px 0; border-bottom:1px solid var(--line); }
  .alerts li:last-child { border-bottom:0; }
  .alerts .title { flex:1 1 12rem; }
  footer { color:var(--muted); font-size:var(--t-sm); }
  .settings-link { font-weight:600; font-size:var(--t-sm); color:#3730a3; text-decoration:none; }
  .settings-link:hover { text-decoration:underline; }
  .sr { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
  @media (max-width:60rem) { :root { --cols:100px 110px minmax(0,1fr) 130px 110px; --gap:8px; } }
  @media (max-width:43.7rem) { .ev-head { display:none; } .ev { grid-template-columns:minmax(0,1fr); gap:6px; } .toolbar { flex-wrap:wrap; } .toolbar .spacer { display:none; } .toolbar .group { width:100%; } main { padding:var(--s2) 12px var(--s3); } .card { padding:16px; } }
  @media (prefers-reduced-motion: reduce) { * { transition:none !important; animation:none !important; } .ev:hover, button.primary:hover:not(:disabled) { transform:none; } }
</style>
</head>
<body>
<main>
<header>
  <div class="brand">
    <span class="logo" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg></span>
    <div><h1>Heads-Up</h1><p class="tagline">A proactive assistant that interrupts you only when it matters.</p></div>
  </div>
  <div class="pills" id="pills">@@PILLS@@</div>
  <a href="/setup" id="settings-link" class="settings-link">Settings</a>
</header>
<div class="card">
  <div class="toolbar">
    <button id="check-btn" class="primary" data-action="check">Check now</button>
    <button id="preview-btn" class="secondary" data-action="preview">Preview (dry run)</button>
    <span id="pause-slot">@@PAUSE@@</span>
    <span class="spacer"></span>
    <span class="group"><label for="interval">Every</label><input id="interval" type="number" min="1" max="1440" value="@@INTERVAL@@"><span>min</span>
      <button class="plain" data-action="interval">Set</button></span>
  </div>
  <div id="message" role="status" aria-live="polite"></div>
  <pre id="preview"></pre>
</div>
<div id="live">@@FRAGMENT@@</div>
<footer>Local only (127.0.0.1). Change settings on the <a href="/setup">Settings page</a>: saving there takes effect at once. A hand edit of <code>.env</code> needs a restart. Re-run CI is only available from the Telegram buttons.</footer>
</main>
<script nonce="@@NONCE@@">
const token = document.querySelector('meta[name=csrf]').content;
const msg = document.getElementById('message');
const checkBtn = document.getElementById('check-btn');
const previewBtn = document.getElementById('preview-btn');
async function post(path, body) {
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':token}, body:JSON.stringify(body||{})});
  let data = {}; try { data = await r.json(); } catch (e) {}
  return [r.status, data];
}
function rel(d) {
  const diff = Math.round((Date.now() - d) / 1000), s = Math.abs(diff), future = diff < 0;
  if (s < 45) return future ? 'in under a minute' : 'just now';
  const m = Math.round(s / 60); if (m < 60) return future ? 'in ' + m + ' min' : m + ' min ago';
  const h = Math.round(m / 60); if (h < 24) return future ? 'in ' + h + ' h' : h + ' h ago';
  const days = Math.round(h / 24); return future ? 'in ' + days + ' d' : days + ' d ago';
}
let filter = 'all';
function applyFilter() {
  document.querySelectorAll('#live [data-filter]').forEach(b => b.setAttribute('aria-pressed', b.dataset.filter === filter ? 'true' : 'false'));
  document.querySelectorAll('#live div[data-group]').forEach(g => {
    let shown = 0;
    g.querySelectorAll('.ev').forEach(r => { const ok = filter === 'all' || r.dataset.kind === filter; r.hidden = !ok; if (ok) shown++; });
    g.hidden = shown === 0;
  });
}
function relTimes() {
  document.querySelectorAll('time[data-rel]').forEach(t => { const d = new Date(t.dateTime); if (!isNaN(d)) t.textContent = rel(d); });
}
// A click on Check now queues a pass; the worker starts it within a couple of seconds. Until the page sees it start (or finish, which
// changes the last-check time) the button keeps saying so, and the page asks for news every second instead of every five.
let queuedAt = 0, lastSeen = null, running = false;
function syncState() {
  const st = document.querySelector('#live [data-running]');
  running = !!st && st.dataset.running === '1';
  const enabled = !st || st.dataset.enabled === '1', last = st ? st.dataset.last : null;
  if (queuedAt && (running || (lastSeen !== null && last !== lastSeen) || Date.now() - queuedAt > 20000)) queuedAt = 0;
  lastSeen = last;
  const busy = running || queuedAt > 0;
  checkBtn.textContent = busy ? 'Checking...' : 'Check now';
  checkBtn.disabled = busy || !enabled;
  previewBtn.disabled = running;
  const src = document.querySelector('#live [data-pills]');
  if (src) document.getElementById('pills').innerHTML = src.innerHTML;
  relTimes();
  applyFilter();
}
async function refresh() {
  try { const r = await fetch('/fragment'); if (r.ok) { const d = await r.json(); document.getElementById('live').innerHTML = d.html; document.getElementById('pause-slot').innerHTML = d.pause_button; syncState(); } } catch (e) {}
}
document.addEventListener('click', async (ev) => {
  const f = ev.target.closest('[data-filter]');
  if (f) { filter = f.dataset.filter; applyFilter(); return; }
  const b = ev.target.closest('[data-action]'); if (!b || b.disabled) return;
  const a = b.dataset.action; msg.textContent = '';
  if (a === 'interval') { const [s, d] = await post('/api/interval', {minutes: Number(document.getElementById('interval').value)}); msg.textContent = d.message || ''; }
  else if (a === 'preview') { msg.textContent = 'Running a dry run...'; previewBtn.disabled = true; const [s, d] = await post('/api/preview'); msg.textContent = d.message || ''; document.getElementById('preview').textContent = (d.lines || []).join('\\n'); }
  else { const [s, d] = await post('/api/' + a); msg.textContent = d.message || ''; if (a === 'check' && s === 202) { queuedAt = Date.now(); checkBtn.textContent = 'Checking...'; checkBtn.disabled = true; } }
  refresh();
});
syncState();
(function poll() { refresh().finally(() => setTimeout(poll, running || queuedAt ? 1000 : 5000)); })();
setInterval(relTimes, 30000);
</script>
</body>
</html>
"""


def pause_button(paused):
    return ('<button class="secondary" data-action="resume">Resume automatic checks</button>' if paused
            else '<button class="secondary" data-action="pause">Pause automatic checks</button>')


ICONS = {
    "github": '<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>',
    "ical": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="2" y="3" width="12" height="11" rx="2"/>'
            '<path d="M2 7h12M5.5 1.5v3M10.5 1.5v3"/></svg>',
}
TILES = {
    "alerts": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg>',
    "clock": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    "spend": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M14.8 9.2c-.5-.8-1.5-1.2-2.8-1.2-1.6 0-2.7.8-2.7 2 0 3 5.6 1.4 5.6 4.2 0 1.2-1.2 2-2.9 2-1.4 0-2.5-.5-3-1.4M12 6v2M12 16v2"/></svg>',
}


def _cell(value):
    return "" if value is None else _e(str(value))


def _local(iso_or_dt):
    dt = iso_or_dt if isinstance(iso_or_dt, datetime) else datetime.fromisoformat(iso_or_dt)
    return dt.astimezone(LOCAL_TZ)


def _rel(dt, now):
    """'5 min ago' / 'in 12 min' (the script keeps these fresh between refreshes)."""
    diff = (now - dt).total_seconds()
    s, future = abs(diff), diff < 0
    if s < 45:
        return "in under a minute" if future else "just now"
    m = round(s / 60)
    if m < 60:
        return f"in {m} min" if future else f"{m} min ago"
    h = round(m / 60)
    if h < 24:
        return f"in {h} h" if future else f"{h} h ago"
    d = round(h / 24)
    return f"in {d} d" if future else f"{d} d ago"


def _clock(iso, now):
    """The local clock time, with the date only when it is not today."""
    dt, today = _local(iso), _local(now).date()
    return dt.strftime("%H:%M") if dt.date() == today else dt.strftime("%d %b %H:%M")


def _time(iso, snap, relative=True):
    """A <time> with the full local date and time on hover. relative=True reads 'N min ago' (the script keeps it fresh);
    otherwise the local clock time, e.g. '13:13', which stays as it is."""
    if not iso:
        return ""
    full = snap["fmt"](iso)
    if not relative:
        return f'<time datetime="{_e(iso)}" title="{_e(full)}">{_e(_local(iso).strftime("%H:%M"))}</time>'
    text = _rel(datetime.fromisoformat(iso), snap["now"])
    return f'<time datetime="{_e(iso)}" title="{_e(full)}" data-rel>{_e(text)}</time>'


def _pills(snap):
    s, h = snap["status"], snap["health"]
    if not s["checks_enabled"]:
        run = '<span class="pill grey">Checks off</span>'
    elif s["paused"]:
        run = '<span class="pill warn">Paused</span>'
    else:
        run = '<span class="pill ok">Running</span>'
    if not h["up"]:
        judge = '<span class="pill bad" title="Judge (Decider): down">Judge down</span>'
    elif h["model"] == "present":
        judge = '<span class="pill ok" title="Judge (Decider): up">Judge up</span>'
    else:
        word = "model missing" if h["model"] == "missing" else "model unknown"
        judge = f'<span class="pill warn" title="Judge (Decider): up, {word}">Judge up, {word}</span>'
    return run + judge


def human_reason(verdict, reason):
    """A short label for the technical reason (which stays available as a tooltip)."""
    reason = reason or ""
    if verdict == "interrupt":
        return "Above the alert bar"
    if reason.startswith("daily limit of"):
        return "Daily limit reached"
    if reason.startswith(("already alerted", "a newer failure")):
        return "Repeat of an earlier alert"
    if reason.startswith("already recovered"):
        return "Already fixed"
    if reason.startswith("probability"):
        return "Below the alert bar"
    if reason.startswith("decider unavailable"):
        return "Judge was down"
    if reason.startswith("snoozed"):
        return "Snoozed"
    if reason.startswith("first run"):
        return "Already there at setup"
    return "Logged"


def decision_kind(e):
    """The chip class for a decision: interrupt, limit, repeat, fixed or logged."""
    if e["verdict"] == "interrupt":
        return "interrupt"
    reason = e["reason"] or ""
    if reason.startswith("daily limit of"):
        return "limit"
    if reason.startswith(("already alerted", "a newer failure")):
        return "repeat"
    if reason.startswith("already recovered"):
        return "fixed"
    return "logged"


CHIPS = {"interrupt": "Interrupt", "limit": "Limit reached", "repeat": "Repeat", "fixed": "Already fixed", "logged": "Logged"}


TIPS = {"repeat": "Repeat of an earlier alert"}  # the full text of a shortened chip


def _decision(e):
    kind = decision_kind(e)
    tip = f' title="{_e(TIPS[kind])}"' if kind in TIPS else ""
    return f'<span class="badge {kind}"{tip}>{CHIPS[kind]}</span>'


def _score_bar(p, reason):
    if p is None:
        return f'<span class="num" title="{_e(reason or "no score")}">&mdash;</span>'
    pct = max(0.0, min(1.0, p)) * 100
    cls = "fill pass" if p >= CHOSEN_THRESHOLD else "fill"
    return (f'<div class="score"><div class="track" role="img" aria-label="score {p:.3f}, threshold {CHOSEN_THRESHOLD:.2f}">'
            f'<span class="{cls}" style="width:{pct:.1f}%"></span>'
            f'<span class="mark" style="left:{CHOSEN_THRESHOLD * 100:.1f}%" title="threshold {CHOSEN_THRESHOLD:.2f}"></span></div>'
            f'<span class="num">{p:.2f}</span></div>')


def _alert_badge(state):
    if state == "pending":
        return '<span class="badge pending">Pending</span>'
    if state.startswith("tapped"):
        action = state.partition(": ")[2].replace("_", " ")
        return f'<span class="badge tapped">Tapped: {_e(action)}</span>'
    if state == "expired":
        return '<span class="badge expired">Expired</span>'
    return '<span class="badge interrupt">Buttons active</span>'


def _money(x):
    return "$0.00" if x == 0 else ("< $0.01" if x < 0.01 else f"${x:.2f}")


def _group(iso, now):
    days = (_local(now).date() - _local(iso).date()).days
    return "Today" if days <= 0 else ("Yesterday" if days == 1 else "Earlier")


def render_page(snapshot, token, nonce):
    status = snapshot["status"]
    return (PAGE.replace("@@TOKEN@@", _e(token)).replace("@@NONCE@@", _e(nonce))
            .replace("@@INTERVAL@@", str(int(status["interval_minutes"]))).replace("@@PAUSE@@", pause_button(status["paused"]))
            .replace("@@PILLS@@", _pills(snapshot)).replace("@@FRAGMENT@@", render_fragment(snapshot)))


def _event_row(e, snap):
    alert = e["verdict"] == "interrupt"
    src = e["source"]
    reason = e["reason"] or ""
    kind = decision_kind(e)
    skey = source_key(src)
    relative = _group(e["time"], snap["now"]) == "Today"
    return (f'<div class="ev{" alert" if alert else ""}" role="listitem" data-kind="{"alerts" if alert else "logged"}">'
            f'<span class="dotc {kind}" aria-hidden="true"></span>'
            f'<div class="when">{_time(e["time"], snap, relative)}</div>'
            f'<div class="src"><span class="label {_e(skey or "other")}">{ICONS.get(skey, "")}{_e(SOURCES.get(skey, src or "?"))}</span></div>'
            f'<div><div class="title">{_cell(e["title"])}</div>'
            f'<div class="why" title="{_e(reason)}">{_e(human_reason(e["verdict"], reason))}</div></div>'
            f'<div class="scorecell">{_score_bar(e["probability"], reason)}</div><div class="dec">{_decision(e)}</div></div>')


def render_fragment(snap):
    s, h, today = snap["status"], snap["health"], snap["today"]
    fmt, now = snap["fmt"], snap["now"]
    out = [f'<div hidden data-running="{1 if s["running"] else 0}" data-enabled="{1 if s["checks_enabled"] else 0}" data-last="{_e(str(s["last_check"] or ""))}"></div>',
           f'<div hidden data-pills>{_pills(snap)}</div>']
    if snap.get("setup_missing"):  # first run, or something was cleared: say what is missing and where to fix it
        out.append(f'<div class="notice">Setup needed: {_e(", ".join(snap["setup_missing"]))}. <a href="/setup">Open the Settings page</a>.</div>')

    # notices (the judge and Telegram)
    if not h["up"]:
        out.append('<div class="notice">The Decider is not reachable, so events are only logged. Heads-Up tried to start Ollama; if it cannot, start it yourself.</div>')
    if h["up"] and h["model"] == "missing":
        out.append(f'<div class="notice">The model {_e(h["model_name"])} is not installed. Run this in a second terminal:'
                   f'<pre>{_e(h["pull_command"])}</pre></div>')
    if not s["checks_enabled"]:
        out.append('<div class="notice">Telegram is not set up, so automatic checks are off. Only Preview works.</div>')

    # summary cards
    limit, n = int(today["limit"]), int(today["alerts_today"])
    pct = 100 if limit <= 0 else min(100, n * 100 // limit)
    every = f"every {int(s['interval_minutes'])} min"
    if s["last_check"] is None:
        last = "Last check: never"
    else:
        last = f"Last check: {_time(s['last_check'], snap)}"
    if s["last_error"]:
        last += f" (error: {_e(s['last_error'])})"
    if s["running"]:
        big, sub = "Checking now", every
    elif not s["checks_enabled"]:
        big, sub = "Off", "Telegram is not set up"
    elif s["paused"]:
        big, sub = "Paused", f"{every} when resumed"
    else:
        big = _time(s["next_check"], snap)
        sub = f"at {_e(_clock(s['next_check'], now))}, {every}"
    exact = f"${today['spend_today']:.7f} today, ${today['spend_total']:.7f} in total"
    calls = int(snap["calls_today"])
    out.append('<section class="summary">'
               f'<div class="card"><span class="tile violet">{TILES["alerts"]}</span><div class="body"><h2>Alerts today</h2><p class="big">{n} of {limit}</p>'
               f'<div class="bar{" full" if n >= limit else ""}" role="progressbar" aria-label="Alerts: {n} of {limit}" aria-valuemin="0" '
               f'aria-valuemax="{limit}" aria-valuenow="{n}"><span style="width:{pct}%"></span></div></div></div>'
               f'<div class="card"><span class="tile teal">{TILES["clock"]}</span><div class="body"><h2>Next check</h2><p class="big">{big}</p><p class="sub">{sub}</p><p class="sub">{last}</p></div></div>'
               f'<div class="card" title="{_e(exact)}"><span class="tile amber">{TILES["spend"]}</span><div class="body"><h2>Spend</h2><p class="big">{_e(_money(today["spend_today"]))}</p>'
               f'<p class="sub">{calls} Haiku call{"" if calls == 1 else "s"} today</p>'
               f'<p class="sub">{_e(_money(today["spend_total"]))} in total</p></div></div></section>')

    # recent events, grouped by local day, with filter tabs
    if snap["events"]:
        groups = {"Today": [], "Yesterday": [], "Earlier": []}
        for e in snap["events"]:
            groups[_group(e["time"], now)].append(e)
        bodies = "".join(f'<div data-group><div class="group-h">{name}</div>' + "".join(_event_row(e, snap) for e in rows) + "</div>"
                         for name, rows in groups.items() if rows)
        tabs = ('<div class="tabs" role="group" aria-label="Filter events">'
                '<button data-filter="all" aria-pressed="true">All</button><button data-filter="alerts" aria-pressed="false">Alerts</button>'
                '<button data-filter="logged" aria-pressed="false">Logged</button></div>')
        head = ('<div class="ev-head" aria-hidden="true"><span>When</span><span>Source</span><span>Event</span>'
                f'<span>Score <span class="hint2">&middot; bar {CHOSEN_THRESHOLD:.2f}</span></span><span>Decision</span></div>')
        body = tabs + head + f'<div class="evs" role="list">{bodies}</div>'
    else:
        when = "" if s["next_check"] is None else f" the next check runs at {_e(_clock(s['next_check'], now))}"
        body = f'<div class="empty">No events yet:{when or " checks are not scheduled"}.</div>'
    out.append(f'<div class="card"><h2>Recent events (last 50)</h2>{body}</div>')

    # setup checklist
    items = "".join(f'<li><span class="dot {"set" if ok else "missing"}" aria-hidden="true">{"&#10003;" if ok else ""}</span>'
                    f'<code>{_e(name)}</code><span class="sr">{"set" if ok else "missing"}</span></li>' for name, ok in snap["setup"].items())
    out.append(f'<div class="card"><h2>Setup</h2><ul class="checklist">{items}</ul>'
               '<p class="hint">Names only. A tick means set, an empty circle means missing. Some settings have defaults, so missing is fine for '
               'REMINDER_LEAD_MINUTES and the DECIDER_ ones.</p></div>')

    # alerts
    if snap["alerts"]:
        rows = "".join(f'<li><span class="num">{_time(a["sent_at"], snap)}</span><span class="label">{_cell(a["kind"])}</span>'
                       f'<span class="title">{_cell(a["title"])}</span>{_alert_badge(a["state"])}</li>' for a in snap["alerts"])
        body = f'<ul class="alerts">{rows}</ul>'
    else:
        body = '<div class="empty">No alerts sent yet.</div>'
    out.append(f'<div class="card"><h2>Alerts sent and their buttons</h2>{body}</div>')
    return "".join(out)
