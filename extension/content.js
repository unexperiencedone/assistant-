// Nova's page agent. Injected on demand by background.js into the top frame of a tab.
//
// The idea that makes this cheap: a model never needs a page's prose to act on it, only
// what it can act on. So a page becomes a short numbered list -- `[7] textbox "Email" *`
// -- and the model answers with a number. Each element keeps its number for as long as
// the document lives, which is what lets an action report only what changed.
//
// Everything here runs in the extension's isolated world: the page's scripts can't see
// or call it, but the DOM and its events are shared, so React and friends react to what
// it does exactly as they would to a person.

(() => {
  if (window.__novaAgent) return;

  const INTERACTIVE = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea', 'summary',
    '[role=button]', '[role=link]', '[role=checkbox]', '[role=radio]', '[role=switch]',
    '[role=tab]', '[role=menuitem]', '[role=menuitemcheckbox]', '[role=option]',
    '[role=combobox]', '[role=textbox]', '[role=searchbox]', '[role=slider]',
    '[contenteditable=""]', '[contenteditable=true]', '[onclick]',
  ].join(',');
  const HEADINGS = 'h1,h2,h3';
  // Clicking one of these can spend money, send something or destroy something. A model
  // has to come back with confirmed=true for them (the user said yes); Nova's own
  // automations and Claude Code don't pass the guard.
  const RISKY = /\b(pay|buy|purchase|place (your )?order|checkout|check out|send|submit|delete|remove|transfer|confirm|book now|subscribe|post|publish|tweet|reply all|unsubscribe|sign out|log ?out|deactivate|cancel (my )?(order|subscription|account))\b/i;
  const SECRET_AUTOCOMPLETE = /^(cc-|current-password|new-password|one-time-code)/;

  const refOf = new WeakMap();    // element -> number, stable while the document lives
  const byRef = new Map();        // number -> WeakRef(element)
  let nextRef = 1;
  let last = null;                // Map(ref -> line) from the previous map, for diffs
  let lastUrl = '';

  // -- reading elements ---------------------------------------------------------------
  const clean = (s, max = 80) => {
    s = String(s || '').replace(/\s+/g, ' ').trim();
    return s.length > max ? s.slice(0, max - 1) + '…' : s;
  };

  function* walk(root) {
    // Every element, including inside open shadow roots (YouTube, Salesforce, many
    // design systems keep their buttons there).
    const it = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let node = it.currentNode;
    while (node) {
      if (node.nodeType === 1) {
        yield node;
        if (node.shadowRoot) yield* walk(node.shadowRoot);
      }
      node = it.nextNode();
    }
  }

  function visible(el) {
    if (el.checkVisibility && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
  }

  function inView(el) {
    const r = el.getBoundingClientRect();
    return r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  }

  function textOfIds(ids) {
    return ids.split(/\s+/).map((id) => {
      const n = document.getElementById(id);
      return n ? n.innerText || n.textContent : '';
    }).join(' ');
  }

  function nameOf(el) {
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return clean(aria);
    const by = el.getAttribute('aria-labelledby');
    if (by) { const t = clean(textOfIds(by)); if (t) return t; }
    const tag = el.tagName;
    if ((tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') && el.labels && el.labels.length) {
      const t = clean([...el.labels].map((l) => l.innerText || l.textContent).join(' '));
      if (t) return t;
    }
    if (tag === 'INPUT') {
      const type = (el.type || '').toLowerCase();
      if (['submit', 'button', 'reset'].includes(type) && el.value) return clean(el.value);
      if (type === 'image' && el.alt) return clean(el.alt);
    }
    const ph = el.getAttribute('placeholder');
    if (ph) return clean(ph);
    if (tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') {
      const t = clean(el.innerText || el.textContent);
      if (t) return t;
      const img = el.querySelector && el.querySelector('img[alt], svg title');
      if (img) { const a = clean(img.getAttribute('alt') || img.textContent); if (a) return a; }
    }
    return clean(el.getAttribute('title') || el.getAttribute('name') || el.id || '');
  }

  function roleOf(el) {
    const role = el.getAttribute('role');
    if (role) return role;
    const tag = el.tagName;
    if (tag === 'A') return 'link';
    if (tag === 'BUTTON' || tag === 'SUMMARY') return 'button';
    if (tag === 'SELECT') return 'select';
    if (tag === 'TEXTAREA') return 'textbox';
    if (tag === 'INPUT') {
      const type = (el.type || 'text').toLowerCase();
      if (['submit', 'button', 'reset', 'image'].includes(type)) return 'button';
      if (type === 'checkbox' || type === 'radio') return type;
      if (type === 'range') return 'slider';
      if (type === 'file') return 'file';
      return ['text', 'search', 'url', 'tel'].includes(type) ? 'textbox' : `textbox:${type}`;
    }
    if (el.isContentEditable) return 'textbox';
    return 'clickable';
  }

  const isField = (el) => /^(textbox|searchbox|combobox|select)/.test(roleOf(el)) || el.isContentEditable;
  const isSecret = (el) => el.type === 'password' || SECRET_AUTOCOMPLETE.test(el.getAttribute('autocomplete') || '');

  function stateOf(el) {
    let s = '';
    const role = roleOf(el);
    if (role === 'checkbox' || role === 'radio' || role === 'switch') {
      const on = el.checked ?? el.getAttribute('aria-checked') === 'true';
      s += on ? ' [x]' : ' [ ]';
    } else if (el.tagName === 'SELECT') {
      const opt = el.selectedOptions && el.selectedOptions[0];
      s += ` ="${clean(opt ? opt.text : '', 40)}" (${el.options.length} options)`;
    } else if (isField(el)) {
      const v = el.isContentEditable ? el.innerText : el.value;
      if (v) s += isSecret(el) ? ' =•••' : ` ="${clean(v, 40)}"`;
    }
    if (el.getAttribute('aria-expanded') === 'true') s += ' (open)';
    if (el.getAttribute('aria-pressed') === 'true') s += ' (pressed)';
    if (el.getAttribute('aria-selected') === 'true' && role !== 'option') s += ' (selected)';
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') s += ' (disabled)';
    if (el.required || el.getAttribute('aria-required') === 'true') s += ' *';
    return s;
  }

  function refFor(el) {
    let ref = refOf.get(el);
    if (!ref) {
      ref = nextRef++;
      refOf.set(el, ref);
      byRef.set(ref, new WeakRef(el));
    }
    return ref;
  }

  function lineFor(el) {
    const ref = refFor(el);
    const role = roleOf(el);
    let name = nameOf(el);
    if (!name && role === 'link') name = clean((el.getAttribute('href') || '').replace(/^https?:\/\/[^/]+/, ''), 50);
    return `[${ref}] ${role} "${name}"${stateOf(el)}`;
  }

  // Everything the model could act on, in document order, with nested duplicates
  // (a <button> inside an <a> with the same words) folded into one.
  function collect() {
    const out = [];
    const taken = new Set();
    for (const el of walk(document.documentElement)) {
      const heading = el.matches(HEADINGS);
      if (!heading && !el.matches(INTERACTIVE)) continue;
      if (!visible(el)) continue;
      if (heading) {
        const t = clean(el.innerText, 70);
        if (t) out.push({ el, heading: true, line: `# ${t}`, view: inView(el) });
        continue;
      }
      if (!isField(el) && !nameOf(el)) continue;
      let parent = el.parentElement, dup = false;
      for (let i = 0; parent && i < 4; i++, parent = parent.parentElement) {
        if (taken.has(parent) && nameOf(parent) === nameOf(el)) { dup = true; break; }
      }
      if (dup) continue;
      taken.add(el);
      out.push({ el, line: lineFor(el), view: inView(el) });
    }
    return out;
  }

  function header() {
    const max = Math.max(document.documentElement.scrollHeight - innerHeight, 0);
    const where = max ? ` · scrolled ${Math.round((scrollY / max) * 100)}%` : '';
    return `${clean(document.title, 100) || '(untitled)'}\n${location.href.slice(0, 160)}${where}`;
  }

  // The page as the model sees it. What's on screen comes first when the list has to be
  // cut, but the lines keep document order so forms still read top to bottom.
  function snapshot({ limit = 60, all = false } = {}) {
    const items = collect();
    const cap = all ? 400 : limit;
    let keep = items;
    if (items.length > cap) {
      const order = items.map((it, i) => [it.view ? 0 : it.heading ? 2 : 1, i]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
      const chosen = new Set(order.slice(0, cap).map(([, i]) => i));
      keep = items.filter((_, i) => chosen.has(i));
    }
    last = new Map();
    for (const it of keep) if (!it.heading) last.set(refOf.get(it.el), it.line);
    lastUrl = location.href;
    const more = items.length - keep.length;
    const tail = more > 0 ? `\n… ${more} more off screen (find them by words, or scroll)` : '';
    return `${header()}\n${keep.map((it) => it.line).join('\n')}${tail}`;
  }

  // What changed since the last map: new or changed lines, and how many went away. A
  // navigation, or a change so big a diff saves nothing, gets a whole new map instead.
  function changes() {
    if (!last || lastUrl.split('#')[0] !== location.href.split('#')[0]) return { page: snapshot(), full: true };
    const items = collect().filter((it) => !it.heading);
    const now = new Map(items.map((it) => [refOf.get(it.el), it.line]));
    const added = [];
    for (const [ref, line] of now) if (last.get(ref) !== line) added.push(line);
    let gone = 0;
    for (const ref of last.keys()) if (!now.has(ref)) gone++;
    if (added.length > 30) return { page: snapshot(), full: true };
    last = now;
    if (!added.length && !gone) return { page: 'No visible change.', full: false };
    const lines = added.map((l) => `+ ${l}`);
    if (gone) lines.push(`- ${gone} element${gone === 1 ? '' : 's'} gone`);
    return { page: lines.join('\n'), full: false };
  }

  // -- finding a target ---------------------------------------------------------------
  function fromRef(ref) {
    const n = parseInt(String(ref).replace(/\D/g, ''), 10);
    const el = byRef.get(n)?.deref();
    if (!el || !el.isConnected) throw new Error(`[${n}] is no longer on the page; look at the page again`);
    return el;
  }

  function query(selector) {
    // Automations written for Playwright say `:visible`; CSS doesn't, so strip it and
    // take the first match that actually is.
    const plain = selector.replace(/:visible\b/g, '');
    const want = plain !== selector;
    const found = [...document.querySelectorAll(plain)];
    return (want ? found.find(visible) : found[0]) || null;
  }

  function byWords(words, { roles = null, fields = false } = {}) {
    const q = clean(words, 200).toLowerCase();
    let best = null, score = 0;
    for (const el of walk(document.documentElement)) {
      if (!el.matches(INTERACTIVE) || !visible(el)) continue;
      if (fields && !isField(el)) continue;
      if (roles && !roles.includes(roleOf(el))) continue;
      const name = nameOf(el).toLowerCase();
      if (!name) continue;
      const s = name === q ? 3 : name.startsWith(q) ? 2 : name.includes(q) ? 1 : 0;
      if (s > score) { best = el; score = s; if (s === 3) break; }
    }
    return best;
  }

  function resolve(t = {}) {
    let el = null;
    if (t.ref != null && t.ref !== '') el = fromRef(t.ref);
    else if (t.selector) el = query(t.selector);
    else if (t.role) el = byWords(t.name || '', { roles: [t.role] });
    else if (t.label) el = byWords(t.label, { fields: true });
    else if (t.placeholder) el = [...document.querySelectorAll('[placeholder]')].find((e) => visible(e) && e.placeholder.toLowerCase().includes(t.placeholder.toLowerCase()));
    else if (t.text) el = byWords(t.text);
    else throw new Error('Say which element: ref, text, selector, role, label or placeholder.');
    if (!el) throw new Error(`Nothing on the page matches ${JSON.stringify(t)}`);
    return el;
  }

  // -- acting ---------------------------------------------------------------------------
  function mouse(el, type) {
    const r = el.getBoundingClientRect();
    const init = { bubbles: true, cancelable: true, composed: true, view: window, button: 0,
                   clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
    const Ctor = type.startsWith('pointer') ? PointerEvent : MouseEvent;
    el.dispatchEvent(new Ctor(type, { ...init, pointerType: 'mouse', isPrimary: true }));
  }

  function centre(el) {
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }

  function click(t, { guard = false } = {}) {
    const el = resolve(t);
    const line = lineFor(el);
    const name = nameOf(el);
    // sensitive: clicking it twice could send, pay or delete twice, so nothing ever
    // retries it automatically, guard or no guard.
    const sensitive = RISKY.test(name);
    if (guard && sensitive) return { risky: true, line };
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') throw new Error(`${line} is disabled`);
    el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
    const base = { line, name, sensitive };
    // A link that opens a new tab would be blocked as a popup from a synthetic click;
    // background.js opens it as a tab instead.
    const a = el.closest('a[href]');
    if (a && a.target === '_blank' && /^https?:/.test(a.href)) return { ...base, openUrl: a.href };
    // whatsapp:, mailto:, tel: ... belong to an app, not a page. Chrome won't hand one
    // over from a scripted click, so Nova opens it through Windows instead.
    if (a && /^[a-z][a-z0-9+.-]*:/i.test(a.href) && !/^(https?|javascript|blob|data|about|chrome):/i.test(a.href)) {
      return { ...base, external: a.href };
    }
    if (t.trusted) return { ...base, point: centre(el) };
    for (const type of ['pointerover', 'pointerenter', 'mouseover', 'pointerdown', 'mousedown']) mouse(el, type);
    if (el.focus) el.focus({ preventScroll: true });
    for (const type of ['pointerup', 'mouseup']) mouse(el, type);
    el.click();
    return base;
  }

  function fire(el, type, extra = {}) {
    const Ctor = type === 'input' || type === 'beforeinput' ? InputEvent : Event;
    el.dispatchEvent(new Ctor(type, { bubbles: true, cancelable: type === 'beforeinput', composed: true, ...extra }));
  }

  function pickOption(el, value) {
    const want = String(value).trim().toLowerCase();
    const opts = [...el.options];
    const opt = opts.find((o) => o.value.toLowerCase() === want) ||
                opts.find((o) => o.text.trim().toLowerCase() === want) ||
                opts.find((o) => o.text.toLowerCase().includes(want));
    if (!opt) throw new Error(`"${value}" isn't one of: ${opts.slice(0, 12).map((o) => clean(o.text, 30)).join(', ')}`);
    el.value = opt.value;
    fire(el, 'input');
    fire(el, 'change');
  }

  // Typing the way frameworks expect: through the element's own value setter (so React's
  // value tracker sees a change), then input and change events. Rich editors (Gmail,
  // LinkedIn, Notion) are contenteditable and only listen to real editing commands.
  function setValue(el, value) {
    value = String(value ?? '');
    el.scrollIntoView({ block: 'center', behavior: 'instant' });
    if (el.focus) el.focus({ preventScroll: true });
    const role = roleOf(el);
    if (el.tagName === 'SELECT') return pickOption(el, value);
    if (role === 'checkbox' || role === 'radio' || role === 'switch') {
      const want = /^(true|yes|on|1|x|checked|tick)$/i.test(value.trim());
      const on = el.checked ?? el.getAttribute('aria-checked') === 'true';
      if (on !== want) el.click();
      return;
    }
    if (el.isContentEditable) {
      const range = document.createRange();
      range.selectNodeContents(el);
      const sel = getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      if (!document.execCommand('insertText', false, value)) {
        el.textContent = value;
        fire(el, 'input', { inputType: 'insertText', data: value });
      }
      return;
    }
    if (!('value' in el)) throw new Error(`${lineFor(el)} can't be typed into`);
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    fire(el, 'beforeinput', { inputType: 'insertText', data: value });
    setter.call(el, value);
    fire(el, 'input', { inputType: 'insertText', data: value });
    fire(el, 'change');
  }

  const KEYS = { Enter: 13, Escape: 27, Tab: 9, Backspace: 8, Delete: 46, Space: 32, ' ': 32,
                 ArrowUp: 38, ArrowDown: 40, ArrowLeft: 37, ArrowRight: 39, PageDown: 34, PageUp: 33, Home: 36, End: 35 };

  function key(el, name) {
    const code = KEYS[name] || (name.length === 1 ? name.toUpperCase().charCodeAt(0) : 0);
    const init = { key: name === 'Space' ? ' ' : name, code: name.length === 1 ? `Key${name.toUpperCase()}` : name,
                   keyCode: code, which: code, bubbles: true, cancelable: true, composed: true };
    const down = el.dispatchEvent(new KeyboardEvent('keydown', init));
    if (name === 'Enter' || name.length === 1) el.dispatchEvent(new KeyboardEvent('keypress', init));
    el.dispatchEvent(new KeyboardEvent('keyup', init));
    return down;  // false when the page handled it (preventDefault)
  }

  function submit(el) {
    // A synthetic Enter doesn't trigger the browser's implicit submission, so if no
    // script took the key, submit the form the way the browser would have.
    const untouched = key(el, 'Enter');
    if (untouched && el.form) el.form.requestSubmit();
  }

  function fill(t, value, { submit: enter = false } = {}) {
    const el = resolve(t);
    setValue(el, value);
    if (enter) submit(el);
    return { line: lineFor(el) };
  }

  function form(fields, { submitRef = null, guard = false } = {}) {
    const done = [], failed = [];
    for (const { ref, value } of fields) {
      try { setValue(fromRef(ref), value); done.push(ref); }
      catch (e) { failed.push(`[${ref}] ${e.message}`); }
    }
    let clicked = null;
    if (submitRef != null && !failed.length) {
      const r = click({ ref: submitRef }, { guard });
      if (r.risky) return { done, failed, risky: true, line: r.line };
      clicked = r;
    }
    return { done, failed, clicked };
  }

  function press(name) {
    const el = document.activeElement || document.body;
    if (name === 'Enter' && el !== document.body) submit(el); else key(el, name);
    return { line: el === document.body ? 'the page' : lineFor(el) };
  }

  function scroll(direction = 'down', t = null) {
    if (t && (t.ref != null || t.text || t.selector)) {
      resolve(t).scrollIntoView({ block: 'center', behavior: 'instant' });
    } else {
      const amount = { down: 0.85, up: -0.85, bottom: 1e6, top: -1e6 }[direction] ?? 0.85;
      scrollBy({ top: Math.abs(amount) > 10 ? amount : amount * innerHeight, behavior: 'instant' });
    }
    return snapshot();
  }

  // -- reading text -----------------------------------------------------------------------
  function mainArea() {
    const m = document.querySelector('main, [role=main], article');
    return m && (m.innerText || '').length > 200 ? m : document.body;
  }

  function read({ limit = 3000, ...target } = {}) {
    const el = (target.ref != null || target.selector || target.text) ? resolve(target) : mainArea();
    let s = (el.innerText || el.textContent || '').replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n').trim();
    if (s.length > limit) s = s.slice(0, limit).replace(/\n[^\n]*$/, '') + '\n…';
    return s;
  }

  // The cheap question: "is there a Pay button" or "where does it say the price". Up to
  // eight matching elements, or a few lines of text around the words when no element has them.
  function find(words) {
    const q = clean(words, 200).toLowerCase();
    const parts = q.split(/\s+/).filter(Boolean);
    const hits = [];
    for (const el of walk(document.documentElement)) {
      if (!el.matches(INTERACTIVE) || !visible(el)) continue;
      const name = nameOf(el).toLowerCase();
      if (name && parts.every((p) => name.includes(p))) hits.push(lineFor(el));
      if (hits.length >= 8) break;
    }
    if (hits.length) return hits.join('\n');
    const text = (document.body.innerText || '').replace(/\s+/g, ' ');
    const lower = text.toLowerCase();
    const snippets = [];
    for (let i = lower.indexOf(parts[0] || q); i >= 0 && snippets.length < 4; i = lower.indexOf(parts[0] || q, i + 200)) {
      snippets.push('… ' + text.slice(Math.max(0, i - 80), i + 140).trim() + ' …');
    }
    return snippets.length ? snippets.join('\n') : `Nothing on the page mentions "${words}".`;
  }

  // -- waiting ----------------------------------------------------------------------------
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function waitFor(t, timeout = 15) {
    const end = Date.now() + timeout * 1000;
    for (;;) {
      try { const el = resolve(t); if (visible(el)) return { line: lineFor(el) }; } catch (_) { /* not yet */ }
      if (Date.now() > end) throw new Error(`Waited ${timeout}s and ${JSON.stringify(t)} never appeared`);
      await sleep(150);
    }
  }

  // After an action: wait until the page stops changing (300 ms without a DOM mutation,
  // at most 2.5 s), so the diff shows the result and not half of it.
  function settle(quiet = 300, max = 2500) {
    return new Promise((done) => {
      let timer = setTimeout(finish, quiet);
      const cap = setTimeout(finish, max);
      const obs = new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(finish, quiet); });
      obs.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
      function finish() { obs.disconnect(); clearTimeout(timer); clearTimeout(cap); done(); }
    });
  }

  const commands = {
    snapshot: (a) => ({ page: snapshot(a) }),
    changes: async () => { await settle(); return changes(); },
    click: (a) => click(a.target || {}, a),
    fill: (a) => fill(a.target || {}, a.value, a),
    form: (a) => form(a.fields || [], a),
    press: (a) => press(a.key || 'Enter'),
    scroll: (a) => ({ page: scroll(a.direction, a.target) }),
    read: (a) => ({ text: read(a) }),
    find: (a) => ({ text: find(a.query || '') }),
    wait: (a) => waitFor(a.target || {}, a.timeout),
    title: () => ({ title: document.title, url: location.href }),
  };

  window.__novaAgent = {
    async run(name, args) {
      const fn = commands[name];
      if (!fn) return { error: `unknown page command ${name}` };
      // pageChars: the page's full text, which is what a reader without the map would
      // have been sent. Nova's usage ledger compares the two.
      try { return { ok: true, ...(await fn(args || {})), pageChars: (document.body?.innerText || '').length }; }
      catch (e) { return { error: e.message || String(e) }; }
    },
  };
})();
