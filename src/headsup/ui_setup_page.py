"""The /setup page. Same look as the dashboard (it reuses the dashboard's stylesheet), one nonce'd script, no external resource.

The page is built in the browser from /api/setup-form with createElement and textContent only: nothing the server sends is ever
parsed as HTML, so a setting's help text, a chat name or an error message is shown as plain text. A key is shown only as "set" or
"missing"; the password boxes are always empty when the page is drawn and again after every save. It submits with fetch (the CSP has
form-action 'none'), and every request carries the session token.
"""

import re
from html import escape as _e

from headsup.ui_page import PAGE

_STYLE = re.search(r"<style>(.*?)</style>", PAGE, re.S).group(1)

_EXTRA_CSS = """
  .field { margin:0 0 var(--s2); } .field:last-child { margin-bottom:0; }
  .field label { display:block; font-weight:600; font-size:var(--t-sm); margin:0 0 4px; }
  .row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .row input[type=password] { flex:1 1 16rem; }
  input[type=password], textarea, input.num { width:100%; padding:8px 12px; border:1px solid #cbd5e1; border-radius:10px; background:#fff; color:var(--fg); }
  input.num { width:8rem; text-align:center; }
  textarea { resize:vertical; font:.9rem ui-monospace, SFMono-Regular, Menlo, monospace; }
  .chip { display:inline-block; padding:2px 10px; border-radius:999px; font-size:var(--t-xs); font-weight:600; background:#e2e8f0; color:#334155; }
  .chip.set, .chip.ok { background:#dcfce7; color:#166534; } .chip.bad { background:#ffe4e6; color:#9f1239; } .chip.missing { background:#e2e8f0; color:#475569; }
  button.small { padding:4px 12px; font-size:var(--t-sm); }
  .buttons { display:flex; flex-wrap:wrap; gap:var(--s1); margin:0 0 var(--s2); }
  .notice.bad { background:#ffe4e6; color:#9f1239; } .notice.good { background:#dcfce7; color:#166534; }
  .choices { list-style:none; margin:8px 0 0; padding:0; } .choices li { display:flex; align-items:center; gap:12px; padding:6px 0; flex-wrap:wrap; }
  .results { list-style:none; margin:0; padding:0; font-size:var(--t-sm); } .results li { display:flex; gap:8px; align-items:baseline; padding:3px 0; flex-wrap:wrap; }
  a.link { color:#3730a3; font-weight:600; font-size:var(--t-sm); text-decoration:none; } a.link:hover { text-decoration:underline; }
  [hidden] { display:none !important; }
  #message { position:sticky; top:0; z-index:10; }
  #message:not(:empty) { margin:0 0 var(--s2); }
"""

