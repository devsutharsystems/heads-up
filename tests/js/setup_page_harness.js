'use strict';
// Runs the /setup page's script against a strict fake DOM and a fake fetch, and prints a JSON report [{name, ok, error?}].
// No dependencies. The fake DOM THROWS on anything that could turn text into markup (innerHTML, outerHTML, insertAdjacentHTML,
// document.write) and on anything that is not a node or a string passed to append/replaceChildren (an array would become the
// text "[object ...]" in a real browser). Usage: node setup_page_harness.js <file with the page script>

const fs = require('node:fs');
const vm = require('node:vm');

const script = fs.readFileSync(process.argv[2], 'utf8');
const TOKEN = 'session-token-for-tests';
const forbid = (what) => () => { throw new Error(what + ' must never be used'); };

class Text { constructor(d) { this.nodeType = 3; this.data = String(d); } get textContent() { return this.data; } }

class El {
  constructor(doc, tag) {
    this.ownerDocument = doc; this.nodeType = 1; this.tagName = tag.toUpperCase(); this.children = []; this.attributes = new Map();
    this.listeners = {}; this.className = ''; this.disabled = false; this.hidden = false; this.value = ''; this.type = '';
  }
  get id() { return this.attributes.get('id') || ''; }
  setAttribute(k, v) { this.attributes.set(k, String(v)); }
  getAttribute(k) { return this.attributes.has(k) ? this.attributes.get(k) : null; }
  append(...nodes) {
    for (const n of nodes) {
      if (typeof n !== 'string' && !(n && (n.nodeType === 1 || n.nodeType === 3))) throw new TypeError('not a node or a string: ' + Object.prototype.toString.call(n));
      this.children.push(typeof n === 'string' ? new Text(n) : n);
    }
  }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes.filter((n) => n !== '')); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  focus() { this.ownerDocument.active = this; }
  async click() { for (const f of this.listeners.click || []) await f({ type: 'click', target: this, preventDefault() {} }); }
  get textContent() { return this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this.children = String(v) === '' ? [] : [new Text(v)]; }
  all(out = []) { for (const c of this.children) if (c.nodeType === 1) { out.push(c); c.all(out); } return out; }
}
for (const n of ['innerHTML', 'outerHTML']) Object.defineProperty(El.prototype, n, { get: forbid(n), set: forbid(n) });
El.prototype.insertAdjacentHTML = forbid('insertAdjacentHTML');

function makeDocument() {
  const doc = { created: [], active: null };
  doc.createElement = (tag) => { doc.created.push(tag.toLowerCase()); return new El(doc, tag); };
  doc.write = forbid('document.write');
  doc.body = new El(doc, 'body');
  for (const id of ['notices', 'app', 'message', 'results']) { const e = new El(doc, id === 'results' ? 'ul' : 'div'); e.setAttribute('id', id); doc.body.append(e); }
  for (const id of ['save', 'test']) { const b = new El(doc, 'button'); b.setAttribute('id', id); doc.body.append(b); }
  doc.getElementById = (id) => doc.body.all().find((e) => e.id === id) || null;
  doc.querySelector = (sel) => (sel === 'meta[name=csrf]' ? { content: TOKEN } : null);
  doc.querySelectorAll = (sel) => (sel === 'button' ? doc.body.all().filter((e) => e.tagName === 'BUTTON') : []);
  return doc;
}

const tick = () => new Promise((r) => setImmediate(r));
async function settle() { for (let i = 0; i < 6; i++) await tick(); }

// the fields as GET /api/setup-form returns them
function field(name, label, group, kind, state, extra) {
  return Object.assign({ name, label, group, help: 'help for ' + label, kind, required: false, editable: kind !== 'readonly', state, from_shell: false }, extra || {});
}
function baseForm(over) {
  return Object.assign({
    fields: [
      field('TELEGRAM_BOT_TOKEN', 'Telegram bot token', 'Telegram', 'secret', 'set', { required: true }),
      field('TELEGRAM_CHAT_ID', 'Telegram chat ID', 'Telegram', 'id', 'missing', { required: true }),
      field('GITHUB_TOKEN', 'GitHub read token', 'GitHub', 'secret', 'missing'),
      field('GITHUB_REPOS', 'Repositories to watch', 'GitHub', 'list', 'set', { value: 'a/b,c/d' }),
      field('GITHUB_WRITE_REPOS', 'Repositories you may re-run', 'GitHub', 'list', 'set', { value: 'a/b' }),
      field('GITHUB_ACTIONS_WRITE_TOKEN', 'GitHub write token', 'GitHub', 'secret', 'missing'),
      field('ICAL_URL', 'Calendar address', 'Calendar', 'secret', 'set'),
      field('REMINDER_LEAD_MINUTES', 'Meeting reminder lead', 'Timing', 'int', 'set', { value: '30' }),
      field('ANTHROPIC_API_KEY', 'Anthropic key', 'Haiku', 'secret', 'missing'),
      field('DECIDER_FORMAT', 'Judge question format', 'Judge', 'readonly', 'default'),
      field('DECIDER_THRESHOLD', 'Judge threshold', 'Judge', 'readonly', 'custom'),
    ],
    required_missing: ['Telegram chat ID'], shell_overrides: [], file_exists: true, unapplied: false,
  }, over || {});
}

