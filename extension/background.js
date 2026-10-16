// Nova's side of the browser: one WebSocket out to the Nova server on this PC, commands
// in, results back. The connection goes outward on purpose -- Nova never opens a port into
// Chrome (no DevTools port, no remote debugging), and nothing but Nova, holding the same
// token as the canvas, can send this anything.
//
// Protocol (JSON text frames):
//   ext  -> nova  {type: "hello", version, agent}        once, on connect
//   nova -> ext   {id, method, params}
//   ext  -> nova  {id, ok: true, result} | {id, ok: false, error}
//   ext  -> nova  {type: "ping"}                          every 20 s; keeps the worker alive

const VERSION = chrome.runtime.getManifest().version;
const DEFAULTS = { port: 8765, token: '' };
const KEEPALIVE_MS = 20000;

let ws = null;
let keepalive = null;

// -- settings and status ----------------------------------------------------------------
async function settings() {
  const stored = await chrome.storage.local.get(DEFAULTS);
  return { port: Number(stored.port) || DEFAULTS.port, token: String(stored.token || '').trim() };
}

// No token typed in? If this Chrome has opened the canvas, the pairing cookie is already
// here -- the same secret, read through the cookies API, so there's nothing to paste.
async function token(port) {
  const s = await settings();
  if (s.token) return s.token;
  for (const host of ['127.0.0.1', 'localhost']) {
    try {
      const c = await chrome.cookies.get({ url: `http://${host}:${port}/`, name: 'nova_token' });
      if (c && c.value) return c.value;
    } catch (_) { /* no cookie permission for that host */ }
  }
  return '';
}

async function setStatus(state, detail = '') {
  await chrome.storage.session.set({ status: { state, detail, at: Date.now() } });
  const badge = { connected: '', offline: '!', 'no-token': '?' }[state] ?? '!';
  chrome.action.setBadgeText({ text: badge });
  chrome.action.setBadgeBackgroundColor({ color: state === 'connected' ? '#7c5cff' : '#ff8a65' });
  chrome.action.setTitle({ title: `Nova: ${state}${detail ? ` (${detail})` : ''}` });
}

// -- connection -----------------------------------------------------------------------------
async function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  const { port } = await settings();
  const secret = await token(port);
  if (!secret) {
    await setStatus('no-token', 'open the Nova canvas in this Chrome once, or paste the token');
    return;
  }
  // Ask first with a plain request: when Nova isn't running, a failed fetch can be caught
  // quietly, while a failed WebSocket logs a red error on chrome://extensions every retry.
  try {
    const probe = await fetch(`http://127.0.0.1:${port}/api/ext`, { headers: { 'X-Nova-Token': secret } });
    if (probe.status === 401) { await setStatus('no-token', 'Nova refused the token'); return; }
  } catch (_) {
    await setStatus('offline', 'Nova is not running');
    return;
  }
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  const socket = new WebSocket(`ws://127.0.0.1:${port}/ext?token=${encodeURIComponent(secret)}`);
  ws = socket;
  socket.onopen = () => {
    socket.send(JSON.stringify({ type: 'hello', version: VERSION, agent: navigator.userAgent.match(/Chrome\/[\d.]+/)?.[0] || 'Chrome' }));
    setStatus('connected', `port ${port}`);
    clearInterval(keepalive);
    keepalive = setInterval(() => socket.readyState === WebSocket.OPEN && socket.send('{"type":"ping"}'), KEEPALIVE_MS);
  };
  socket.onmessage = (event) => onCommand(socket, event.data);
  socket.onclose = (event) => {
    if (ws === socket) ws = null;
    clearInterval(keepalive);
    // 1008 is the server refusing the token. Retrying with the same one is pointless.
    setStatus(event.code === 1008 ? 'no-token' : 'offline',
              event.code === 1008 ? 'Nova refused the token' : 'Nova is not running');
  };
  socket.onerror = () => { /* onclose follows and says why */ };
}

// A worker that Chrome put to sleep loses its timers, so the reconnect loop is an alarm.
chrome.alarms.create('nova-reconnect', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((a) => a.name === 'nova-reconnect' && connect());
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && (changes.port || changes.token)) { ws?.close(); ws = null; connect(); }
});
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg?.type === 'reconnect') { ws?.close(); ws = null; connect().then(() => reply({ ok: true })); return true; }
  return false;
});
connect();