_SCRIPT = r"""
(function () {
  'use strict';
  const token = document.querySelector('meta[name=csrf]').content;
  const el = (id) => document.getElementById(id);
  const GROUPS = ['Telegram', 'GitHub', 'Calendar', 'Timing', 'Judge', 'Optional extras'];
  let form = null;
  let busy = false;
  let fields = {};

  // Build with textContent only: nothing the server sends is ever parsed as HTML.
  function h(tag, props, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = String(v);
      else if (k === 'on') { for (const [t, f] of Object.entries(v)) e.addEventListener(t, f); }
      else if (k === 'hidden' || k === 'disabled' || k === 'type' || k === 'value') e[k] = v;
      else e.setAttribute(k, String(v));
    }
    for (const kid of kids.flat()) if (kid !== undefined && kid !== null && kid !== false) e.append(kid);
    return e;
  }

  // A request that gets no answer for a minute is given up, so the page never waits for ever.
  async function call(method, path, body) {
    const init = method === 'GET' ? {} : { method, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token }, body: JSON.stringify(body || {}) };
    const stop = new AbortController();
    const timer = setTimeout(() => stop.abort(), 60000);
    init.signal = stop.signal;
    try {
      const r = await fetch(path, init);
      let data = {};
      try { data = await r.json(); } catch (e) { /* an empty or non-JSON answer */ }
      return [r.status, data];
    } catch (e) {
      return [0, { message: stop.signal.aborted ? 'The app did not answer within a minute.' : 'Could not reach the app. Is it still running?', timedOut: stop.signal.aborted }];
    } finally {
      clearTimeout(timer);
    }
  }

  // The outcome line sits in a sticky bar at the top of the page, so it is on screen whichever card's button was pressed.
  function say(text, kind, withApply) {
    const box = el('message');
    box.replaceChildren(
      text ? h('div', { class: ('notice ' + (kind || '')).trim(), text: text }) : '',
      withApply ? h('button', { class: 'secondary small', type: 'button', id: 'apply', text: 'Apply saved settings', on: { click: apply } }) : '');
    if (text && typeof box.scrollIntoView === 'function') box.scrollIntoView({ block: 'nearest' });
  }

  // The server sends the time in UTC; the person reading it lives in local time.
  function localTime(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso).slice(0, 16).replace('T', ' ');
    const p = (n) => String(n).padStart(2, '0');
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  async function busyWhile(fn) {
    if (busy) return;
    busy = true;
    for (const b of document.querySelectorAll('button')) b.disabled = true;
    try { await fn(); } finally {
      busy = false;
      for (const b of document.querySelectorAll('button')) b.disabled = false;
    }
  }

  // The three helper buttons (find chat, test message, test connection) only read or send: each one disables just itself, shows what
  // it is doing, and leaves the rest of the page usable. Saving and choosing a chat change the settings, so those still lock everything.
  const inFlight = {};
  async function runOne(name, button, progress, fn) {
    if (busy || inFlight[name]) return;
    inFlight[name] = true;
    if (button) button.disabled = true;
    if (progress) say(progress, '');
    try { await fn(); } finally {
      delete inFlight[name];
      const again = button ? el(button.id) : null;
      if (again && !busy) again.disabled = false;
    }
  }

  // ---- the form
  const lines = (v) => String(v || '').split(',').filter(Boolean).join('\n');
  const norm = (v) => String(v || '').split(/[\s,]+/).filter(Boolean).join(',');

  function secretRow(f) {
    const isSet = f.state === 'set';
    const input = h('input', { type: 'password', id: 'f-' + f.name, autocomplete: 'off', spellcheck: 'false', placeholder: 'Paste it here', hidden: isSet });
    const replace = h('button', { class: 'plain small', type: 'button', text: 'Replace', hidden: !isSet, on: { click: () => { input.hidden = false; replace.hidden = true; input.focus(); } } });
    const clear = h('button', { class: 'plain small', type: 'button', text: 'Clear', hidden: !isSet, on: { click: () => busyWhile(() => clearKey(f)) } });
    fields[f.name] = { kind: f.kind, input: input };
    return h('div', { class: 'row' }, h('span', { class: 'chip ' + (isSet ? 'set' : 'missing'), text: isSet ? 'set' : 'missing' }), input, replace, clear);
  }

  function row(f) {
    let control;
    if (f.kind === 'readonly') {
      return h('div', { class: 'field' }, h('label', { text: f.label }),
        h('p', { class: 'hint', text: (f.state === 'custom' ? 'A custom value is set in the settings file (not shown). ' : 'Using the evaluated default. ') + f.help }));
    }
    if (f.kind === 'secret' || f.kind === 'id') {
      control = secretRow(f);
    } else if (f.kind === 'list') {
      const t = h('textarea', { id: 'f-' + f.name, rows: '3', spellcheck: 'false', placeholder: 'owner/repository, one per line' });
      t.value = lines(f.value);
      fields[f.name] = { kind: f.kind, input: t, before: norm(f.value) };
      control = t;
    } else {
      const i = h('input', { type: 'number', id: 'f-' + f.name, class: 'num', min: '1', max: '2880' });
      i.value = f.value || '';
      fields[f.name] = { kind: f.kind, input: i, before: norm(f.value) };
      control = i;
    }
    const shell = f.from_shell && form.shell_overrides.includes(f.name)
      ? h('p', { class: 'notice', text: 'This is also set in your shell, and the shell wins. Saving here changes the settings file only.' }) : null;
    return h('div', { class: 'field' }, h('label', { for: 'f-' + f.name, text: f.label + (f.required ? ' (required)' : '') }), control, shell, h('p', { class: 'hint', text: f.help }));
  }

  function telegramTools() {
    return h('div', {}, h('div', { class: 'buttons' },
      h('button', { class: 'secondary', type: 'button', id: 'find-chat', text: 'Find my chat ID', on: { click: (ev) => runOne('find', ev.currentTarget, 'Looking for messages sent to your bot...', findChat) } }),
      h('button', { class: 'secondary', type: 'button', id: 'send-test', text: 'Send test message', on: { click: (ev) => runOne('send', ev.currentTarget, 'Sending the test message...', sendTest) } })),
      h('div', { id: 'chat-area' }));
  }

  function render() {
    fields = {};
    const cards = [];
    for (const g of GROUPS) {
      const fs = form.fields.filter((f) => f.group === g);
      if (!fs.length) continue;
      cards.push(h('section', { class: 'card' }, h('h2', { text: g }), fs.map(row), g === 'Telegram' ? telegramTools() : null));
    }
    el('app').replaceChildren(...cards);
    const missing = form.required_missing || [];
    el('notices').replaceChildren(missing.length
      ? h('div', { class: 'notice', text: 'Still needed before checks can run: ' + missing.join(', ') + '.' }) : '',
      form.unapplied ? h('div', { class: 'notice bad', text: 'Saved settings are not active yet. Press Apply.' }) : '');
  }

  function collect() {
    const values = {};
    const clear = [];
    for (const f of form.fields) {
      const r = fields[f.name];
      if (!r) continue;
      const v = r.input.value.trim();
      if (r.kind === 'secret' || r.kind === 'id') {
        if (v) values[f.name] = v; // an empty box means "keep what is saved"
      } else if (norm(v) !== r.before) {
        if (norm(v)) values[f.name] = v; else clear.push(f.name);
      }
    }
    return { values: values, clear: clear };
  }

  // ---- what the buttons do
  function take(status, data, okKind) {
    if (data.form) { form = data.form; form.unapplied = !!data.unapplied; render(); }
    const bad = status >= 400 || status === 0 || data.ok === false;
    const warn = data.applied === false && data.saved !== false && data.error;
    say(data.message || (bad ? 'Something went wrong.' : 'Done.'), bad ? 'bad' : (warn ? '' : (okKind || 'good')), !!data.unapplied);
  }

  async function save() {
    const [status, data] = await call('POST', '/api/setup/save', collect());
    take(status, data);
  }

  async function clearKey(f) {
    const [status, data] = await call('POST', '/api/setup/clear', { name: f.name });
    take(status, data);
  }

  async function apply() {
    await busyWhile(async () => { const [status, data] = await call('POST', '/api/setup/apply', {}); take(status, data); });
  }

  async function findChat() {
    const [status, data] = await call('POST', '/api/setup/find-chat', {});
    const area = el('chat-area');
    if (status === 200 && data.status === 'choose') {
      say('', '');
      area.replaceChildren(h('p', { class: 'hint', text: data.message }),
        h('ul', { class: 'choices' }, data.choices.map((c) => h('li', {},
          h('span', { text: c.name + (c.last ? ' (last message ' + localTime(c.last) + ')' : '') }),
          h('button', { class: 'primary small', type: 'button', text: data.single ? 'Yes, this is me' : 'Use this chat', on: { click: () => busyWhile(() => useChat(c.index)) } })))));
    } else {
      area.replaceChildren();
      take(status, data);
    }
  }

  async function useChat(index) {
    const [status, data] = await call('POST', '/api/setup/use-chat', { index: index });
    if (status === 200 && data.status === 'saved') el('chat-area').replaceChildren();
    take(status, data);
    if (status === 200 && data.status === 'saved') say((data.message || 'Saved.') + ' Chat: ' + data.name + '. Press Send test message to check it.', 'good', !!data.unapplied);
  }

  // Every outcome has its own wording: the server's own line when it sent one, else which kind of failure the page itself saw.
  async function sendTest() {
    const [status, data] = await call('POST', '/api/setup/test-message', {});
    let text = data.message;
    if (status === 0) text = 'NOT SENT (' + (data.timedOut ? 'no_answer' : 'no_connection') + '): ' + data.message;
    else if (!text) text = 'NOT SENT (no_message): the app answered HTTP ' + status + ' without a message.';
    say(text, data.ok ? 'good' : 'bad');
  }

  async function testConnection() {
    say('Testing the saved settings...', '');
    const waiting = (name) => h('li', {}, h('span', { class: 'chip missing', text: 'Testing' }), h('span', { text: name + ': waiting for the answer...' }));
    el('results').replaceChildren(waiting('GitHub'), waiting('Calendar'), waiting('Telegram'));
    const [status, data] = await call('POST', '/api/setup/test-connection', {});
    if (status !== 200 || !data.ok) { el('results').replaceChildren(); say(data.message || 'The test could not run.', 'bad'); return; }
    say('Test finished. The results are at the bottom of the page.', 'good');
    const ms = data.timing_ms || {};
    const took = (key) => (typeof ms[key] === 'number' ? ' (' + (ms[key] / 1000).toFixed(1) + ' s)' : '');
    const line = (name, ok, text, key) => h('li', {}, h('span', { class: 'chip ' + (ok ? 'ok' : 'bad'), text: ok ? 'OK' : 'Problem' }), h('span', { text: name + ': ' + text + took(key) }));
    const skipped = (name, text) => h('li', {}, h('span', { class: 'chip missing', text: 'Not tested' }), h('span', { text: name + ': ' + text }));
    el('results').replaceChildren(
      ...data.github.map((r) => line('GitHub ' + r.name, r.ok, r.message, 'github')),
      data.calendar.configured ? line('Calendar', data.calendar.ok, data.calendar.message, 'calendar') : skipped('Calendar', data.calendar.message),
      data.telegram.configured ? line('Telegram', data.telegram.ok, data.telegram.message, 'telegram') : skipped('Telegram', data.telegram.message),
      skipped('Haiku', data.haiku.message), skipped('Write token', data.writeToken.message));
  }

  el('save').addEventListener('click', () => busyWhile(save));
  el('test').addEventListener('click', (ev) => runOne('test', ev.currentTarget, '', testConnection));

  (async function start() {
    const [status, data] = await call('GET', '/api/setup-form');
    if (status !== 200 || !Array.isArray(data.fields)) { say('Could not load the settings. Reload this page.', 'bad'); return; }
    form = data;
    render();
  })();
})();
"""