// routes: {'POST /api/setup/save': (body) => [status, data] | async fn}
async function boot(form, routes) {
  const doc = makeDocument();
  const calls = [];
  const fetchFake = async (url, init) => {
    init = init || {};
    const method = init.method || 'GET';
    calls.push({ url, method, headers: init.headers || {}, body: init.body === undefined ? undefined : JSON.parse(init.body) });
    if (routes.__throw) throw new TypeError('Failed to fetch');
    const key = method + ' ' + url;
    const handler = key === 'GET /api/setup-form' ? () => [200, form] : routes[key];
    if (!handler) return { status: 404, json: async () => ({ message: 'not found' }) };
    const [status, data] = await handler(init.body === undefined ? undefined : JSON.parse(init.body));
    return { status, json: async () => data };
  };
  const sandbox = { document: doc, fetch: fetchFake, JSON, Object, Array, String, Promise, Map, Set, console: { log() {}, error() {} } };
  vm.runInNewContext(script, sandbox);
  await settle();
  return { doc, calls, byId: (id) => doc.getElementById(id), buttonLabelled: (label) => doc.body.all().filter((e) => e.tagName === 'BUTTON' && e.textContent === label)[0] || null,
           text: () => doc.body.textContent, posts: () => calls.filter((c) => c.method === 'POST') };
}

function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }
function eq(a, b, msg) { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error((msg || 'not equal') + ': ' + JSON.stringify(a) + ' vs ' + JSON.stringify(b)); }

const report = [];
async function scenario(name, fn) {
  try { await fn(); report.push({ name, ok: true }); } catch (e) { report.push({ name, ok: false, error: String((e && e.message) || e) }); }
}

