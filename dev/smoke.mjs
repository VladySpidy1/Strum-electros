// Smoke test for custom_components/electros_ev/frontend/electros-card.js
//   node dev/smoke.mjs          (Node 22, no npm packages, no network)
// Loads the card + dev/mock-hass.js into a tiny DOM stub, renders every scenario,
// drives the controls and checks the service calls, texts, escaping and re-render behaviour.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');

/* ------------------------------------------------------------ DOM stub */
let mutations = 0;
const innerHTMLLog = [];

class Node {
  constructor() {
    this.childNodes = [];
    this.parentNode = null;
  }

  appendChild(n) {
    if (n.parentNode) n.parentNode.removeChild(n);
    n.parentNode = this;
    this.childNodes.push(n);
    mutations += 1;
    return n;
  }

  removeChild(n) {
    const i = this.childNodes.indexOf(n);
    if (i >= 0) this.childNodes.splice(i, 1);
    n.parentNode = null;
    return n;
  }
}

class Text extends Node {
  constructor(t) {
    super();
    this.nodeType = 3;
    this.data = String(t);
  }

  get textContent() {
    return this.data;
  }

  set textContent(v) {
    this.data = String(v);
  }
}

class Style {
  setProperty(k, v) {
    this[k] = String(v);
    mutations += 1;
  }

  getPropertyValue(k) {
    return this[k] || '';
  }

  removeProperty(k) {
    delete this[k];
  }
}

class ClassList {
  constructor(el) {
    this.el = el;
  }

  _get() {
    return (this.el.getAttribute('class') || '').split(/\s+/).filter(Boolean);
  }

  _set(a) {
    this.el.setAttribute('class', a.join(' '));
  }

  contains(c) {
    return this._get().includes(c);
  }

  add(...cs) {
    const a = this._get();
    for (const c of cs) if (!a.includes(c)) a.push(c);
    this._set(a);
  }

  remove(...cs) {
    this._set(this._get().filter((c) => !cs.includes(c)));
  }

  toggle(c, force) {
    const has = this.contains(c);
    const want = force === undefined ? !has : !!force;
    if (want && !has) this.add(c);
    else if (!want && has) this.remove(c);
    return want;
  }
}

const VOID = new Set(['input', 'br', 'img', 'hr', 'meta', 'link', 'area', 'base', 'col', 'embed', 'source', 'track', 'wbr']);
const decode = (s) => s.replace(/&(amp|lt|gt|quot|#39|nbsp);/g, (_, e) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'", nbsp: ' ' }[e]));

function parseInto(parent, html) {
  const stack = [parent];
  const cur = () => stack[stack.length - 1];
  let i = 0;
  while (i < html.length) {
    if (html.startsWith('<!--', i)) {
      const e = html.indexOf('-->', i);
      i = e < 0 ? html.length : e + 3;
      continue;
    }
    if (html[i] === '<' && html[i + 1] === '/') {
      const e = html.indexOf('>', i);
      const tag = html.slice(i + 2, e).trim().toLowerCase();
      for (let k = stack.length - 1; k > 0; k -= 1) {
        if (stack[k].localName === tag) {
          stack.length = k;
          break;
        }
      }
      i = e + 1;
      continue;
    }
    if (html[i] === '<' && /[a-zA-Z]/.test(html[i + 1] || '')) {
      let j = i + 1;
      const tag = /^[a-zA-Z][\w-]*/.exec(html.slice(j))[0];
      j += tag.length;
      const el = doc.createElement(tag);
      let selfClose = false;
      for (;;) {
        while (/\s/.test(html[j])) j += 1;
        if (html[j] === '>') {
          j += 1;
          break;
        }
        if (html.startsWith('/>', j)) {
          j += 2;
          selfClose = true;
          break;
        }
        const name = /^[^\s=/>]+/.exec(html.slice(j))[0];
        j += name.length;
        while (/\s/.test(html[j])) j += 1;
        let val = '';
        if (html[j] === '=') {
          j += 1;
          while (/\s/.test(html[j])) j += 1;
          const q = html[j];
          if (q === '"' || q === "'") {
            const end = html.indexOf(q, j + 1);
            val = html.slice(j + 1, end);
            j = end + 1;
          } else {
            val = /^[^\s>]+/.exec(html.slice(j))[0];
            j += val.length;
          }
        }
        el.setAttribute(name, decode(val));
      }
      cur().appendChild(el);
      const lt = tag.toLowerCase();
      if (lt === 'style' || lt === 'script') {
        const end = html.indexOf('</' + lt, j);
        el.appendChild(new Text(html.slice(j, end)));
        j = html.indexOf('>', end) + 1;
      } else if (!selfClose && !VOID.has(lt)) {
        stack.push(el);
      }
      i = j;
      continue;
    }
    let e = html.indexOf('<', i + 1);
    if (e < 0) e = html.length;
    const t = html.slice(i, e);
    if (t) cur().appendChild(new Text(decode(t)));
    i = e;
  }
}

function matches(el, sel) {
  const s = sel.trim();
  const re = /([a-zA-Z][\w-]*)|\.([\w-]+)|#([\w-]+)|\[([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]/g;
  let m;
  let pos = 0;
  while ((m = re.exec(s))) {
    if (m.index !== pos) throw new Error('stub: unsupported selector ' + sel);
    pos = re.lastIndex;
    if (m[1] && el.localName !== m[1].toLowerCase()) return false;
    if (m[2] && !el.classList.contains(m[2])) return false;
    if (m[3] && el.getAttribute('id') !== m[3]) return false;
    if (m[4]) {
      if (!el.hasAttribute(m[4])) return false;
      const v = m[5] !== undefined ? m[5] : m[6] !== undefined ? m[6] : m[7];
      if (v !== undefined && el.getAttribute(m[4]) !== v) return false;
    }
  }
  if (pos !== s.length) throw new Error('stub: unsupported selector ' + sel);
  return true;
}

class Element extends Node {
  constructor(tag) {
    super();
    this.nodeType = 1;
    this.localName = String(tag).toLowerCase();
    this.tagName = this.localName.toUpperCase();
    this._attrs = new Map();
    this._ls = {};
    this.style = new Style();
    this.classList = new ClassList(this);
  }

  setAttribute(k, v) {
    this._attrs.set(k, String(v));
    mutations += 1;
  }

  getAttribute(k) {
    return this._attrs.has(k) ? this._attrs.get(k) : null;
  }

  hasAttribute(k) {
    return this._attrs.has(k);
  }

  removeAttribute(k) {
    if (this._attrs.delete(k)) mutations += 1;
  }

  get className() {
    return this.getAttribute('class') || '';
  }

  set className(v) {
    this.setAttribute('class', v);
  }

  get hidden() {
    return this.hasAttribute('hidden');
  }

  set hidden(v) {
    if (v) this.setAttribute('hidden', '');
    else this.removeAttribute('hidden');
  }

  get disabled() {
    return this.hasAttribute('disabled');
  }

  set disabled(v) {
    if (v) this.setAttribute('disabled', '');
    else this.removeAttribute('disabled');
  }

  get value() {
    return this._value !== undefined ? this._value : this.getAttribute('value') || '';
  }

  set value(v) {
    this._value = String(v);
  }

  get children() {
    return this.childNodes.filter((n) => n.nodeType === 1);
  }

  get textContent() {
    return this.childNodes.map((n) => n.textContent).join('');
  }

  set textContent(v) {
    for (const n of this.childNodes) n.parentNode = null;
    this.childNodes = [];
    mutations += 1;
    const s = v === null || v === undefined ? '' : String(v);
    if (s) this.appendChild(new Text(s));
  }

  set innerHTML(html) {
    innerHTMLLog.push(String(html));
    for (const n of this.childNodes) n.parentNode = null;
    this.childNodes = [];
    mutations += 1;
    parseInto(this, String(html));
  }

  replaceChildren(...nodes) {
    for (const n of this.childNodes) n.parentNode = null;
    this.childNodes = [];
    mutations += 1;
    for (const n of nodes) this.appendChild(n);
  }

  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => {
      for (const c of n.childNodes) {
        if (c.nodeType === 1) {
          if (matches(c, sel)) out.push(c);
          walk(c);
        }
      }
    };
    walk(this);
    return out;
  }

  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }

  addEventListener(t, fn) {
    (this._ls[t] = this._ls[t] || []).push(fn);
  }

  removeEventListener(t, fn) {
    this._ls[t] = (this._ls[t] || []).filter((f) => f !== fn);
  }

  dispatchEvent(ev) {
    if (!ev.target) ev.target = this;
    let n = this;
    while (n) {
      ev.currentTarget = n;
      for (const fn of (n._ls && n._ls[ev.type]) || []) fn.call(n, ev);
      if (!ev.bubbles || ev._stopped) break;
      n = n.parentNode || (ev.composed && n.host) || null;
    }
    return !ev.defaultPrevented;
  }

  getRootNode() {
    let n = this;
    while (n.parentNode) n = n.parentNode;
    return n;
  }

  focus() {
    const r = this.getRootNode();
    if (r instanceof ShadowRoot) r.activeElement = this;
  }

  blur() {}

  getBoundingClientRect() {
    return { left: 0, top: 0, width: 322, height: 64, right: 322, bottom: 64 };
  }

  setPointerCapture() {}

  releasePointerCapture() {}

  get offsetWidth() {
    return 0;
  }
}

