// Shared UI helpers. Text is always inserted as text nodes, never parsed as HTML:
// news titles and agent evidence come from outside sources.

export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'style') Object.assign(el.style, v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  add(el, kids);
  return el;
}

function add(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}

const SVGNS = 'http://www.w3.org/2000/svg';
export function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v); else el.setAttribute(k, v);
  }
  add(el, kids);
  return el;
}

// 16px stroke icons (simple paths, no external icon font)
const ICONS = {
  overview: 'M3 3h7v7H3zM14 3h7v4h-7zM14 11h7v10h-7zM3 14h7v7H3z',
  markets: 'M3 17l5-6 4 3 6-8 3 3M3 21h18',
  execution: 'M4 7h12l-3-3M20 17H8l3 3',
  agents: 'M12 3v3M12 18v3M3 12h3M18 12h3M7 7l2 2M15 15l2 2M17 7l-2 2M9 15l-2 2M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  risk: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z',
  brain: 'M12 5a3 3 0 0 0-5.5-1.5A3 3 0 0 0 4 8a3 3 0 0 0 0 5 3 3 0 0 0 3 4.5A3 3 0 0 0 12 19zM12 5a3 3 0 0 1 5.5-1.5A3 3 0 0 1 20 8a3 3 0 0 1 0 5 3 3 0 0 1-3 4.5A3 3 0 0 1 12 19zM12 5v14',
  research: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  news: 'M4 5h13v14H6a2 2 0 0 1-2-2zM17 9h3v8a2 2 0 0 1-2 2M8 9h5M8 13h5',
  activity: 'M3 12h4l3-8 4 16 3-8h4',
  settings: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM19 12l2-1-1-3-2 .3-1.5-1.5L17 4l-3-1-1 2h-2L10 3 7 4l.3 2L6 7.5 4 7.3 3 10l2 1v2l-2 1 1 3 2-.3 1.5 1.5L7 20l3 1 1-2h2l1 2 3-1-.3-2 1.5-1.5 2 .3 1-3-2-1z',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5',
  bell: 'M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0',
  menu: 'M4 6h16M4 12h16M4 18h16',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
  close: 'M6 6l12 12M18 6L6 18',
  minus: 'M5 12h14',
  pin: 'M9 3h6l-1 6 4 4H6l4-4zM12 13v8',
  eyeoff: 'M3 3l18 18M10.6 6.1A9 9 0 0 1 21 12a13 13 0 0 1-2.4 3.2M6.5 6.6A13 13 0 0 0 3 12s3 7 9 7a8.7 8.7 0 0 0 4-1',
  send: 'M4 12l16-8-6 16-2-6z',
  bolt: 'M13 3L5 13h6l-1 8 8-10h-6z',
  ext: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
};
export function icon(name) {
  return s('svg', { viewBox: '0 0 24 24', 'aria-hidden': 'true' }, s('path', { d: ICONS[name] || '' }));
}