_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="csrf" content="@@TOKEN@@">
<title>Heads-Up setup</title>
<style>@@STYLE@@@@EXTRA@@</style>
</head>
<body>
<main>
<header>
  <div class="brand">
    <span class="logo" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg></span>
    <div><h1>Heads-Up setup</h1><p class="tagline">Everything stays on this Mac. Keys are never shown again after you save them.</p></div>
  </div>
  <a class="link" href="/">Back to the dashboard</a>
</header>
<div id="message" role="status" aria-live="polite"></div>
<div id="notices"></div>
<div id="app"></div>
<div class="buttons">
  <button id="save" class="primary" type="button">Save</button>
  <button id="test" class="secondary" type="button">Test connection</button>
</div>
<ul id="results" class="results"></ul>
<footer>Local only (127.0.0.1). Saving writes the settings file on this Mac (readable by you only) and the app starts using the new settings at once. Haiku and the write token are not tested: a test call costs money, and the write token can only be tested by re-running a build.</footer>
</main>
<script nonce="@@NONCE@@">@@SCRIPT@@</script>
</body>
</html>
"""


def render_setup_page(token, nonce):
    return (_HTML.replace("@@STYLE@@", _STYLE).replace("@@EXTRA@@", _EXTRA_CSS).replace("@@SCRIPT@@", _SCRIPT)
            .replace("@@TOKEN@@", _e(token)).replace("@@NONCE@@", _e(nonce)))
