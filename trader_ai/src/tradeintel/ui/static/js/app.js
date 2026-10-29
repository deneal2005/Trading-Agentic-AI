// Application shell: sidebar, status bar, routing, command palette, alerts, actions.
import { h, api, fmt, icon, badge, confirmDialog, toast, store, errorState } from './lib.js';
import * as pages from './pages.js';
import { createCompanion } from './companion.js';

// [group, [[route id, name, one-line intro]]]; route ids stay stable so links keep working
const NAV = [
  ['Monitor', [
    ['overview', 'Today', ''],
    ['markets', 'Chart', 'Price history with the strategy’s signals and the AI’s recorded decisions.'],
    ['execution', 'Orders & positions', 'What the paper account holds, every order the AI considered, and the emergency stop.'],
    ['risk', 'Safety', 'The hard limits every order has to pass, and how close the account is to them.'],
  ]],
  ['Understand', [
    ['agents', 'AI decisions', 'How each decision was made, step by step, from what was actually recorded.'],
    ['brain', 'Neural brain', ''],
    ['research', 'Strategy lab', 'Every strategy tested on real history, and exactly why it passed or failed.'],
  ]],
  ['Records', [
    ['news', 'News & calendar', 'Economic events that can move the market, and headlines from official sources.'],
    ['activity', 'History', 'Everything that happened, newest first: decisions, orders and system logs.'],
  ]],
];
const SETTINGS = ['settings', 'Settings', 'How the system is set up. Changes are made in config files on purpose, not from this screen.'];
const PAGES = [...NAV.flatMap(([, items]) => items), SETTINGS];
const POLL_MS = 10000;

const app = { state: null, stateAt: 0, stateError: null, page: null, route: null, listeners: new Set() };