(async () => {
  await scenario('the form is drawn from the server: every key is a password box, empty, hidden when set', async () => {
    const t = await boot(baseForm(), {});
    for (const n of ['TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'GITHUB_TOKEN', 'GITHUB_ACTIONS_WRITE_TOKEN', 'ICAL_URL', 'ANTHROPIC_API_KEY']) {
      const i = t.byId('f-' + n);
      assert(i && i.type === 'password' && i.value === '', n + ' is an empty password input');
      eq(i.hidden, ['TELEGRAM_BOT_TOKEN', 'ICAL_URL'].includes(n), n + ' is hidden exactly when it is set');
    }
    eq(t.byId('f-GITHUB_REPOS').value, 'a/b\nc/d');
    eq(t.byId('f-REMINDER_LEAD_MINUTES').value, '30');
    assert(t.byId('f-DECIDER_FORMAT') === null && t.byId('f-DECIDER_THRESHOLD') === null, 'no input for the judge settings');
    assert(t.text().includes('Using the evaluated default.') && t.text().includes('A custom value is set in the settings file (not shown).'));
    assert(t.text().includes('Still needed before checks can run: Telegram chat ID.'));
    assert(t.buttonLabelled('Find my chat ID') && t.buttonLabelled('Send test message') && t.byId('save') && t.byId('test'));
  });

  await scenario('a key is only ever shown as set or missing; Replace opens an empty box', async () => {
    const t = await boot(baseForm(), {});
    const chips = t.doc.body.all().filter((e) => e.className.startsWith('chip '));
    assert(chips.every((c) => ['set', 'missing'].includes(c.textContent)), 'chips say set or missing only');
    const replace = t.doc.body.all().filter((e) => e.tagName === 'BUTTON' && e.textContent === 'Replace')[0];
    await replace.click();
    assert(t.byId('f-TELEGRAM_BOT_TOKEN').hidden === false && replace.hidden === true && t.byId('f-TELEGRAM_BOT_TOKEN').value === '');
  });

  await scenario('everything the server sends is shown as plain text, never parsed as HTML', async () => {
    const evil = '<img src=x onerror=alert(1)><script>alert(2)</script>';
    const form = baseForm();
    form.fields[0].label = evil; form.fields[0].help = evil; form.required_missing = [evil];
    const t = await boot(form, { 'POST /api/setup/test-connection': () => [200, { ok: true, github: [{ name: evil, ok: false, message: evil }], calendar: { configured: false, message: evil }, telegram: { configured: true, ok: true, message: evil }, haiku: { message: evil }, writeToken: { message: evil } }] });
    await t.byId('test').click();
    await settle();
    assert(!t.doc.created.some((x) => ['img', 'script', 'iframe', 'svg', 'object'].includes(x)), 'no element came from server text: ' + [...new Set(t.doc.created)]);
    assert(t.text().includes(evil), 'shown literally');
  });

  await scenario('Save sends only what was typed or changed, with the session token, then empties every key box', async () => {
    const after = baseForm();
    after.fields[2].state = 'set';
    const t = await boot(baseForm(), { 'POST /api/setup/save': () => [200, { ok: true, saved: true, applied: true, message: 'Saved. Settings reloaded.', changed: [], form: after }] });
    t.byId('f-GITHUB_TOKEN').value = '  typed-read-token-0001  ';
    t.byId('f-GITHUB_REPOS').value = 'a/b\nnew/repo';
    t.byId('f-GITHUB_WRITE_REPOS').value = '';
    await t.byId('save').click();
    await settle();
    const posts = t.posts();
    eq(posts.length, 1); eq(posts[0].url, '/api/setup/save'); eq(posts[0].method, 'POST');
    eq(posts[0].body, { values: { GITHUB_TOKEN: 'typed-read-token-0001', GITHUB_REPOS: 'a/b\nnew/repo' }, clear: ['GITHUB_WRITE_REPOS'] });
    eq(posts[0].headers['X-CSRF-Token'], TOKEN); eq(posts[0].headers['Content-Type'], 'application/json');
    assert(t.text().includes('Saved. Settings reloaded.'));
    for (const n of ['TELEGRAM_BOT_TOKEN', 'GITHUB_TOKEN', 'ICAL_URL', 'ANTHROPIC_API_KEY']) assert(t.byId('f-' + n).value === '', n + ' box is empty again');
    assert(t.byId('f-GITHUB_TOKEN').hidden === true, 'it now shows as set');
    assert(!JSON.stringify(t.doc.body.all().map((e) => [e.value, e.textContent])).includes('typed-read-token-0001'), 'the key is nowhere on the page');
  });

  await scenario('an untouched page saves nothing: unchanged lists (even with another separator) and empty boxes are not sent', async () => {
    const t = await boot(baseForm(), { 'POST /api/setup/save': () => [200, { ok: true, saved: false, applied: false, message: 'Nothing to save.', changed: [] }] });
    await t.byId('save').click();
    await settle();
    eq(t.posts()[0].body, { values: {}, clear: [] });
    t.byId('f-GITHUB_REPOS').value = 'a/b c/d';
    await t.byId('save').click();
    await settle();
    eq(t.posts()[1].body, { values: {}, clear: [] }, 'a space instead of a comma or a line break is the same list');
    t.byId('f-GITHUB_REPOS').value = 'c/d\na/b';
    await t.byId('save').click();
    await settle();
    eq(t.posts()[2].body, { values: { GITHUB_REPOS: 'c/d\na/b' }, clear: [] }, 'a different order is a change: the order is the user\'s');
  });

  await scenario('a refused save keeps what the user typed and says which setting is wrong', async () => {
    const t = await boot(baseForm(), { 'POST /api/setup/save': () => [400, { ok: false, error: 'invalid', field: 'GITHUB_REPOS', message: 'Repositories to watch is not valid.' }] });
    t.byId('f-GITHUB_TOKEN').value = 'typed-token-keep-me';
    await t.byId('save').click();
    await settle();
    assert(t.text().includes('Repositories to watch is not valid.'));
    eq(t.byId('f-GITHUB_TOKEN').value, 'typed-token-keep-me', 'nothing was re-drawn');
  });

  await scenario('Clear sends the name only', async () => {
    const t = await boot(baseForm(), { 'POST /api/setup/clear': () => [200, { ok: true, applied: true, message: 'Saved. Settings reloaded.', form: baseForm() }] });
    const clear = t.doc.body.all().filter((e) => e.tagName === 'BUTTON' && e.textContent === 'Clear')[0];
    await clear.click();
    await settle();
    eq(t.posts()[0].url, '/api/setup/clear'); eq(Object.keys(t.posts()[0].body), ['name']);
  });

  await scenario('one chat is offered for confirmation and nothing is saved until the click', async () => {
    const t = await boot(baseForm(), {
      'POST /api/setup/find-chat': () => [200, { ok: true, status: 'choose', single: true, message: 'Is this you?', choices: [{ index: 0, name: 'Ada Lovelace', last: '2026-11-02T11:46:40+00:00' }] }],
      'POST /api/setup/use-chat': () => [200, { ok: true, status: 'saved', name: 'Ada Lovelace', applied: true, message: 'Saved. Settings reloaded.', form: baseForm() }],
    });
    await t.buttonLabelled('Find my chat ID').click();
    await settle();
    assert(t.text().includes('Is this you?') && t.text().includes('Ada Lovelace') && t.buttonLabelled('Yes, this is me'));
    eq(t.posts().map((p) => p.url), ['/api/setup/find-chat'], 'asked Telegram, saved nothing');
    await t.buttonLabelled('Yes, this is me').click();
    await settle();
    eq(t.posts().map((p) => p.url), ['/api/setup/find-chat', '/api/setup/use-chat']); eq(t.posts()[1].body, { index: 0 });
    assert(t.text().includes('Chat: Ada Lovelace') && t.text().includes('Send test message'));
    assert(t.byId('chat-area').children.length === 0, 'the offer is gone once used');
  });

  await scenario('several chats: one button each, each sends its own index', async () => {
    const t = await boot(baseForm(), { 'POST /api/setup/find-chat': () => [200, { ok: true, status: 'choose', single: false, message: 'Several people have messaged this bot. Pick yourself.', choices: [{ index: 0, name: 'Grace', last: null }, { index: 1, name: 'Ada', last: '2026-11-02T11:46:40+00:00' }] }],
      'POST /api/setup/use-chat': () => [200, { ok: true, status: 'saved', name: 'Ada', applied: true, message: 'Saved.', form: baseForm() }] });
    await t.buttonLabelled('Find my chat ID').click();
    await settle();
    const buttons = t.doc.body.all().filter((e) => e.tagName === 'BUTTON' && e.textContent === 'Use this chat');
    eq(buttons.length, 2);
    await buttons[1].click();
    await settle();
    eq(t.posts()[1].body, { index: 1 });
  });

  await scenario('nothing found, a webhook or no token are shown as messages, with no chat list', async () => {
    const t = await boot(baseForm(), { 'POST /api/setup/find-chat': () => [200, { ok: false, status: 'webhook', message: 'Remove the webhook yourself; Heads-Up will not do it for you.' }] });
    await t.buttonLabelled('Find my chat ID').click();
    await settle();
    assert(t.text().includes('will not do it for you') && !t.buttonLabelled('Use this chat') && !t.buttonLabelled('Yes, this is me'));
  });

  await scenario('a save that could not be applied offers Apply, and Apply sends one request', async () => {
    const busy = { ok: true, saved: true, applied: false, error: 'busy', unapplied: true, message: 'Saved. A check is still running, so the new settings are not active yet. Press Apply in a minute.', form: baseForm({ unapplied: true }) };
    const t = await boot(baseForm(), { 'POST /api/setup/save': () => [200, busy], 'POST /api/setup/apply': () => [200, { ok: true, applied: true, unapplied: false, message: 'Applied. Settings reloaded.', form: baseForm() }] });
    await t.byId('save').click();
    await settle();
    assert(t.buttonLabelled('Apply saved settings'), 'an Apply button is shown');
    assert(t.text().includes('Saved settings are not active yet. Press Apply.'));
    await t.buttonLabelled('Apply saved settings').click();
    await settle();
    eq(t.posts().map((p) => p.url), ['/api/setup/save', '/api/setup/apply']); eq(t.posts()[1].body, {});
    assert(t.text().includes('Applied. Settings reloaded.') && !t.buttonLabelled('Apply saved settings'));
  });

  await scenario('Test connection lists every check and marks Haiku and the write token as not tested', async () => {
    const t = await boot(baseForm(), { 'POST /api/setup/test-connection': () => [200, { ok: true, github: [{ name: 'a/b', ok: true, message: 'OK' }, { name: 'c/d', ok: false, message: 'HTTP 404 (repository not found, or the token cannot see it)' }],
      calendar: { configured: true, ok: true, message: 'readable' }, telegram: { configured: true, ok: true, message: 'OK, bot @x' }, haiku: { tested: false, message: 'not tested: a test call would use your Anthropic credit' }, writeToken: { tested: false, message: 'not tested: it can only be tested by re-running a build' } }] });
    await t.byId('test').click();
    await settle();
    const lines = t.byId('results').children.map((li) => li.textContent);
    eq(lines, ['OKGitHub a/b: OK', 'ProblemGitHub c/d: HTTP 404 (repository not found, or the token cannot see it)', 'OKCalendar: readable', 'OKTelegram: OK, bot @x',
      'Not testedHaiku: not tested: a test call would use your Anthropic credit', 'Not testedWrite token: not tested: it can only be tested by re-running a build']);
  });

  await scenario('the test message button posts one empty request and shows the answer', async () => {
    const t = await boot(baseForm(), { 'POST /api/setup/test-message': () => [200, { ok: true, message: 'Sent. Check Telegram.' }] });
    await t.buttonLabelled('Send test message').click();
    await settle();
    eq(t.posts().map((p) => [p.url, p.body]), [['/api/setup/test-message', {}]]); assert(t.text().includes('Sent. Check Telegram.'));
  });

  await scenario('buttons are disabled while a request runs, so a double click sends one request', async () => {
    let release;
    const gate = new Promise((r) => { release = r; });
    const t = await boot(baseForm(), { 'POST /api/setup/test-message': async () => { await gate; return [200, { ok: true, message: 'Sent.' }]; } });
    const first = t.buttonLabelled('Send test message').click();
    await settle();
    assert(t.doc.querySelectorAll('button').every((b) => b.disabled), 'every button is disabled meanwhile');
    await t.buttonLabelled('Send test message').click();
    release();
    await first;
    await settle();
    eq(t.posts().length, 1);
    assert(t.doc.querySelectorAll('button').every((b) => !b.disabled), 'and enabled again afterwards');
  });

  await scenario('a key typed on the page is sent once, in the save request, and in no other request', async () => {
    const typed = ['typed', 'value', '1234'].join('-'); // a made-up key, assembled at run time
    const t = await boot(baseForm(), {
      'POST /api/setup/save': () => [200, { ok: true, saved: true, applied: true, message: 'Saved.', form: baseForm() }],
      'POST /api/setup/test-connection': () => [200, { ok: true, github: [], calendar: { configured: false, message: '' }, telegram: { configured: false, message: '' }, haiku: { message: '' }, writeToken: { message: '' } }],
      'POST /api/setup/find-chat': () => [200, { ok: false, status: 'none', message: 'none' }], 'POST /api/setup/test-message': () => [200, { ok: true, message: 'ok' }],
    });
    t.byId('f-ANTHROPIC_API_KEY').value = typed;
    await t.byId('test').click(); await settle();
    await t.buttonLabelled('Find my chat ID').click(); await settle();
    await t.buttonLabelled('Send test message').click(); await settle();
    await t.byId('save').click(); await settle();
    const carrying = t.calls.filter((c) => JSON.stringify(c).includes(typed)).map((c) => c.url);
    eq(carrying, ['/api/setup/save']);
  });

  await scenario('every request goes to this app, with the session token on every POST and nothing on the GET', async () => {
    const t = await boot(baseForm(), { 'POST /api/setup/save': () => [200, { ok: true, saved: false, message: 'Nothing to save.' }], 'POST /api/setup/apply': () => [200, { ok: true, message: 'x' }] });
    await t.byId('save').click(); await settle();
    assert(t.calls.every((c) => c.url.startsWith('/') && !c.url.startsWith('//') && !c.url.includes('://')), 'relative URLs only');
    assert(t.posts().every((p) => p.headers['X-CSRF-Token'] === TOKEN && p.headers['Content-Type'] === 'application/json'));
    eq(t.calls[0], { url: '/api/setup-form', method: 'GET', headers: {}, body: undefined });
  });

  await scenario('an unreachable app, a refused form and an answer with no message are all said plainly', async () => {
    const down = await boot(baseForm(), { __throw: true });
    assert(down.text().includes('Could not load the settings. Reload this page.'), 'the form could not be loaded');
    assert(!down.byId('f-GITHUB_TOKEN'), 'and no half-drawn form is shown');
    const t = await boot(baseForm(), { 'POST /api/setup/save': () => [500, {}], 'POST /api/setup/test-message': () => [200, {}] });
    await t.byId('save').click(); await settle();
    assert(t.text().includes('Something went wrong.'), 'a 500 with no message');
    await t.buttonLabelled('Send test message').click(); await settle();
    assert(t.text().includes('No answer.'), 'a test-message answer with no message');
  });

  await scenario('a shell override is named on the field, and the page says the shell wins', async () => {
    const form = baseForm({ shell_overrides: ['GITHUB_REPOS'] });
    form.fields[3].from_shell = true;
    const t = await boot(form, {});
    assert(t.text().includes('This is also set in your shell, and the shell wins. Saving here changes the settings file only.'));
  });

  process.stdout.write(JSON.stringify(report));
})();