// ------------------------------------------------------------ formatting
const nf = (d) => new Intl.NumberFormat(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
export const fmt = {
  num(v, d = 2) { return v == null || Number.isNaN(v) ? '—' : nf(d).format(v); },
  money(v, ccy = '', d = 2) { return v == null ? '—' : `${nf(d).format(v)}${ccy ? ' ' + ccy : ''}`; },
  pct(v, d = 2, sign = false) { return v == null ? '—' : `${sign && v > 0 ? '+' : ''}${nf(d).format(v * 100)}%`; },
  signed(v, d = 2) { return v == null ? '—' : `${v > 0 ? '+' : ''}${nf(d).format(v)}`; },
  time(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(+d) ? String(iso) : d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  },
  date(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(+d)) return String(iso);
    const sameYear = d.getUTCFullYear() === new Date().getUTCFullYear();
    return d.toLocaleDateString(undefined, { year: sameYear ? undefined : 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' });
  },
  ago(iso) {
    if (!iso) return '—';
    const sec = (Date.now() - new Date(iso)) / 1000;
    if (sec < 0) return 'in ' + fmt.dur(-sec);
    return fmt.dur(sec) + ' ago';
  },
  dur(sec) {
    if (sec < 60) return `${Math.round(sec)}s`;
    if (sec < 3600) return `${Math.round(sec / 60)}m`;
    if (sec < 86400) return `${(sec / 3600).toFixed(1)}h`;
    return `${Math.round(sec / 86400)}d`;
  },
  tone(v) { return v == null ? '' : v > 0 ? 'pos' : v < 0 ? 'neg' : ''; },
};
// timestamps: shown in local time, with the UTC ISO value in the tooltip
export function when(iso, mode = 'time') {
  return h('span', { class: 'nowrap', title: iso ? `${iso} (UTC)` : '' }, mode === 'ago' ? fmt.ago(iso) : mode === 'date' ? fmt.date(iso) : fmt.time(iso));
}

// ------------------------------------------------------------ api
const TOKEN = document.querySelector('meta[name="ti-token"]')?.content || '';

export class ApiError extends Error {
  constructor(msg, status) { super(msg); this.status = status; }
}

async function req(method, path, body, signal) {
  let r;
  try {
    r = await fetch(path, {
      method, signal,
      headers: method === 'POST' ? { 'Content-Type': 'application/json', 'X-TI-Token': TOKEN } : {},
      body: method === 'POST' ? JSON.stringify(body || {}) : undefined,
    });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError('Backend unreachable. Is `tradeintel ui` still running?', 0);
  }
  let data = null;
  try { data = await r.json(); } catch { /* non-JSON */ }
  if (!r.ok) throw new ApiError(data?.error || `HTTP ${r.status}`, r.status);
  return data;
}
export const api = {
  get: (path, signal) => req('GET', path, null, signal),
  post: (path, body, signal) => req('POST', path, body, signal),
};

// ------------------------------------------------------------ components
export function panel(title, body, { tools, meta, foot, cls = '' } = {}) {
  return h('section', { class: `panel ${cls}` },
    h('div', { class: 'panel-h' }, h('h2', null, title), meta ? h('span', { class: 'meta' }, meta) : null,
      tools ? h('div', { class: 'tools' }, tools) : null),
    body instanceof Node && body.classList?.contains('panel-b') ? body : h('div', { class: 'panel-b' }, body),
    foot ? h('div', { class: 'panel-f' }, foot) : null);
}
export const flush = (...kids) => h('div', { class: 'panel-b flush' }, ...kids);

export function badge(text, tone = '') { return h('span', { class: `badge ${tone}` }, text); }

// columns: [{key, label, num, render(row), cls}]
export function table(columns, rows, { empty = 'Nothing here yet.', onRow, selected, maxHeight } = {}) {
  if (!rows || !rows.length) return emptyState(empty);
  const tbody = h('tbody');
  for (const r of rows) {
    tbody.append(h('tr', { class: [onRow ? 'click' : '', selected && selected(r) ? 'sel' : ''].join(' '), onclick: onRow ? () => onRow(r) : null },
      columns.map((c) => h('td', { class: [c.num ? 'num' : '', c.cls || ''].join(' ') }, c.render ? c.render(r) : fmtCell(r[c.key], c)))));
  }
  return h('div', { class: 'tbl-wrap', style: maxHeight ? { maxHeight } : null },
    h('table', { class: 'tbl' }, h('thead', null, h('tr', null, columns.map((c) => h('th', { class: c.num ? 'num' : '' }, c.label)))), tbody));
}
function fmtCell(v, c) {
  if (v == null || v === '') return h('span', { class: 'dim' }, '—');
  if (c.num && typeof v === 'number') return fmt.num(v, c.d ?? 2);
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

export function kv(pairs) {
  return h('dl', { class: 'kv' }, pairs.filter(Boolean).map(([k, v]) => [h('dt', null, k), h('dd', null, v ?? h('span', { class: 'dim' }, '—'))]));
}
export function stat(k, v, sub, tone = '') {
  return h('div', { class: 'stat' }, h('div', { class: 'k' }, k), h('div', { class: `v ${tone}` }, v), sub ? h('div', { class: 's', title: typeof sub === 'string' ? sub : '' }, sub) : null);
}
export function meter(frac) {
  // red only when a limit is exceeded; reaching it exactly is still allowed
  const raw = frac || 0;
  const f = Math.max(0, Math.min(1, raw));
  return h('div', { class: `meter ${raw > 1 ? 'b' : raw >= 0.8 ? 'w' : ''}`, role: 'meter', 'aria-valuenow': Math.round(f * 100), 'aria-valuemin': 0, 'aria-valuemax': 100 },
    h('i', { style: { width: `${Math.max(f * 100, f > 0 ? 3 : 0)}%` } }));
}

export function loading(lines = 3) {
  return h('div', { class: 'stack', style: { padding: '12px' }, 'aria-busy': 'true' },
    Array.from({ length: lines }, (_, i) => h('div', { class: 'skeleton', style: { width: `${90 - i * 18}%` } })));
}
export function emptyState(msg, sub) {
  return h('div', { class: 'state' }, h('div', { class: 't' }, msg), sub ? h('div', { class: 'small' }, sub) : null);
}
export function errorState(err, retry) {
  return h('div', { class: 'state err', role: 'alert' }, h('div', { class: 't' }, 'Could not load'), h('div', { class: 'small' }, String(err?.message || err)),
    retry ? h('button', { class: 'btn sm', onclick: retry }, 'Retry') : null);
}

// Loads `fn(signal)` into `slot`, re-rendering with `render(data)`. Aborts the
// previous request when called again (page switch or refresh).
export function loadInto(slot, fn, render, { quiet = false } = {}) {
  slot._ctl?.abort();
  const ctl = new AbortController();
  slot._ctl = ctl;
  if (!quiet || !slot.childNodes.length) slot.replaceChildren(loading());
  return fn(ctl.signal).then((d) => { if (!ctl.signal.aborted) slot.replaceChildren(render(d)); },
    (e) => { if (e.name !== 'AbortError') slot.replaceChildren(errorState(e, () => loadInto(slot, fn, render))); });
}

// ------------------------------------------------------------ dialogs & toasts
// Resolves true only after an explicit confirm. `typed` requires typing a word.
export function confirmDialog({ title, body, confirm = 'Confirm', danger = false, typed = null }) {
  return new Promise((resolve) => {
    const input = typed ? h('input', { class: 'input', placeholder: `Type ${typed} to confirm`, 'aria-label': `Type ${typed} to confirm` }) : null;
    const ok = h('button', { class: `btn ${danger ? 'danger' : 'primary'}`, disabled: !!typed }, confirm);
    const done = (v) => { ov.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') done(false); };
    input?.addEventListener('input', () => { ok.disabled = input.value.trim() !== typed; });
    ok.addEventListener('click', () => done(true));
    const ov = h('div', { class: 'overlay', onclick: (e) => { if (e.target === ov) done(false); } },
      h('div', { class: 'dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
        h('h3', null, title), h('div', { class: 'body' }, body, input),
        h('div', { class: 'foot' }, h('button', { class: 'btn ghost', onclick: () => done(false) }, 'Cancel'), ok)));
    document.body.append(ov);
    document.addEventListener('keydown', onKey);
    (input || ok).focus();
  });
}

// A sticky toast stays until update()/close(); update() swaps in the result and then fades it out.
export function toast(msg, tone = '', { sticky = false } = {}) {
  let box = document.querySelector('.toasts');
  if (!box) { box = h('div', { class: 'toasts', role: 'status', 'aria-live': 'polite' }); document.body.append(box); }
  const t = h('div', { class: `toast ${tone}` }, msg);
  box.append(t);
  let timer = sticky ? null : setTimeout(() => t.remove(), 7000);
  return {
    update(m, tn = '') { t.className = `toast ${tn}`; t.replaceChildren(m); clearTimeout(timer); timer = setTimeout(() => t.remove(), 9000); },
    close() { clearTimeout(timer); t.remove(); },
  };
}

export function pre(obj) {
  return h('pre', { class: 'code' }, typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2));
}

export const store = {
  get(k, d) { try { const v = localStorage.getItem('ti.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('ti.' + k, JSON.stringify(v)); } catch { /* private mode */ } },
};
