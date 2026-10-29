// Application shell: sidebar, status bar, routing, command palette, alerts, actions.
import { h, api, fmt, icon, badge, confirmDialog, toast, store, errorState } from './lib.js';
import * as pages from './pages.js';
import { createCompanion } from './companion.js';

const NAV = [
  ['overview', 'Overview'], ['markets', 'Markets'], ['execution', 'Execution'], ['agents', 'Agents'],
  ['risk', 'Risk Center'], ['research', 'Research'], ['news', 'News & Events'], ['activity', 'Activity & Logs'], ['settings', 'Settings'],
];
const POLL_MS = 10000;

const app = { state: null, stateAt: 0, stateError: null, page: null, route: null, listeners: new Set() };

// ------------------------------------------------------------ actions (confirmation + backend result only)
const ACTIONS = {
  kill: {
    confirm: () => ({
      title: 'Engage the kill switch?', danger: true, confirm: 'Engage kill switch',
      body: h('div', { class: 'stack' }, h('div', null, 'The backend will refuse every new order until the switch is cleared.'),
        h('div', { class: 'note warn small' }, 'It does NOT close open positions or cancel resting broker orders.'),
        h('div', { class: 'small muted' }, 'Clearing it later needs `tradeintel risk reset` in a terminal (type RESET).')),
    }),
    done: (r) => 'Kill switch engaged (backend confirmed). ' + (r.note || ''), remount: true,
  },
  'run-once': {
    confirm: (st) => ({
      title: 'Run one decision cycle?', danger: st.mode.execution_enabled, confirm: 'Run cycle',
      body: st.mode.execution_enabled
        ? h('div', { class: 'note neg' }, 'Order submission is ENABLED: a promoted strategy with a risk-approved signal will send a paper order.')
        : h('div', null, 'Records a decision with the current data. Order submission is disabled, so no order can be sent (dry run).'),
    }),
    done: (r) => `Cycle finished (backend): ${r.result?.decision || r.result?.status || 'done'}${r.result?.execution ? ' · ' + r.result.execution.status : ''}`, remount: true,
  },
  research: {
    confirm: (st, args) => ({ title: `Run walk-forward research on ${args.strategy}?`, confirm: 'Run research',
      body: h('div', null, 'This takes a few seconds, adds trials to the experiment registry (they count against the strategy) and overwrites its HTML report.') }),
    done: (r) => `Research recorded as experiment #${r.experiment_id}: ${r.gate.passed ? 'passed' : 'rejected'}.`,
  },
  backtest: { done: () => 'In-sample backtest finished (inspection only).' },
  ingest: { done: (r) => `Stored ${r.result.rows} ${r.result.source} bars, last ${fmt.date(r.result.last)}.`, remount: true },
  'news-ingest': { done: () => 'News and calendar refreshed.' },
};

async function act(name, args = {}, { skipConfirm = false } = {}) {
  const spec = ACTIONS[name];
  if (!spec) throw new Error(`unknown action ${name}`);
  if (spec.confirm && !skipConfirm && !(await confirmDialog(spec.confirm(app.state, args)))) return null;
  companion?.setActivity('working');
  try {
    const r = await api.post(`/api/actions/${name}`, args);
    if (r.ok === false) toast(`Failed: ${r.error}`, 'neg');
    else toast(spec.done(r), 'pos');
    await refreshState();
    if (spec.remount && r.ok !== false) mount();
    return r;
  } catch (e) {
    toast(`Failed: ${e.message}`, 'neg');
    return { ok: false, error: e.message };
  } finally {
    companion?.setActivity(null);
  }
}

// ------------------------------------------------------------ state polling
async function refreshState() {
  try {
    app.state = await api.get('/api/state');
    app.stateAt = Date.now();
    app.stateError = null;
  } catch (e) {
    app.stateError = e;
  }
  renderTopbar();
  renderNavBadges();
  companion?.update(app.state, app.stateError);
  if (!app.stateError) app.page?.onState?.(app.state);
}