async function onCommand(socket, raw) {
  let msg;
  try { msg = JSON.parse(raw); } catch (_) { return; }
  if (msg.id == null || !msg.method) return;
  let reply;
  // A dot on the toolbar icon while Nova is working in this browser.
  if (msg.method !== 'status') chrome.action.setBadgeText({ text: '•' });
  try {
    const fn = METHODS[msg.method];
    if (!fn) throw new Error(`unknown method ${msg.method}`);
    reply = { id: msg.id, ok: true, result: await fn(msg.params || {}) };
  } catch (e) {
    reply = { id: msg.id, ok: false, error: e.message || String(e) };
  }
  if (msg.method !== 'status') chrome.action.setBadgeText({ text: '' });
  if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(reply));
}

// -- which tab --------------------------------------------------------------------------
// The tab Nova works in is the last web page that was in front, whoever put it there:
// Nova opening one, or you clicking over to one. The canvas itself never counts, or
// typing a request into it would make Nova act on its own UI.
async function isWebPage(tab) {
  if (!tab || !/^https?:/.test(tab.url || tab.pendingUrl || '')) return false;
  const { port } = await settings();
  return !new RegExp(`^https?://(127\\.0\\.0\\.1|localhost):${port}/`).test(tab.url || '');
}

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  try { if (await isWebPage(await chrome.tabs.get(tabId))) await chrome.storage.session.set({ tabId }); } catch (_) { /* closed */ }
});

async function currentTab(params) {
  if (params.tab) return chrome.tabs.get(Number(params.tab));
  const { tabId } = await chrome.storage.session.get('tabId');
  if (tabId) {
    try { const t = await chrome.tabs.get(tabId); if (await isWebPage(t)) return t; } catch (_) { /* closed */ }
  }
  const tabs = await chrome.tabs.query({ active: true });
  for (const t of tabs) if (await isWebPage(t)) return t;
  throw new Error('No web page is open. Open one first.');
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Resolves when the tab finishes loading (or after `timeout`, so a page with a never-ending
// spinner doesn't hang the request -- what has loaded by then is usually enough).
function loaded(tabId, timeout = 20000) {
  return new Promise((resolve) => {
    const finish = () => { chrome.tabs.onUpdated.removeListener(listener); clearTimeout(timer); resolve(); };
    const listener = (id, info) => { if (id === tabId && info.status === 'complete') finish(); };
    const timer = setTimeout(finish, timeout);
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then((t) => t.status === 'complete' && finish(), finish);
  });
}

// -- talking to the page ------------------------------------------------------------------
async function page(tabId, name, args = {}) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] });
  const [res] = await chrome.scripting.executeScript({
    target: { tabId },
    func: (n, a) => window.__novaAgent.run(n, a),
    args: [name, args],
  });
  const r = res && res.result;
  if (!r) throw new Error('The page did not answer; it may still be loading.');
  if (r.error) throw new Error(r.error);
  return r;
}

// After anything that may change the page: follow a navigation if one started, then
// report only what changed (or the whole new page if it is a new page).
// Tabs (and popup windows, which are tabs too) that appear while an action settles.
function watchNewTabs() {
  const seen = [];
  const listener = (t) => seen.push(t);
  chrome.tabs.onCreated.addListener(listener);
  return () => { chrome.tabs.onCreated.removeListener(listener); return seen; };
}

async function report(tab, r, verb, stopWatching = null) {
  if (r.openUrl) {
    if (stopWatching) stopWatching();
    const t = await chrome.tabs.create({ url: r.openUrl, openerTabId: tab.id });
    await chrome.storage.session.set({ tabId: t.id });
    await sleep(300);
    await loaded(t.id);
    const s = await page(t.id, 'snapshot');
    return { page: `${verb} ${r.line}; it opened in a new tab.\n${s.page}`, pageChars: s.pageChars };
  }
  await sleep(350);
  if ((await chrome.tabs.get(tab.id)).status === 'loading') await loaded(tab.id);
  const opened = stopWatching ? stopWatching() : [];
  if (opened.length) {
    const t = opened[opened.length - 1];
    await chrome.storage.session.set({ tabId: t.id });
    await sleep(300);
    await loaded(t.id);
    const fresh = await chrome.tabs.get(t.id);
    let s = { page: `${fresh.title || ''}\n${fresh.url || fresh.pendingUrl || ''}`, pageChars: 0 };
    try { s = await page(t.id, 'snapshot'); } catch (_) { /* not a web page: an app link, a download */ }
    return { page: `${verb} ${r.line}; it opened a new ${fresh.windowId === tab.windowId ? 'tab' : 'window'}.\n${s.page}`,
             pageChars: s.pageChars, changed: true };
  }
  let c;
  try {
    c = await page(tab.id, 'changes');
  } catch (_) {  // the document was replaced while we looked
    await loaded(tab.id);
    c = await page(tab.id, 'changes');
  }
  return { page: `${verb}${r.line ? ` ${r.line}` : ''}.${c.full ? ' Now on:' : ''}\n${c.page}`, pageChars: c.pageChars,
           changed: c.full || c.page !== 'No visible change.' };
}