class ShadowRoot extends Element {
  constructor(host) {
    super('#shadow-root');
    this.host = host;
    this.activeElement = null;
  }
}

class HTMLElement extends Element {
  constructor() {
    super('x-element');
  }

  attachShadow() {
    this.shadowRoot = new ShadowRoot(this);
    return this.shadowRoot;
  }
}

class Ev {
  constructor(type, init = {}) {
    this.type = type;
    this.bubbles = !!init.bubbles;
    this.composed = !!init.composed;
    this.defaultPrevented = false;
    this.target = null;
    this.currentTarget = null;
  }

  stopPropagation() {
    this._stopped = true;
  }

  preventDefault() {
    this.defaultPrevented = true;
  }
}

class CEv extends Ev {
  constructor(t, i = {}) {
    super(t, i);
    this.detail = i.detail === undefined ? null : i.detail;
  }
}

class MEv extends Ev {
  constructor(t, i = {}) {
    super(t, i);
    this.detail = i.detail === undefined ? 0 : i.detail;
    this.clientX = i.clientX || 0;
    this.button = i.button || 0;
    this.pointerId = i.pointerId === undefined ? 1 : i.pointerId;
    this.key = i.key;
  }
}

const defs = new Map();
const doc = {
  documentElement: { lang: 'uk' },
  createElement(tag) {
    const C = defs.get(String(tag).toLowerCase());
    if (C) {
      const el = new C();
      el.localName = String(tag).toLowerCase();
      el.tagName = el.localName.toUpperCase();
      return el;
    }
    return new Element(tag);
  },
};

Object.assign(globalThis, {
  window: globalThis,
  document: doc,
  HTMLElement,
  Event: Ev,
  CustomEvent: CEv,
  MouseEvent: MEv,
  customElements: {
    define(n, c) {
      if (defs.has(n)) throw new Error('already defined ' + n);
      defs.set(n, c);
    },
    get(n) {
      return defs.get(n);
    },
  },
  matchMedia: () => ({ matches: false }),
});