// ------------------------------------------------------------ shell
const shell = h('div', { class: `shell ${store.get('nav.collapsed', false) ? 'collapsed' : ''}` });
const navEl = h('nav', { class: 'nav', 'aria-label': 'Main' });
const titleEl = h('h1');
const pillsEl = h('div', { class: 'pills', role: 'status', 'aria-live': 'polite' });
const bellCount = h('span', { class: 'badge warn', style: { display: 'none' } });
const content = h('main', { class: 'content', id: 'content', tabindex: '-1' });
let companion = null;
let dropEl = null;

function buildShell() {
  for (const [id, label] of NAV) {
    navEl.append(h('a', { href: `#/${id}`, dataset: { id }, title: label }, icon(id), h('span', null, label)));
  }
  const toggleNav = () => { shell.classList.toggle('collapsed'); store.set('nav.collapsed', shell.classList.contains('collapsed')); };
  shell.append(
    h('aside', { class: 'sidebar' },
      h('div', { class: 'brand' }, h('span', { class: 'brand-mark', 'aria-hidden': 'true' }), h('span', { class: 'brand-name' }, 'Trading OS', h('small', null, 'tradeintel'))),
      navEl,
      h('div', { class: 'side-foot' }, h('button', { class: 'btn icon ghost', title: 'Collapse sidebar (Ctrl+B)', 'aria-label': 'Collapse sidebar', onclick: toggleNav }, icon('menu')),
        h('span', { class: 'small dim' }, 'local · demo only'))),
    h('div', { class: 'main' },
      h('header', { class: 'topbar' }, titleEl, pillsEl, h('div', { class: 'spacer' }),
        h('button', { class: 'btn sm', onclick: openPalette, title: 'Search & commands', 'aria-label': 'Open command palette' }, icon('search'), h('span', null, 'Search'), h('kbd', null, 'Ctrl K')),
        h('button', { class: 'btn icon ghost', onclick: toggleAlerts, title: 'Alerts', 'aria-label': 'Alerts' }, icon('bell'), bellCount),
        h('button', { class: 'btn icon ghost', onclick: () => companion.toggle(), title: 'Companion (Ctrl+.)', 'aria-label': 'Toggle companion' }, h('span', { class: 'brand-mark' }))),
      content));
  document.getElementById('app').replaceChildren(shell);
  app.toggleNav = toggleNav;
}

function pill(text, tone = '', title = '') {
  return h('span', { class: `pill ${tone}`, title }, h('span', { class: 'dot' }), text);
}

function renderTopbar() {
  const st = app.state;
  const kids = [];
  if (app.stateError) kids.push(pill('BACKEND DISCONNECTED', 'bad', app.stateError.message));
  if (st) {
    const b = st.broker;
    kids.push(b.available
      ? h('span', { class: 'pill env', title: `account ${b.account}` }, b.environment === 'paper' ? 'INTERNAL PAPER' : `PAPER · ${b.broker.toUpperCase()}`)
      : pill('BROKER DISCONNECTED', 'bad', b.error));
    kids.push(pill(st.mode.execution_enabled ? 'PAPER ORDERS ON' : 'OBSERVATION ONLY', st.mode.execution_enabled ? 'warnp' : 'ok', st.mode.label));
    if (b.clock) {
      const open = b.clock.is_open;
      kids.push(pill(open ? `US market open · closes ${fmt.time(b.clock.next_close)}` : `US market closed · opens ${fmt.time(b.clock.next_open)}`, open ? 'ok' : '', 'Alpaca market clock'));
    }
    kids.push(pill(`${st.data.instrument} bar ${fmt.date(st.data.last_bar)}${st.data.stale ? ' · stale' : ''}`, st.data.stale ? 'warnp' : 'ok', `source ${st.data.source || 'none'}`));
    kids.push(pill(`agent ${st.agent.state}${st.agent.last_decision ? ' · ' + fmt.ago(st.agent.last_decision.decided_at) : ''}`, st.agent.state === 'halted' ? 'bad' : 'aip', st.agent.note));
    kids.push(pill(st.kill_switch.engaged ? 'KILL SWITCH ENGAGED' : 'kill switch clear', st.kill_switch.engaged ? 'bad' : 'ok', st.kill_switch.reason || ''));
    kids.push(h('span', { class: 'pill', title: 'last state update' }, `updated ${fmt.ago(new Date(app.stateAt).toISOString())}`));
  }
  pillsEl.replaceChildren(...kids);
  const unseen = unseenAlerts();
  bellCount.style.display = unseen.length ? '' : 'none';
  bellCount.textContent = String(unseen.length);
}