function hostOf(url) {
  try { return new URL(url).host; } catch (_) { return ''; }
}

async function realClick(tabId, { x, y }) {
  await trusted(tabId, async (send) => {
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
  });
}

// Real input through the DevTools protocol, for the few sites that ignore synthetic
// events. Chrome shows a "started debugging" bar while it is attached, so it is attached
// for one gesture and detached straight after, and only when asked for.
async function trusted(tabId, fn) {
  const target = { tabId };
  await chrome.debugger.attach(target, '1.3');
  try { await fn((method, params) => chrome.debugger.sendCommand(target, method, params)); }
  finally { await chrome.debugger.detach(target).catch(() => {}); }
}

const VK = { Enter: 13, Escape: 27, Tab: 9, Backspace: 8, Delete: 46, ArrowUp: 38, ArrowDown: 40,
             ArrowLeft: 37, ArrowRight: 39, PageDown: 34, PageUp: 33, Home: 36, End: 35, Space: 32 };

// -- methods Nova can call ----------------------------------------------------------------
const METHODS = {
  async status() {
    let tab = null;
    try { const t = await currentTab({}); tab = { id: t.id, title: t.title, url: t.url }; } catch (_) { /* none */ }
    return { version: VERSION, tab };
  },

  async tabs() {
    const { tabId } = await chrome.storage.session.get('tabId');
    const all = await chrome.tabs.query({});
    const rows = [];
    for (const t of all) {
      if (!(await isWebPage(t))) continue;
      rows.push(`${t.id === tabId ? '*' : ' '} ${t.id}\t${(t.title || '').slice(0, 70)}\t${(t.url || '').slice(0, 90)}`);
    }
    return { text: rows.join('\n') || 'No web pages are open.' };
  },

  async use_tab({ tab }) {
    const t = await chrome.tabs.get(Number(tab));
    await chrome.tabs.update(t.id, { active: true });
    await chrome.windows.update(t.windowId, { focused: true });
    await chrome.storage.session.set({ tabId: t.id });
    return page(t.id, 'snapshot');
  },

  async open({ url, new_tab = false, limit }) {
    if (!/^https?:\/\//.test(url || '')) throw new Error('Give a full http or https URL.');
    let tab = null;
    if (!new_tab) { try { tab = await currentTab({}); } catch (_) { /* none yet */ } }
    tab = tab ? await chrome.tabs.update(tab.id, { url, active: true }) : await chrome.tabs.create({ url, active: true });
    await chrome.windows.update(tab.windowId, { focused: true });
    await chrome.storage.session.set({ tabId: tab.id });
    await sleep(300);  // let the navigation start, so "complete" means the new page
    await loaded(tab.id);
    return page(tab.id, 'snapshot', { limit });
  },

  async back(params) {
    const tab = await currentTab(params);
    await chrome.tabs.goBack(tab.id);
    await sleep(300);
    await loaded(tab.id);
    return page(tab.id, 'snapshot');
  },

  async snapshot(params) {
    return page((await currentTab(params)).id, 'snapshot', params);
  },

  // The click ladder, run here so no model spends a turn on it:
  //   1. a scripted click inside the page (fast, invisible, enough for most buttons)
  //   2. a real mouse click through the debugger, when (1) changed nothing. Chrome only
  //      lets a real click open a window, a popup or an app link.
  //   3. Windows accessibility (UI Automation), done by Nova in Python when (2) also
  //      changed nothing -- this answer says so with `unchanged`, the button's name and
  //      the tab's title.
  // A site where (1) never works is remembered, so it starts at (2) next time. Nothing
  // that could send, pay or delete is ever clicked a second time automatically.
  async click(params) {
    const tab = await currentTab(params);
    const host = hostOf(tab.url);
    const { methods = {} } = await chrome.storage.local.get('methods');
    const start = (params.target && params.target.trusted) || methods[host] === 'trusted';
    const target = { ...(params.target || {}), ...(start ? { trusted: true } : {}) };
    const stop = watchNewTabs();
    const r = await page(tab.id, 'click', { ...params, target });
    if (r.risky) { stop(); return { risky: true, page: r.line }; }
    if (r.external) { stop(); return { external: r.external, page: `${r.line} is a link to an app (${r.external.split(':')[0]}).` }; }
    if (r.point) await realClick(tab.id, r.point);
    const first = await report(tab, r, 'Clicked', stop);
    const facts = { name: r.name, tabTitle: tab.title, host, sensitive: r.sensitive };
    if (first.changed || params.escalate === false) return { ...first, ...facts, method: start ? 'trusted' : 'page' };
    if (r.sensitive) {
      return { ...first, ...facts, unchanged: true, method: start ? 'trusted' : 'page',
               page: `${first.page}\nIt may still have worked: this button sends or changes something, so it wasn't clicked again. Check before retrying.` };
    }
    if (!start) {
      const again = await page(tab.id, 'click', { ...params, target: { ...(params.target || {}), trusted: true } });
      const stop2 = watchNewTabs();
      if (again.point) await realClick(tab.id, again.point);
      const second = await report(tab, again, 'Clicked', stop2);
      if (second.changed) {
        methods[host] = 'trusted';
        await chrome.storage.local.set({ methods });
        return { ...second, ...facts, method: 'trusted',
                 page: `The page ignored a scripted click, so I used a real one (remembered for ${host}).\n${second.page}` };
      }
    }
    return { ...first, ...facts, unchanged: true, method: 'trusted' };
  },

  async changes(params) {
    const tab = await currentTab(params);
    await sleep(350);
    return page(tab.id, 'changes');
  },

  async fill(params) {
    const tab = await currentTab(params);
    const r = await page(tab.id, 'fill', params);
    return params.submit ? report(tab, r, 'Filled and submitted') : { page: `Filled ${r.line}`, pageChars: r.pageChars };
  },

  async form(params) {
    const tab = await currentTab(params);
    const r = await page(tab.id, 'form', { fields: params.fields, submitRef: params.submit_ref ?? null, guard: params.guard });
    if (r.risky) return { risky: true, page: r.line, filled: r.done };
    const head = `Filled ${r.done.length} field${r.done.length === 1 ? '' : 's'}` +
                 (r.failed.length ? `; ${r.failed.length} failed:\n${r.failed.join('\n')}` : '');
    if (!r.clicked) return report(tab, { line: '' }, head);
    return report(tab, r.clicked, `${head}, then clicked`);
  },

  async press(params) {
    const tab = await currentTab(params);
    const name = params.key || 'Enter';
    if (params.trusted) {
      await trusted(tab.id, async (send) => {
        const code = VK[name] || name.toUpperCase().charCodeAt(0);
        const text = name === 'Enter' ? '\r' : name.length === 1 ? name : undefined;
        await send('Input.dispatchKeyEvent', { type: 'keyDown', key: name, windowsVirtualKeyCode: code, text });
        await send('Input.dispatchKeyEvent', { type: 'keyUp', key: name, windowsVirtualKeyCode: code });
      });
      return report(tab, { line: name }, 'Pressed');
    }
    const r = await page(tab.id, 'press', { key: name });
    return report(tab, { line: `${name} on ${r.line}` }, 'Pressed');
  },

  async scroll(params) {
    return page((await currentTab(params)).id, 'scroll', params);
  },

  async read(params) {
    return page((await currentTab(params)).id, 'read', params);
  },

  async find(params) {
    return page((await currentTab(params)).id, 'find', params);
  },

  async wait(params) {
    return page((await currentTab(params)).id, 'wait', params);
  },

  // Local files into a page's file input, the way a person picking them would: through
  // the debugger, since a page script can't name a path on disk. The input is found
  // through shadow roots too (YouTube Studio keeps its own inside them).
  async upload(params) {
    const tab = await currentTab(params);
    const files = params.files || [];
    if (!files.length) throw new Error('upload needs at least one file');
    const selector = params.selector || 'input[type=file]';
    const find = `(() => { const sel = ${JSON.stringify(selector)};
      const walk = (root) => { const hit = root.querySelector(sel); if (hit) return hit;
        for (const el of root.querySelectorAll('*')) if (el.shadowRoot) { const h = walk(el.shadowRoot); if (h) return h; }
        return null; };
      return walk(document); })()`;
    let attached = false;
    await trusted(tab.id, async (send) => {
      await send('DOM.getDocument', { depth: 0 });
      const { result } = await send('Runtime.evaluate', { expression: find });
      if (!result || !result.objectId) return;
      await send('DOM.setFileInputFiles', { files, objectId: result.objectId });
      attached = true;
    });
    if (!attached) throw new Error(`no file input matching ${selector} on the page`);
    return report(tab, { line: `${files.length} file${files.length === 1 ? '' : 's'}` }, 'Attached');
  },

  // Pick up a changed background.js without a trip to chrome://extensions.
  async reload() {
    setTimeout(() => chrome.runtime.reload(), 200);
    return { page: 'Reloading the Nova extension.' };
  },
};