// ------------------------------------------------------------ actions (confirmation + backend result only)
const ACTIONS = {
  kill: {
    confirm: () => ({
      title: 'Stop all new orders?', danger: true, confirm: 'Stop new orders',
      body: h('div', { class: 'stack' }, h('div', null, 'Every new order will be refused until you turn the emergency stop off again.'),
        h('div', { class: 'note warn small' }, 'It does not sell what you already hold, and it does not cancel orders already waiting at the broker.'),
        h('div', { class: 'small muted' }, 'To turn it off later, run `tradeintel risk reset` in a terminal and type RESET.')),
    }),
    done: () => 'Emergency stop is on. New orders are blocked.', remount: true,
  },
  'run-once': {
    confirm: (st) => ({
      title: 'Run a new check?', danger: st.mode.execution_enabled, confirm: 'Run check',
      body: st.mode.execution_enabled
        ? h('div', { class: 'note neg' }, 'Paper orders are ON. If a promoted strategy signals and every safety check passes, a paper order will be sent.')
        : h('div', null, 'The AI looks at the latest prices and records what it would do. Orders are off, so nothing is sent.'),
    }),
    done: (r) => `Check finished: ${({ abstain: 'no trade, the AI held back', hold: 'nothing changed', exit: 'wants to close', enter_long: 'wants to buy', enter_short: 'wants to sell short' })[r.result?.decision] || r.result?.status || 'done'}.`, remount: true,
  },
  research: {
    confirm: (st, args) => ({ title: `Test ${args.strategy} on history?`, confirm: 'Run test',
      body: h('div', null, 'This takes a few seconds. The result is recorded in the Strategy lab, and the run counts as an extra attempt for this strategy.') }),
    done: (r) => `Test recorded as experiment #${r.experiment_id}: ${r.gate.passed ? 'passed' : 'failed'} the safety gate.`,
  },
  backtest: { done: () => 'Quick backtest finished. It is for a first look only, not proof.' },
  ingest: { done: (r) => `Price data refreshed: ${r.result.rows} daily bars, latest ${fmt.date(r.result.last)}.`, remount: true },
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
const envEl = h('span', { class: 'env-chip' }, 'Connecting…');
const clockEl = h('span', { class: 'clock' });
const statusBtn = h('button', { class: 'status-btn', onclick: () => toggleAlerts(), 'aria-label': 'System status and alerts' }, h('span', { class: 'dot' }), h('span', { class: 'label' }, 'Checking…'));
const content = h('main', { class: 'content', id: 'content', tabindex: '-1' });
let companion = null;
let dropEl = null;

function buildShell() {
  for (const [group, items] of NAV) {
    navEl.append(h('div', { class: 'nav-group' }, group));
    for (const [id, label] of items) navEl.append(h('a', { href: `#/${id}`, dataset: { id }, title: label }, icon(id), h('span', null, label)));
  }
  const toggleNav = () => { shell.classList.toggle('collapsed'); store.set('nav.collapsed', shell.classList.contains('collapsed')); };
  shell.append(
    h('aside', { class: 'sidebar' },
      h('div', { class: 'brand' }, h('span', { class: 'brand-mark', 'aria-hidden': 'true' }), h('span', { class: 'brand-name' }, 'Trading OS')),
      navEl,
      h('div', { class: 'nav', style: { flex: 'none', paddingTop: 0 } },
        h('a', { href: '#/settings', dataset: { id: 'settings' }, title: 'Settings' }, icon('settings'), h('span', null, 'Settings'))),
      h('div', { class: 'side-foot' }, h('button', { class: 'btn icon ghost sm', title: 'Collapse sidebar (Ctrl+B)', 'aria-label': 'Collapse sidebar', onclick: toggleNav }, icon('menu')),
        h('span', { class: 'small dim' }, 'Runs on this PC · paper only'))),
    h('div', { class: 'main' },
      h('header', { class: 'topbar' }, titleEl, envEl, clockEl, h('div', { class: 'spacer' }), statusBtn,
        h('button', { class: 'btn sm', onclick: openPalette, title: 'Search and commands (Ctrl+K)', 'aria-label': 'Search and commands' }, icon('search'), h('span', null, 'Search'), h('kbd', null, 'Ctrl K')),
        h('button', { class: 'btn icon ghost', onclick: () => companion.toggle(), title: 'Ask the assistant (Ctrl+.)', 'aria-label': 'Ask the assistant' }, h('span', { class: 'brand-mark' }))),
      content));
  document.getElementById('app').replaceChildren(shell);
  app.toggleNav = toggleNav;
}

function renderTopbar() {
  const st = app.state;
  if (app.stateError) {
    envEl.className = 'env-chip bad';
    envEl.textContent = 'App backend offline';
  } else if (st) {
    const b = st.broker;
    envEl.className = `env-chip ${b.available ? '' : 'bad'}`;
    envEl.textContent = !b.available ? 'Broker not connected' : b.environment === 'paper' ? 'Internal paper account' : `Paper account · ${b.broker[0].toUpperCase()}${b.broker.slice(1)}`;
    envEl.title = b.available ? `Account ${b.account}. Live trading is disabled in this build.` : b.error;
    const c = b.clock;
    const next = c && (c.is_open ? c.next_close : c.next_open);
    clockEl.title = next ? `${c.is_open ? 'Closes' : 'Opens'} ${fmt.time(next)} your time` : '';
    clockEl.replaceChildren(...(c ? [h('b', null, c.is_open ? 'US market open' : 'US market closed'), ` · ${c.is_open ? 'closes' : 'opens'} ${fmt.ago(next)}`] : []));
  }
  const issues = app.stateError ? [{ level: 'danger' }] : (st?.alerts || []).filter((a) => a.level !== 'info');
  const bad = issues.some((a) => a.level === 'danger');
  statusBtn.className = `status-btn ${bad ? 'bad' : issues.length ? 'warn' : ''}`;
  statusBtn.querySelector('.label').textContent = app.stateError ? 'Offline' : issues.length ? `${issues.length} need${issues.length === 1 ? 's' : ''} attention` : 'All clear';
}

function renderNavBadges() {
  const st = app.state;
  if (!st) return;
  for (const a of shell.querySelectorAll('.nav a')) {
    a.querySelector('.badge')?.remove();
    if (a.dataset.id === 'risk' && st.kill_switch.engaged) a.append(badge('stopped', 'neg'));
    if (a.dataset.id === 'execution' && !st.broker.available) a.append(badge('offline', 'neg'));
  }
}

// ------------------------------------------------------------ alerts center
function toggleAlerts() {
  if (dropEl) { dropEl.remove(); dropEl = null; return; }
  const al = app.state?.alerts || [];
  dropEl = h('div', { class: 'drop', role: 'dialog', 'aria-label': 'Alerts' },
    h('div', { class: 'panel-h', style: { paddingBottom: '10px' } }, h('h2', null, 'Needs your attention'), h('span', { class: 'meta' }, 'checked every 10 seconds')),
    al.length ? al.map((a) => h('div', { class: `alert-item ${a.level}` }, h('div', { class: 'bar' }),
      h('div', null, h('div', { style: { fontWeight: 500 } }, a.title), a.detail ? h('div', { class: 'small muted' }, a.detail) : null, a.at ? h('div', { class: 'small dim' }, fmt.ago(a.at)) : null)))
      : h('div', { class: 'state' }, h('div', { class: 't' }, 'All clear'), h('div', { class: 'small' }, 'Nothing needs your attention.')));
  document.body.append(dropEl);
  renderTopbar();
  setTimeout(() => document.addEventListener('click', function close(e) {
    if (dropEl && !dropEl.contains(e.target)) { dropEl.remove(); dropEl = null; document.removeEventListener('click', close); }
  }), 0);
}

// ------------------------------------------------------------ routing
function parseRoute() {
  const [path, qs] = (location.hash.replace(/^#\/?/, '') || 'overview').split('?');
  return { id: PAGES.some(([n]) => n === path) ? path : 'overview', params: Object.fromEntries(new URLSearchParams(qs || '')) };
}

function mount() {
  app.route = parseRoute();
  app.page?.destroy?.();
  for (const a of shell.querySelectorAll('.nav a')) a.classList.toggle('active', a.dataset.id === app.route.id);
  const [, name, intro] = PAGES.find(([n]) => n === app.route.id);
  titleEl.textContent = name;
  document.title = `${name} · Trading OS`;
  if (!app.state) { content.replaceChildren(errorState(app.stateError || 'no state', () => refreshState().then(mount))); return; }
  try {
    // ctx.state is a getter so pages always read the latest polled state
    app.page = pages[app.route.id]({ get state() { return app.state; }, params: app.route.params, act, companion, go: (hash) => { location.hash = hash; } });
    content.replaceChildren(...(intro ? [h('p', { class: 'page-intro' }, intro)] : []), app.page.el);
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
    ...PAGES.map(([id, label]) => ({ label: `Go to ${label}`, group: 'page', run: () => { location.hash = `#/${id}`; } })),
    { label: 'Run a new check', group: 'action', kw: 'run-once agent decision cycle', run: () => act('run-once') },
    { label: 'Refresh price data', group: 'action', kw: 'ingest bars market data', run: () => act('ingest') },
    { label: 'Refresh news & calendar', group: 'action', kw: 'ingest events', run: () => act('news-ingest') },
    { label: 'Stop all new orders (emergency stop)', group: 'action', kw: 'kill switch halt pause', run: () => act('kill') },
    { label: 'Reload account from the broker', group: 'action', kw: 'alpaca account refresh', run: () => api.post('/api/actions/refresh-broker').then(refreshState) },
    { label: 'Ask the assistant', group: 'assistant', kw: 'companion', run: () => companion.open() },
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