function renderNavBadges() {
  const st = app.state;
  if (!st) return;
  for (const a of navEl.querySelectorAll('a')) {
    a.querySelector('.badge')?.remove();
    if (a.dataset.id === 'risk' && st.kill_switch.engaged) a.append(badge('halt', 'neg'));
    if (a.dataset.id === 'execution' && !st.broker.available) a.append(badge('off', 'neg'));
  }
}

// ------------------------------------------------------------ alerts center
function unseenAlerts() {
  const seen = new Set(store.get('alerts.seen', []));
  return (app.state?.alerts || []).filter((a) => !seen.has(a.key));
}
function toggleAlerts() {
  if (dropEl) { dropEl.remove(); dropEl = null; return; }
  const al = app.state?.alerts || [];
  store.set('alerts.seen', [...new Set([...store.get('alerts.seen', []), ...al.map((a) => a.key)])].slice(-300));
  dropEl = h('div', { class: 'drop', role: 'dialog', 'aria-label': 'Alerts' },
    h('div', { class: 'panel-h' }, h('h2', null, 'Alerts'), h('span', { class: 'meta' }, 'derived from live state')),
    al.length ? al.map((a) => h('div', { class: `alert-item ${a.level}` }, h('div', { class: 'bar' }),
      h('div', null, h('div', null, a.title), a.detail ? h('div', { class: 'small muted' }, a.detail) : null, a.at ? h('div', { class: 'small dim' }, fmt.ago(a.at)) : null)))
      : h('div', { class: 'state' }, h('div', { class: 't' }, 'No active alerts')));
  document.body.append(dropEl);
  renderTopbar();
  setTimeout(() => document.addEventListener('click', function close(e) {
    if (dropEl && !dropEl.contains(e.target)) { dropEl.remove(); dropEl = null; document.removeEventListener('click', close); }
  }), 0);
}