/* -------------------------------------------------------- load scripts */
const errors = [];
const origError = console.error;
console.error = (...a) => {
  errors.push(a.map(String).join(' '));
  origError('  console.error:', ...a);
};
const origInfo = console.info;
console.info = () => {};
vm.runInThisContext(fs.readFileSync(path.join(root, 'dev/mock-hass.js'), 'utf8'), { filename: 'dev/mock-hass.js' });
globalThis.ElectrosMock.installFakeNow('21:18');
const CARD_FILE = path.join(root, 'custom_components/electros_ev/frontend/electros-card.js');
vm.runInThisContext(fs.readFileSync(CARD_FILE, 'utf8'), { filename: 'electros-card.js' });
console.info = origInfo;
const { createMock } = globalThis.ElectrosMock;

/* -------------------------------------------------------------- harness */
const results = [];
async function test(name, fn) {
  const before = errors.length;
  try {
    await fn();
    if (errors.length > before) throw new Error('console.error was called: ' + errors.slice(before).join(' | '));
    results.push([true, name]);
    console.log('PASS ' + name);
  } catch (e) {
    results.push([false, name]);
    console.log('FAIL ' + name + '\n     ' + (e && e.stack ? e.stack.split('\n').slice(0, 3).join('\n     ') : e));
  }
}
function eq(a, b, msg) {
  if (a !== b) throw new Error((msg || 'eq') + ': expected ' + JSON.stringify(b) + ', got ' + JSON.stringify(a));
}
function ok(v, msg) {
  if (!v) throw new Error(msg || 'assertion failed');
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const q = (el, ref) => {
  const n = el.shadowRoot.querySelector('[data-r="' + ref + '"]');
  if (!n) throw new Error('no ref ' + ref);
  return n;
};
const txt = (el, ref) => q(el, ref).textContent;
function shown(el, ref) {
  let n = q(el, ref);
  while (n && n !== el.shadowRoot) {
    if (n.hasAttribute && n.hasAttribute('hidden')) return false;
    n = n.parentNode;
  }
  return true;
}
const click = (node, detail = 1) => node.dispatchEvent(new MEv('click', { bubbles: true, composed: true, detail }));

const ALL = [
  { id: 'dev_charging', login: '10001', scenario: 'charging' },
  { id: 'dev_idle', login: '10002', scenario: 'idle_night' },
  { id: 'dev_waiting', login: '10003', scenario: 'waiting_vehicle' },
  { id: 'dev_blocked', login: '10004', scenario: 'blocked' },
  { id: 'dev_fault', login: '10005', scenario: 'fault' },
  { id: 'dev_offline', login: '10006', scenario: 'offline' },
  { id: 'dev_unlimited', login: '10007', scenario: 'unlimited' },
];
function setup(opts = {}) {
  const mock = createMock(Object.assign({ lang: 'uk', latency: 0, devices: ALL }, opts));
  const els = [];
  mock.subscribe((h) => els.forEach((e) => (e.hass = h)));
  const mk = (tag, config) => {
    const el = doc.createElement(tag);
    el.setConfig(config);
    el.hass = mock.hass;
    els.push(el);
    return el;
  };
  return { mock, mk };
}
const lastCall = (mock) => {
  const calls = mock.log.filter((e) => e.domain);
  return calls[calls.length - 1];
};
const weekend = [0, 6].includes(new Date().getDay());

/* ---------------------------------------------------------------- tests */
await test('registers elements and window.customCards', () => {
  for (const n of ['electros-card', 'electros-tile', 'electros-card-editor', 'electros-tile-editor']) ok(customElements.get(n), n + ' defined');
  const types = window.customCards.map((c) => c.type);
  ok(types.includes('electros-card') && types.includes('electros-tile'), 'customCards types');
  ok(window.customCards.every((c) => c.preview === true && c.description && c.name), 'preview + description');
  const Card = customElements.get('electros-card');
  ok(typeof Card.getConfigElement === 'function' && typeof Card.getStubConfig === 'function', 'static API');
});

await test('getStubConfig picks a device of the integration, never throws', () => {
  const { mock } = setup();
  const Card = customElements.get('electros-card');
  const Tile = customElements.get('electros-tile');
  ok(ALL.some((d) => d.id === Card.getStubConfig(mock.hass).device_id), 'device id from registry');
  eq(Tile.getStubConfig(mock.hass).size, 'medium');
  eq(Card.getStubConfig({ entities: {} }).device_id, undefined);
  eq(Card.getStubConfig(undefined).device_id, undefined);
  const devOnly = { entities: {}, devices: { d1: { identifiers: [['electros_ev', 'x']] } } };
  eq(Card.getStubConfig(devOnly).device_id, 'd1');
});

await test('grid options and card size', () => {
  const { mk } = setup();
  const card = mk('electros-card', { device_id: 'dev_charging' });
  const s = mk('electros-tile', { device_id: 'dev_charging', size: 'small' });
  const m = mk('electros-tile', { device_id: 'dev_charging', size: 'medium' });
  ok(card.getCardSize() > 5, 'card size');
  eq(s.getCardSize(), 3);
  eq(s.getGridOptions().columns, 6);
  eq(m.getGridOptions().columns, 12);
  eq(card.getGridOptions().rows, 'auto');
});

await test('charging + 30-min boost (dark) renders like Main.dc', () => {
  const { mk } = setup();
  const el = mk('electros-card', { device_id: 'dev_charging', theme: 'dark', price_per_kwh: 2.16 });
  ok(q(el, 'card').classList.contains('dark'), 'dark class');
  eq(txt(el, 'pillText'), 'Заряджається');
  ok(q(el, 'pill').classList.contains('green'), 'green pill');
  ok(q(el, 'pillDot').classList.contains('pulse'), 'pulsing dot');
  eq(txt(el, 'ringValue'), '7,4');
  eq(txt(el, 'ringUnit'), 'кВт');
  eq(txt(el, 'ringSub'), '32 А · 231 В');
  ok(shown(el, 'ringProg') && shown(el, 'ringDot') && !shown(el, 'ringDash'), 'progress ring with dot');
  const off = Number(q(el, 'ringProg').getAttribute('stroke-dashoffset'));
  ok(off > 60 && off < 75, 'ring ≈ 27/30 remaining, offset ' + off);
  ok(shown(el, 'count'), 'countdown shown');
  ok(/^27:1[0-4]$/.test(txt(el, 'countBig')), 'countdown ' + txt(el, 'countBig'));
  eq(txt(el, 'countSub'), 'до зупинки о 21:45');
  ok(shown(el, 'plus'), '+15 shown');
  eq(txt(el, 'plus'), '+15 хв');
  ok(shown(el, 'stats'), 'stats');
  eq(txt(el, 's1v'), '4,2');
  eq(txt(el, 's2v'), '0:34');
  ok(txt(el, 's3v').includes('9,07'), 'cost ' + txt(el, 's3v'));
  ok(shown(el, 'mainBtn') && !shown(el, 'slide'), 'stop button, no slide');
  eq(txt(el, 'mainLabel'), 'Зупинити');
  ok(q(el, 'mainBtn').classList.contains('stop'), 'stop style');
  const chips = q(el, 'chips').children;
  eq(chips.map((c) => c.textContent).join('|'), '15 хв|30 хв|1 год|2 год|∞');
  eq(chips.filter((c) => c.getAttribute('aria-pressed') === 'true').map((c) => c.textContent).join(), '30 хв');
  ok(shown(el, 'bypass'), 'orange bypass note');
  eq(txt(el, 'bypassText'), 'Заряджаємо поза розкладом. Нічний таймер повернеться сам о 21:45.');
  eq(txt(el, 'nightLine'), '23:00 → 07:00 · старт через 1 год 42 хв');
  eq(q(el, 'nightSw').getAttribute('aria-checked'), 'true');
  ok(Math.abs(parseFloat(q(el, 'tlNow').style.left) - 88.75) < 0.2, 'now marker ' + q(el, 'tlNow').style.left);
  eq(q(el, 'segA').style.left, '95.83%');
  eq(q(el, 'segB').style.width, '29.17%');
  ok(txt(el, 'energySub').startsWith('жовтень · 84,5 кВт·год · 183'), 'energy sub ' + txt(el, 'energySub'));
  const bars = q(el, 'bars').children;
  eq(bars.length, 6);
  eq(q(el, 'barsLbl').children.map((s) => s.textContent).join('|'), '17.10|20.10|23.10|24.10|25.10|зараз');
  ok(bars[5].classList.contains('hi') && !bars[4].classList.contains('hi'), 'live bar highlighted');
  eq(txt(el, 'ampsVal'), '32');
  eq(txt(el, 'ampsKw'), '≈ 7,4 кВт');
  eq(q(el, 'slTicks').children.length, 14);
  eq(txt(el, 'area'), 'Гараж');
  eq(txt(el, 'title'), 'ElectroS');
});

await test('connected idle waiting for 23:00 (light) renders like Idle.dc', () => {
  const { mk } = setup();
  const el = mk('electros-card', { device_id: 'dev_idle', theme: 'light', price_per_kwh: 2.16 });
  ok(q(el, 'card').classList.contains('light'), 'light class');
  eq(txt(el, 'pillText'), 'Чекає нічного тарифу');
  ok(q(el, 'pill').classList.contains('purple') && shown(el, 'pillIcon') && !shown(el, 'pillDot'), 'purple moon pill');
  eq(txt(el, 'ringValue'), '23:00');
  eq(txt(el, 'ringSub'), 'старт через 1 год 42 хв');
  ok(q(el, 'ring').classList.contains('t-time') && shown(el, 'ringDash') && !shown(el, 'ringProg'), 'dashed time ring');
  ok(shown(el, 'hint'), '"Потрібно зараз?" hint');
  eq(txt(el, 'hintTitle'), 'Потрібно зараз?');
  ok(shown(el, 'slide') && !shown(el, 'mainBtn'), 'slide to charge');
  eq(txt(el, 'slideLabel'), 'Проведіть, щоб зарядити');
  ok(q(el, 'slideKnob').getAttribute('aria-label').includes('30 хв'), 'slide aria-label');
  eq(txt(el, 'chargeSub'), 'до 21:48');
  eq(txt(el, 'nightLine'), '23:00 → 07:00 · щодня');
  ok(!shown(el, 'count') && !shown(el, 'stats') && !shown(el, 'bypass'), 'no charging-only blocks');
  eq(q(el, 'barsLbl').children.length, 5);
  ok(shown(el, 'wkRow'), 'weekend row');
  eq(txt(el, 'wkLabel'), 'Вихідні без обмежень');
});

await test('waiting for vehicle / blocked / fault / offline states', () => {
  const { mk } = setup();
  const w = mk('electros-card', { device_id: 'dev_waiting' });
  eq(txt(w, 'pillText'), 'Чекаю авто');
  eq(txt(w, 'ringValue'), 'Підключіть кабель');
  eq(txt(w, 'ringSub'), 'нічний тариф з 23:00');
  ok(!shown(w, 'hint'), 'no hint without a car');

  const b = mk('electros-card', { device_id: 'dev_blocked' });
  eq(txt(b, 'pillText'), 'Заблоковано таймером');
  ok(q(b, 'pill').classList.contains('purple'), 'blocked is purple');
  ok(shown(b, 'slide') && shown(b, 'hint'), 'blocked offers charge-now');
  if (!weekend) {
    eq(txt(b, 'ringValue'), '23:00');
    ok(txt(b, 'nightLine').includes('по буднях'), 'weekday line ' + txt(b, 'nightLine'));
  }
  eq(q(b, 'wkSw').getAttribute('aria-checked'), 'true');

  const f = mk('electros-card', { device_id: 'dev_fault' });
  eq(txt(f, 'pillText'), 'Витік струму');
  ok(q(f, 'pill').classList.contains('red'), 'fault is red');
  ok(q(f, 'ring').classList.contains('fault'), 'red ring');
  eq(txt(f, 'ringValue'), 'Зупинено');
  eq(txt(f, 'ringSub'), 'Спрацював захист від витоку');
  eq(txt(f, 'mainLabel'), 'Зарядити 30 хв');
  ok(!q(f, 'mainBtn').disabled, 'retry allowed');
  eq(txt(f, 'nightLine'), 'вимкнено — можна заряджати будь-коли');

  const o = mk('electros-card', { device_id: 'dev_offline' });
  eq(txt(o, 'pillText'), 'Офлайн');
  ok(q(o, 'pill').classList.contains('neutral'), 'offline grey');
  eq(txt(o, 'ringValue'), "Немає зв'язку");
  eq(txt(o, 'ringSub'), 'офлайн з 21:04');
  ok(q(o, 'mainBtn').disabled && q(o, 'slIn').disabled && q(o, 'nightSw').disabled, 'controls disabled offline');
  ok(q(o, 'chips').children.every((c) => c.disabled), 'chips disabled offline');
});

await test('every contract status x uk/ru/en renders a translated pill; unavailable -> offline', () => {
  const statuses = ['waiting_for_vehicle', 'vehicle_connected_idle', 'charging', 'charging_fault', 'charging_blocked',
    'mains_voltage_low', 'charger_communication_fault', 'offline', 'leakage_current_fault', 'overcurrent_fault', 'unknown'];
  for (const lang of ['uk', 'ru', 'en']) {
    const { mock, mk } = setup({ lang });
    const card = mk('electros-card', { device_id: 'dev_idle' });
    const s = mk('electros-tile', { device_id: 'dev_idle', size: 'small' });
    const m = mk('electros-tile', { device_id: 'dev_idle', size: 'medium' });
    const id = mock.entityId('dev_idle', 'sensor', 'status');
    const seen = new Set();
    for (const st of statuses) {
      mock.setState(id, st);
      const p = txt(card, 'pillText');
      ok(p && !/^(st_|fh_)/.test(p), lang + ' ' + st + ' pill "' + p + '"');
      ok(!/(st_|fh_)/.test(txt(card, 'ringSub') + txt(s, 'sSub') + txt(m, 'mSub') + txt(m, 'mPillText')), lang + ' ' + st + ' untranslated');
      seen.add(p);
      if (['charging_fault', 'mains_voltage_low', 'charger_communication_fault', 'leakage_current_fault', 'overcurrent_fault'].includes(st)) {
        ok(q(card, 'pill').classList.contains('red'), st + ' red');
      }
    }
    ok(seen.size >= 10, lang + ' distinct labels ' + seen.size);
    mock.setState(id, 'unavailable');
    eq(txt(card, 'pillText'), { uk: 'Офлайн', ru: 'Офлайн', en: 'Offline' }[lang]);
    mock.setState(id, 'something_new');
    eq(txt(card, 'pillText'), { uk: 'Невідомо', ru: 'Неизвестно', en: 'Unknown' }[lang]);
  }
});

await test('language by hass.locale.language (ru, en, fallback uk)', () => {
  const ru = setup({ lang: 'ru' }).mk('electros-card', { device_id: 'dev_charging' });
  eq(txt(ru, 'pillText'), 'Заряжается');
  eq(q(ru, 'chips').children[0].textContent, '15 мин');
  const en = setup({ lang: 'en' }).mk('electros-card', { device_id: 'dev_charging' });
  eq(txt(en, 'pillText'), 'Charging');
  eq(q(en, 'chips').children[2].textContent, '1 h');
  eq(txt(en, 'countSub'), 'stops at 21:45');
  const de = setup({ lang: 'de' }).mk('electros-card', { device_id: 'dev_charging' });
  eq(txt(de, 'pillText'), 'Заряджається');
});

await test('actions: stop, +15, chips, slide (keyboard), ∞ -> switch.turn_on', async () => {
  const { mock, mk } = setup();
  const c = mk('electros-card', { device_id: 'dev_charging' });
  click(q(c, 'plus'));
  eq(lastCall(mock).domain + '.' + lastCall(mock).service, 'electros_ev.extend_charge');
  eq(JSON.stringify(lastCall(mock).data), JSON.stringify({ device_id: 'dev_charging', minutes: 15 }));
  ok(/^42:1\d$/.test(txt(c, 'countBig')), 'extended countdown ' + txt(c, 'countBig'));
  // chip while charging re-times the session (mock-up behaviour)
  click(q(c, 'chips').children[2]);
  eq(lastCall(mock).service, 'charge_for');
  eq(lastCall(mock).data.minutes, 60);
  click(q(c, 'mainBtn'));
  eq(lastCall(mock).service, 'stop_charging');
  eq(lastCall(mock).data.device_id, 'dev_charging');
  eq(txt(c, 'pillText'), 'Чекає нічного тарифу', 'after stop the timer waits again');
  ok(shown(c, 'slide'), 'slide after stop');

  const i = mk('electros-card', { device_id: 'dev_idle' });
  const n = mock.log.length;
  click(q(i, 'chips').children[2]); // 1 год, not charging -> select only
  eq(mock.log.length, n, 'selecting a chip while idle calls nothing');
  eq(txt(i, 'chargeSub'), 'до 22:18');
  click(q(i, 'slideKnob'), 1); // plain mouse click must not start (needs a slide)
  eq(mock.log.length, n, 'tap on knob does not start');
  click(q(i, 'slideKnob'), 0); // Enter / Space
  eq(lastCall(mock).service, 'charge_for');
  eq(lastCall(mock).data.minutes, 60);
  eq(txt(i, 'pillText'), 'Заряджається');

  const { mock: m2, mk: mk2 } = setup();
  const u = mk2('electros-card', { device_id: 'dev_idle' });
  click(q(u, 'chips').children[4]); // ∞
  eq(txt(u, 'chargeSub'), 'до зупинки вручну');
  click(q(u, 'slideKnob'), 0);
  eq(lastCall(m2).domain, 'switch');
  eq(lastCall(m2).service, 'turn_on');
  eq(lastCall(m2).data.entity_id, 'switch.electros_dev_idle_charging');
  eq(txt(u, 'countBig'), 'Без ліміту');
  eq(txt(u, 'bypassText'), 'Заряджаємо поза розкладом. Нічний таймер повернеться сам після зупинки.');
});

await test('slide-to-charge with pointer drag to the end', () => {
  const { mock, mk } = setup();
  const el = mk('electros-card', { device_id: 'dev_idle' });
  const k = q(el, 'slideKnob');
  const n = mock.log.length;
  k.dispatchEvent(new MEv('pointerdown', { clientX: 10, pointerId: 7 }));
  k.dispatchEvent(new MEv('pointermove', { clientX: 120, pointerId: 7 }));
  ok(k.style.transform === 'translateX(110px)', 'knob follows pointer ' + k.style.transform);
  k.dispatchEvent(new MEv('pointerup', { clientX: 120, pointerId: 7 }));
  eq(mock.log.length, n, 'half-way release does not start');
  eq(k.style.transform, 'translateX(0px)');
  k.dispatchEvent(new MEv('pointerdown', { clientX: 10, pointerId: 8 }));
  k.dispatchEvent(new MEv('pointermove', { clientX: 400, pointerId: 8 }));
  k.dispatchEvent(new MEv('pointerup', { clientX: 400, pointerId: 8 }));
  eq(lastCall(mock).service, 'charge_for');
  eq(lastCall(mock).data.minutes, 30);
});

await test('switches, amps slider (debounced, not fought while dragging), night window editor', async () => {
  const { mock, mk } = setup({ latency: 40 });
  const el = mk('electros-card', { device_id: 'dev_idle' });
  click(q(el, 'nightSw'));
  eq(q(el, 'nightSw').getAttribute('aria-checked'), 'false', 'optimistic night off');
  eq(lastCall(mock).service, 'toggle');
  eq(lastCall(mock).data.entity_id, 'switch.electros_dev_idle_night_timer');
  eq(txt(el, 'pillText'), 'Авто підключено', 'timer off -> plain connected');
  ok(shown(el, 'mainBtn') && !shown(el, 'slide'), 'no bypass -> plain start button');
  await sleep(60);
  eq(q(el, 'nightSw').getAttribute('aria-checked'), 'false', 'confirmed by hass');
  click(q(el, 'wkSw'));
  eq(lastCall(mock).data.entity_id, 'switch.electros_dev_idle_weekend_free');
  eq(q(el, 'wkSw').getAttribute('aria-checked'), 'true');
  await sleep(60);

  const inp = q(el, 'slIn');
  inp.dispatchEvent(new MEv('pointerdown'));
  inp.value = '20';
  inp.dispatchEvent(new Ev('input'));
  eq(txt(el, 'ampsVal'), '20');
  mock.setState(mock.entityId('dev_idle', 'number', 'charging_amps'), 10);
  eq(txt(el, 'ampsVal'), '20', 'hass update does not move the thumb during drag');
  eq(inp.value, '20');
  const before = mock.log.length;
  inp.dispatchEvent(new Ev('change'));
  eq(mock.log.length, before, 'debounced');
  await sleep(450);
  eq(lastCall(mock).domain, 'number');
  eq(lastCall(mock).data.value, 20);
  eq(lastCall(mock).data.entity_id, 'number.electros_dev_idle_charging_amps');
  await sleep(60);
  eq(txt(el, 'ampsVal'), '20');

  click(q(el, 'nightLine'));
  ok(shown(el, 'nightEdit'), 'inline editor opened');
  eq(q(el, 'nightLine').getAttribute('aria-expanded'), 'true');
  eq(q(el, 'tStart').value, '23:00');
  eq(q(el, 'tEnd').value, '07:00');
  q(el, 'tStart').value = '22:30';
  q(el, 'tStart').dispatchEvent(new Ev('change'));
  eq(q(el, 'segA').style.left, '93.75%', 'optimistic timeline');
  click(q(el, 'nightSw')); // timer back on -> line shows the (pending) window
  ok(txt(el, 'nightLine').startsWith('22:30 → 07:00'), 'optimistic window ' + txt(el, 'nightLine'));
  eq(txt(el, 'ringValue'), '22:30');
  await sleep(760);
  eq(lastCall(mock).domain, 'time');
  eq(JSON.stringify(lastCall(mock).data), JSON.stringify({ entity_id: 'time.electros_dev_idle_timer_start', time: '22:30:00' }));
  click(q(el, 'nightDone'));
  ok(!shown(el, 'nightEdit'), 'editor closed');
  await sleep(60);
});

await test('tiles: small/medium content and actions', () => {
  const { mock, mk } = setup();
  const s = mk('electros-tile', { device_id: 'dev_charging', size: 'small', theme: 'dark' });
  ok(shown(s, 'small') && !shown(s, 'medium'), 'small layout');
  eq(txt(s, 'sVal'), '7,4');
  eq(txt(s, 'sFootText'), 'ще 27 хв');
  const md = mk('electros-tile', { device_id: 'dev_charging', size: 'medium', theme: 'light' });
  ok(q(md, 'card').classList.contains('light') && q(md, 'card').classList.contains('medium'), 'medium light');
  eq(txt(md, 'mKw'), '7,4');
  eq(txt(md, 'mLine'), 'до 21:45 · 4,2 кВт·год · 32 А');
  ok(shown(md, 'mPlus'), '+15 on tile');
  click(q(md, 'mPlus'));
  eq(lastCall(mock).service, 'extend_charge');
  click(q(md, 'mStop'));
  eq(lastCall(mock).service, 'stop_charging');

  const w = mk('electros-tile', { device_id: 'dev_waiting', size: 'small' });
  eq(txt(w, 'sBig'), 'Чекаю авто');
  eq(txt(w, 'sSub'), 'кабель не підключено');
  eq(txt(w, 'sFootText'), '23:00 → 07:00');
  const i = mk('electros-tile', { device_id: 'dev_idle', size: 'medium', quick_minutes: [15, 30, 60] });
  eq(txt(i, 'mPillText'), 'Чекає нічного тарифу');
  eq(txt(i, 'mRight'), 'авто підключено');
  eq(txt(i, 'mBig'), '23:00');
  eq(txt(i, 'mSub'), 'через 1 год 42 хв');
  eq(txt(i, 'mGoT'), 'Зарядити 30 хв');
  click(q(i, 'mOther'));
  eq(txt(i, 'mGoT'), 'Зарядити 1 год');
  click(q(i, 'mOther'));
  eq(txt(i, 'mGoT'), 'Почати зарядку');
  click(q(i, 'mOther'));
  click(q(i, 'mGo'));
  eq(lastCall(mock).service, 'charge_for');
  eq(lastCall(mock).data.minutes, 15);
  let info = null;
  s.addEventListener('hass-more-info', (e) => (info = e.detail));
  click(q(s, 'small'));
  eq(info && info.entityId, 'sensor.electros_dev_charging_status');
  const f = mk('electros-tile', { device_id: 'dev_fault', size: 'medium' });
  ok(q(f, 'mInfo2').classList.contains('col') && q(f, 'mBig').classList.contains('red'), 'fault stacked + red');
  const o = mk('electros-tile', { device_id: 'dev_offline', size: 'medium' });
  ok(q(o, 'mGo').disabled, 'offline tile disabled');
});

await test('empty states never throw', () => {
  const { mock, mk } = setup();
  const a = mk('electros-card', {});
  ok(shown(a, 'empty') && !shown(a, 'main'), 'empty shown');
  eq(txt(a, 'emptyTitle'), 'Оберіть зарядку');
  const b = mk('electros-card', { device_id: 'nope' });
  eq(txt(b, 'emptyTitle'), 'Зарядку не знайдено');
  const t = mk('electros-tile', { device_id: 'nope', size: 'small' });
  eq(txt(t, 'emptyTitle'), 'Зарядку не знайдено');
  const c = doc.createElement('electros-card');
  c.setConfig(null);
  c.hass = undefined;
  c.hass = { states: {} };
  c.setConfig({ device_id: 'dev_idle', quick_minutes: 'abc', theme: 'neon', price_per_kwh: 'x' });
  c.hass = { states: mock.hass.states };
  eq(txt(c, 'emptyTitle'), 'Зарядку не знайдено', 'no entity registry');
  // garbage attributes
  mock.setState(mock.entityId('dev_idle', 'sensor', 'session_energy'), 'abc', { history: 'nope', started_at: 'x' });
  mock.setState(mock.entityId('dev_idle', 'sensor', 'month_energy'), 'unavailable', { months: 'x', current_month: 99 });
  mock.setState(mock.entityId('dev_idle', 'sensor', 'boost_end'), 'garbage', { active: 'yes' });
  mock.setState(mock.entityId('dev_idle', 'time', 'timer_start'), '99:99');
  mock.setState(mock.entityId('dev_idle', 'number', 'charging_amps'), 'unavailable', { min: 'a', max: null });
  const g = mk('electros-card', { device_id: 'dev_idle' });
  ok(shown(g, 'main'), 'still renders');
  eq(txt(g, 'barsEmpty'), 'Ще немає сесій зарядки');
  ok(q(g, 'slIn').disabled, 'unavailable number disabled');
});

await test('entity strings are escaped (textContent only), static innerHTML only', () => {
  const { mock, mk } = setup();
  const evil = '<img src=x onerror=alert(1)>';
  mock.patchDevice('dev_idle', { name_by_user: evil });
  mock.H.areas.garage.name = '<marquee>area</marquee>';
  mock.setState(mock.entityId('dev_idle', 'sensor', 'session_energy'), 1, {
    history: [{ date: '<script>alert(2)</script>', time: 'x', kwh: 3 }, { date: '"><svg onload=alert(3)>', kwh: 'NaN' }],
  });
  const startLog = innerHTMLLog.length;
  const el = mk('electros-card', { device_id: 'dev_idle' });
  const t = mk('electros-tile', { device_id: 'dev_idle', size: 'medium' });
  eq(txt(el, 'title'), evil);
  eq(txt(el, 'area'), '<marquee>area</marquee>');
  ok(q(el, 'barsLbl').children.some((s) => s.textContent === '<script>alert(2)</script>'), 'history label as text');
  const tags = new Set();
  const handlers = [];
  const walk = (n) => n.childNodes.forEach((c) => {
    if (c.nodeType === 1) {
      tags.add(c.localName);
      for (const k of c._attrs.keys()) if (/^on/i.test(k)) handlers.push(c.localName + '[' + k + ']');
      walk(c);
    }
  });
  walk(el.shadowRoot);
  walk(t.shadowRoot);
  ok(!tags.has('img') && !tags.has('script') && !tags.has('marquee'), 'no injected elements: ' + [...tags].join(','));
  eq(handlers.length, 0, 'no inline handlers ' + handlers.join(','));
  const html = innerHTMLLog.slice(startLog).join('\n');
  ok(!/onerror|onload|alert\(|marquee/.test(html), 'innerHTML never carries entity text');
});

await test('re-render is incremental: identical hass -> no DOM writes; chips/bars kept', () => {
  const { mock, mk } = setup();
  const el = mk('electros-card', { device_id: 'dev_charging' });
  const chip0 = q(el, 'chips').children[0];
  const bar0 = q(el, 'bars').children[0];
  const h = mock.hass;
  mutations = 0;
  el.hass = Object.assign({}, h);
  eq(mutations, 0, 'no writes for an unchanged hass');
  mock.setState(mock.entityId('dev_charging', 'sensor', 'power'), 6100);
  eq(txt(el, 'ringValue'), '6,1');
  ok(mutations < 40, 'small patch, writes=' + mutations);
  ok(q(el, 'chips').children[0] === chip0 && q(el, 'bars').children[0] === bar0, 'lists not rebuilt');
  mutations = 0;
  mock.setState('sensor.some_other_integration', '42');
  eq(mutations, 0, 'unrelated entity change -> no writes');
});

await test('countdown ticks from sensor.boost_end; timer starts/stops with the element', async () => {
  const { mock, mk } = setup();
  const el = mk('electros-card', { device_id: 'dev_charging' });
  mock.setState(mock.entityId('dev_charging', 'sensor', 'boost_end'), new Date(Date.now() + 65500).toISOString(), { minutes: 30, active: true });
  eq(txt(el, 'countBig'), '1:05');
  el.connectedCallback();
  await sleep(1100);
  eq(txt(el, 'countBig'), '1:04');
  el.disconnectedCallback();
  ok(!el._timer, 'interval cleared');
});

await test('theme auto follows hass.themes.darkMode; reduced motion CSS present', () => {
  const { mock, mk } = setup({ darkMode: false });
  const el = mk('electros-card', { device_id: 'dev_idle', theme: 'auto' });
  ok(q(el, 'card').classList.contains('light'), 'auto -> light');
  el.hass = Object.assign({}, mock.hass, { themes: { darkMode: true } });
  ok(q(el, 'card').classList.contains('dark'), 'auto -> dark');
  const css = el.shadowRoot.querySelector('style').textContent;
  ok(css.includes('prefers-reduced-motion'), 'reduced motion');
});

await test('editor uses ha-form with the documented schema and emits config-changed', () => {
  const { mock } = setup();
  const ed = doc.createElement('electros-card-editor');
  ed.setConfig({ type: 'custom:electros-card', device_id: 'dev_idle', quick_minutes: [15, 30] });
  ed.hass = mock.hass;
  const form = ed.children[0];
  eq(form.localName, 'ha-form');
  eq(form.schema.map((s) => s.name).join(','), 'device_id,price_per_kwh,theme,quick_minutes');
  eq(form.schema[0].selector.device.integration, 'electros_ev');
  eq(form.schema[0].selector.device.filter.integration, 'electros_ev');
  eq(JSON.stringify(form.data.quick_minutes), JSON.stringify(['15', '30']));
  eq(form.computeLabel({ name: 'theme' }), 'Тема');
  let cfg = null;
  ed.addEventListener('config-changed', (e) => (cfg = e.detail.config));
  form.dispatchEvent(new CEv('value-changed', { detail: { value: { device_id: 'dev_x', price_per_kwh: '2.5', theme: 'dark', quick_minutes: ['45', '15', 'abc'] } } }));
  eq(JSON.stringify(cfg), JSON.stringify({ type: 'custom:electros-card', device_id: 'dev_x', quick_minutes: [15, 45], price_per_kwh: 2.5, theme: 'dark' }));
  form.dispatchEvent(new CEv('value-changed', { detail: { value: { device_id: 'dev_x', theme: 'auto', quick_minutes: [] } } }));
  ok(!('price_per_kwh' in cfg) && cfg.quick_minutes.length === 4, 'price cleared, default chips');
  const te = doc.createElement('electros-tile-editor');
  te.setConfig({ device_id: 'dev_idle', size: 'small' });
  te.hass = mock.hass;
  ok(te.children[0].schema.some((s) => s.name === 'size'), 'tile editor has size');
  eq(te.children[0].data.size, 'small');
});

await test('window maths: wrap past midnight, weekend, durations', () => {
  const I = customElements.get('electros-card')._internals;
  ok(I.inWindow(23 * 60 + 30, 1380, 420) && I.inWindow(60, 1380, 420) && !I.inWindow(21 * 60, 1380, 420), 'wraps');
  ok(I.inWindow(600, 540, 1020) && !I.inWindow(1100, 540, 1020), 'same-day window');
  eq(I.fmtDur('uk', 102), '1 год 42 хв');
  eq(I.fmtDur('ru', 60), '1 ч');
  eq(I.fmtDur('en', 5), '5 min');
  eq(I.fmtCountdown(3725), '1:02:05');
  eq(I.parseHM('07:00:00'), 420);
  eq(I.parseHM('25:00'), null);
  const cfg = I.normalizeConfig({ quick_minutes: [120, '15', 15, 0, 2000, 30, 60, 90, 45] });
  eq(JSON.stringify(cfg.quick_minutes), JSON.stringify([15, 30, 45, 60, 90]));
});

await test('card source: classic script, no imports / network / CDN', () => {
  const src = fs.readFileSync(CARD_FILE, 'utf8');
  ok(!/^\s*(import|export)\s/m.test(src), 'no ES module syntax');
  ok(!/\bfetch\s*\(|XMLHttpRequest|new\s+WebSocket|import\s*\(|sendBeacon|EventSource/.test(src), 'no network APIs');
  ok(!/<script|<link|@import|cdn\.|unpkg|jsdelivr|googleapis/i.test(src), 'no external resources');
  ok(!/\blit\b|LitElement/.test(src), 'plain HTMLElement, no lit');
  const urls = src.match(/https?:\/\/[^\s'"`)]+/g) || [];
  ok(urls.every((u) => u.startsWith('https://github.com/podzolkov/')), 'only the docs URL: ' + urls.join(' '));
});

const failed = results.filter((r) => !r[0]);
console.log('\n' + (results.length - failed.length) + '/' + results.length + ' passed');
process.exit(failed.length ? 1 : 0);