// ------------------------------------------------------------ routing
function parseRoute() {
  const [path, qs] = (location.hash.replace(/^#\/?/, '') || 'overview').split('?');
  return { id: NAV.some(([n]) => n === path) ? path : 'overview', params: Object.fromEntries(new URLSearchParams(qs || '')) };
}

function mount() {
  app.route = parseRoute();
  app.page?.destroy?.();
  for (const a of navEl.querySelectorAll('a')) a.classList.toggle('active', a.dataset.id === app.route.id);
  titleEl.textContent = NAV.find(([n]) => n === app.route.id)[1];
  document.title = `${titleEl.textContent} · Trading OS`;
  if (!app.state) { content.replaceChildren(errorState(app.stateError || 'no state', () => refreshState().then(mount))); return; }
  try {
    // ctx.state is a getter so pages always read the latest polled state
    app.page = pages[app.route.id]({ get state() { return app.state; }, params: app.route.params, act, companion, go: (hash) => { location.hash = hash; } });
    content.replaceChildren(app.page.el);
  } catch (e) {
    console.error(e);
    content.replaceChildren(errorState(e));
  }
  content.scrollTop = 0;
}

// ------------------------------------------------------------ command palette
let paletteEl = null;
async function openPalette() {
  if (paletteEl) return;
  const input = h('input', { placeholder: 'Search pages, actions, symbols, strategies, experiments, decisions…', 'aria-label': 'Command' });
  const list = h('ul', { role: 'listbox' });
  let items = baseItems();
  let sel = 0;
  const close = () => { paletteEl?.remove(); paletteEl = null; };
  const run = (it) => { close(); it.run(); };
  const render = () => {
    const q = input.value.trim().toLowerCase();
    const shown = items.filter((it) => !q || `${it.label} ${it.group} ${it.kw || ''}`.toLowerCase().includes(q)).slice(0, 40);
    if (q) shown.push({ label: `Search activity & logs for "${input.value.trim()}"`, group: 'search', run: () => { location.hash = `#/activity?q=${encodeURIComponent(input.value.trim())}`; } });
    if (q) shown.push({ label: `Ask the companion: "${input.value.trim()}"`, group: 'companion', run: () => companion.ask(input.value.trim()) });
    sel = Math.min(sel, shown.length - 1);
    list.replaceChildren(...shown.map((it, i) => h('li', { class: i === sel ? 'on' : '', role: 'option', 'aria-selected': String(i === sel), onclick: () => run(it), onmousemove: () => { if (sel !== i) { sel = i; render(); } } },
      it.label, h('span', { class: 'grp' }, it.group))));
    list._shown = shown;
  };
  input.addEventListener('input', () => { sel = 0; render(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { sel = Math.min(sel + 1, list._shown.length - 1); render(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { sel = Math.max(sel - 1, 0); render(); e.preventDefault(); }
    else if (e.key === 'Enter' && list._shown[sel]) run(list._shown[sel]);
    else if (e.key === 'Escape') close();
  });
  paletteEl = h('div', { class: 'overlay', onclick: (e) => { if (e.target === paletteEl) close(); } }, h('div', { class: 'palette', role: 'dialog', 'aria-label': 'Command palette' }, input, list));
  document.body.append(paletteEl);
  input.focus();
  render();
  // enrich with live entities; each source appears as soon as it arrives (failures just leave it out)
  const enrich = (p, f) => p.then((x) => { items = [...items, ...f(x)]; if (paletteEl) render(); }).catch(() => {});
  enrich(api.get('/api/research'), (rs) => [
    ...rs.strategies.filter((x) => !x.baseline).flatMap((x) => [
      { label: `Backtest ${x.name} (in-sample)`, group: 'strategy', run: () => { location.hash = '#/research'; act('backtest', { strategy: x.name }); } },
      { label: `Walk-forward research: ${x.name}`, group: 'strategy', run: () => act('research', { strategy: x.name }) }]),
    ...rs.experiments.slice(0, 40).map((e) => ({ label: `Experiment #${e.id} ${e.strategy} ${e.instrument} (${e.verdict || e.kind})`, group: 'experiment', kw: e.data_source, run: () => { location.hash = `#/research?exp=${e.id}`; } }))]);
  enrich(api.get('/api/decisions?limit=30'), (ds) => ds.map((d) => ({ label: `Decision #${d.id} ${d.action} ${d.instrument} ${fmt.time(d.decided_at)}`, group: 'decision', run: () => { location.hash = `#/agents?decision=${d.id}`; } })));
  enrich(api.get('/api/watchlist'), (wl) => wl.map((w) => ({ label: `${w.instrument}: chart`, group: 'symbol', run: () => { location.hash = `#/markets?symbol=${encodeURIComponent(w.instrument)}`; } })));
}

function baseItems() {
  return [
    ...NAV.map(([id, label]) => ({ label: `Go to ${label}`, group: 'page', run: () => { location.hash = `#/${id}`; } })),
    { label: 'Refresh market data', group: 'action', kw: 'ingest bars', run: () => act('ingest') },
    { label: 'Refresh news & calendar', group: 'action', kw: 'ingest events', run: () => act('news-ingest') },
    { label: 'Run decision cycle', group: 'action', kw: 'run-once agent', run: () => act('run-once') },
    { label: 'Engage kill switch', group: 'action', kw: 'stop halt pause', run: () => act('kill') },
    { label: 'Refresh broker snapshot', group: 'action', kw: 'alpaca account', run: () => api.post('/api/actions/refresh-broker').then(refreshState) },
    { label: 'Open companion', group: 'companion', run: () => companion.open() },
    { label: 'Toggle sidebar', group: 'view', run: () => app.toggleNav() },
  ];
}

// ------------------------------------------------------------ boot
document.addEventListener('keydown', (e) => {
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
  else if (mod && e.key === '.') { e.preventDefault(); companion.toggle(); }
  else if (mod && e.key.toLowerCase() === 'b') { e.preventDefault(); app.toggleNav(); }
});
window.addEventListener('hashchange', mount);

buildShell();
companion = createCompanion({ act, getState: () => app.state, route: () => app.route });
document.body.append(companion.el);
content.replaceChildren(h('div', { class: 'state' }, h('div', { class: 't' }, 'Connecting to the backend…'), h('div', { class: 'small' }, 'The first broker read can take a few seconds.')));
refreshState().then(mount);
setInterval(refreshState, POLL_MS);
