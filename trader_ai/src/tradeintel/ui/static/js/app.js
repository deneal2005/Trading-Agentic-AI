// Application shell: header navigation, system status strip, routing, command palette, alerts, actions.
import { h, api, fmt, icon, badge, confirmDialog, toast, errorState, store, SNAPSHOT } from './lib.js';
import * as pages from './pages.js';
import { createCompanion } from './companion.js';
import { createActivity, stepLine } from './activity.js';
import { hub, emitStatus, statusStrip, killReason } from './controls.js';

// Top-level sections, each with its pages: [section id, label, icon, [[route id, name, one-line intro]]].
// Route ids stay stable so old links keep working.
const NAV = [
  ['overview', 'Overview', 'overview', [['overview', 'Overview', 'Account, position, the AI’s latest decision and what needs you, on one screen.']]],
  ['trading', 'Trading', 'trading', [
    ['execution', 'Orders & positions', 'What the paper account holds and every order the AI considered, with what happened to it.'],
    ['markets', 'Chart', 'Price history with the strategy’s signals and the AI’s recorded decisions.']]],
  ['strategy', 'Strategies', 'strategy', [
    ['agents', 'AI decisions', 'How each decision was made, step by step, from what was actually recorded.'],
    ['research', 'Strategy lab', 'Every strategy tested on real history, and exactly why it passed or failed.'],
    ['brain', 'Neural model', 'The research model’s real forward pass on each stored day. Not used for trading.']]],
  ['records', 'Activity', 'activity', [
    ['activity', 'History', 'Everything that happened, newest first: background tasks, decisions, orders and system logs.'],
    ['news', 'News & calendar', 'Economic events that can move the market, and headlines from official sources.']]],
  ['safety', 'Risk', 'risk', [['risk', 'Risk & safety', 'The hard limits every order has to pass, how close the account is to them, and the emergency stop.']]],
  ['config', 'Settings', 'settings', [['settings', 'Settings', 'How the system is set up. Changes are made in config files on purpose, not from this screen.']]],
];
const PAGES = NAV.flatMap(([sec, , , items]) => items.map(([id, name, intro]) => ({ id, name, intro, sec })));
const POLL_MS = 10000;

const app = { page: null, route: null };

// ------------------------------------------------------------ actions (confirmation + backend result only)
const ACTIONS = {
  kill: {
    confirm: () => ({
      title: 'Stop all new orders?', danger: true, confirm: 'Stop new orders',
      body: h('div', { class: 'stack' }, h('div', null, 'Every new order will be refused until you release the emergency stop.'),
        h('div', { class: 'note warn small' }, 'It does not sell what you already hold, and it does not cancel orders already waiting at the broker.')),
    }),
    busy: 'Engaging the emergency stop…',
    done: () => 'Emergency stop engaged. New orders are blocked.', remount: true,
  },
  'reset-kill': {
    confirm: (st) => ({
      title: 'Release the emergency stop?', danger: true, confirm: 'Release', typed: 'RESET',
      body: h('div', { class: 'stack' },
        h('div', null, 'It was engaged: ', h('b', null, killReason(st.kill_switch.reason)), st.kill_switch.at ? [' (', fmt.time(st.kill_switch.at), ')'] : ''),
        h('div', null, 'Releasing lets new orders through again, but only if every other safety check passes. It also clears the losing-streak pause.'),
        h('div', { class: 'small muted' }, st.mode.execution_enabled ? 'Paper orders are ON, so orders can go out after this.' : 'Orders are off in the settings, so nothing will be sent either way.')),
    }),
    busy: 'Releasing the emergency stop…',
    done: () => 'Emergency stop released. Every other safety check still applies.', remount: true,
  },
  'run-once': {
    // only confirm when the check could actually send an order; with orders off it just records a decision
    confirm: (st) => (st.mode.execution_enabled ? {
      title: 'Run a check that may trade?', danger: true, confirm: 'Run check',
      body: h('div', { class: 'note neg' }, 'Paper orders are ON. If a promoted strategy signals and every safety check passes, a paper order will be sent.'),
    } : null),
    busy: 'Starting a check…',
    done: (r) => `Check finished: ${({ abstain: 'no trade, the AI held back', hold: 'nothing changed', exit: 'wants to close', enter_long: 'wants to buy', enter_short: 'wants to sell short' })[r.result?.decision] || r.result?.status || 'done'}.`, remount: true,
  },
  research: {
    confirm: (st, args) => ({ title: `Test ${args.strategy} on history?`, confirm: 'Run test',
      body: h('div', null, 'This takes a few seconds. The result is recorded in the Strategy lab, and the run counts as an extra attempt for this strategy.') }),
    busy: 'Testing on history… about 10–20 seconds.',
    done: (r) => `Test recorded as experiment #${r.experiment_id}: ${r.gate.passed ? 'passed' : 'failed'} the safety gate.`,
  },
  backtest: { busy: 'Running a quick backtest…', done: () => 'Quick backtest finished. It is for a first look only, not proof.' },
  ingest: { busy: 'Downloading price data…', done: (r) => `Price data refreshed: ${r.result.rows} daily bars, latest ${fmt.date(r.result.last)}.`, remount: true },
  'news-ingest': { busy: 'Refreshing news & calendar…', done: () => 'News and calendar refreshed.' },
  reconcile: { busy: 'Asking the broker about waiting orders…', done: (r) => `Broker checked: ${r.updated ?? 0} order(s) updated.` },
};

const busyLine = (t) => h('span', { class: 'row', style: { gap: '10px', flexWrap: 'nowrap' } }, h('span', { class: 'spin' }), t);

// While an action is pending it is in hub.pending: every control shows "…ing" and a second request is refused.
// The result is shown only after the backend confirms it (and the state has been re-read).
async function act(name, args = {}, { skipConfirm = false, button = null } = {}) {
  const spec = ACTIONS[name];
  if (!spec) throw new Error(`unknown action ${name}`);
  if (hub.pending.has(name)) { toast('That is already in progress.', ''); return null; }
  if (!hub.state) { toast('Not connected to the backend, so nothing was sent.', 'neg'); return null; }
  const dlg = spec.confirm && !skipConfirm ? spec.confirm(hub.state, args) : null;
  if (dlg && !(await confirmDialog(dlg))) return null;
  hub.pending.set(name, Date.now());
  emitStatus();
  const note = toast(busyLine(spec.busy || 'Working…'), 'busy', { sticky: true });
  let restore = null;
  if (button) {
    const old = [...button.childNodes];
    button.disabled = true;
    button.replaceChildren(h('span', { class: 'spin' }), 'Working…');
    restore = () => { button.disabled = false; button.replaceChildren(...old); };
  }
  companion?.setActivity('working');
  try {
    let r = await api.post(`/api/actions/${name}`, args);
    if (r.job) {  // long actions run as backend jobs: follow the real job until it ends
      if (!r.started) note.set(busyLine(`Already running (started ${fmt.ago(r.job.started_at)}), so not starting it twice. Following that one.`));
      activity.refresh();
      const j = await activity.track(r.job.id, (jj) => note.set(busyLine(jj ? `${jj.label}: ${stepLine(jj)} · ${Math.round(jj.elapsed_s)} s` : 'Lost contact with the app. Retrying…')));
      r = j.state === 'succeeded' ? (j.result || { ok: true }) : { ok: false, error: j.error || 'it stopped' };
    }
    await refreshState();
    if (r.ok === false) note.update(`Didn't work: ${r.error}`, 'neg');
    else note.update(spec.done(r), 'pos');
    if (name === 'research') loadPromotion();
    if (spec.remount && r.ok !== false) mount();
    return r;
  } catch (e) {
    note.update(`Didn't work: ${e.message}`, 'neg');
    return { ok: false, error: e.message };
  } finally {
    hub.pending.delete(name);
    emitStatus();
    restore?.();
    companion?.setActivity(null);
  }
}
hub.act = act;

// ------------------------------------------------------------ state polling
async function refreshState() {
  try {
    hub.state = await api.get('/api/state');
    hub.stateAt = Date.now();
    hub.error = null;
  } catch (e) {
    hub.error = e;
  }
  renderHeader();
  emitStatus();
  companion?.update(hub.state, hub.error);
  if (!hub.error) app.page?.onState?.(hub.state);
}
// whether the live strategy passed the promotion gate decides if "orders on" can actually send anything
function loadPromotion() {
  return api.get('/api/research').then((rs) => {
    const live = rs.strategies.find((x) => x.is_live);
    hub.promoted = live ? !!live.promoted : null;
    emitStatus();
  }).catch(() => {});
}

// ------------------------------------------------------------ shell
const tabInd = h('span', { class: 'tab-ind', 'aria-hidden': 'true' });
const tabsEl = h('nav', { class: 'tabs pillbox', 'aria-label': 'Main' }, tabInd);
const themeBtn = h('button', { class: 'hbtn', onclick: () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark') });
const envEl = h('span', { class: 'env-chip' }, 'Connecting…');
const alertsBtn = h('button', { class: 'hbtn', onclick: (e) => { e.stopPropagation(); toggleAlerts(); }, 'aria-label': 'Alerts' }, h('span', { class: 'dot' }), h('span', { class: 'label' }, 'Checking…'));
const content = h('main', { class: 'content', id: 'content', tabindex: '-1' });
// Background jobs. Buttons tagged data-job are disabled while that kind of job runs anywhere.
const activity = createActivity({
  onChange(data, offline) {
    hub.activity = offline ? null : data;
    for (const b of document.querySelectorAll('[data-job]')) {
      const busy = activity.isRunning(b.dataset.job);
      if (busy) { b.disabled = true; b.title = 'Already running: see Background work in the header'; } else if (b.title.startsWith('Already running')) { b.disabled = false; b.title = ''; }
    }
    companion?.setActivity(data?.running.length ? 'working' : null);
    emitStatus();
    app.page?.onActivity?.(data);
  },
});
let companion = null;
let dropEl = null;

function buildShell() {
  for (const [sec, label, ic, items] of NAV) tabsEl.append(h('a', { href: `#/${items[0][0]}`, dataset: { sec } }, icon(ic), h('span', null, label)));
  const shell = h('div', { class: 'shell' },
    h('header', { class: 'hdr' },
      h('a', { class: 'brand pillbox', href: '#/overview', 'aria-label': 'Trading OS home' }, h('span', { class: 'brand-mark', 'aria-hidden': 'true' }), h('span', { class: 'brand-name' }, 'Trading OS')),
      tabsEl,
      h('div', { class: 'tools pillbox' }, envEl, activity.el, alertsBtn,
        h('button', { class: 'hbtn', onclick: openPalette, title: 'Search and commands (Ctrl+K)', 'aria-label': 'Search and commands' }, icon('search')),
        h('button', { class: 'hbtn', onclick: () => companion.toggle(), title: 'Ask the assistant (Ctrl+.)', 'aria-label': 'Ask the assistant' }, icon('send')),
        themeBtn)),
    SNAPSHOT ? h('div', { class: 'snapshot-banner', role: 'note' }, h('b', null, 'Read-only snapshot, not live. '),
      'Account, prices and decisions are as recorded when this page was published; controls are switched off. ',
      h('a', { href: 'https://github.com/deneal2005/Trading-Agentic-AI', target: '_blank', rel: 'noopener' }, 'Run it yourself →')) : null,
    statusStrip(),
    activity.drop,
    content);
  document.getElementById('app').replaceChildren(shell);
  addEventListener('resize', moveTabInd);
}

// the black pill slides to the active tab (from the reference)
function moveTabInd() {
  const a = tabsEl.querySelector('a.active');
  if (!a) return;
  tabInd.style.width = `${a.offsetWidth}px`;
  tabInd.style.transform = `translateX(${a.offsetLeft}px)`;
}

function setTheme(t) {
  document.documentElement.dataset.theme = t;
  store.set('theme', t);
  themeBtn.replaceChildren(icon(t === 'dark' ? 'sun' : 'moon'));
  themeBtn.title = t === 'dark' ? 'Light theme' : 'Dark theme';
  themeBtn.setAttribute('aria-label', themeBtn.title);
  if (app.route) mount();  // charts read their colours from the theme when they are built
}

function renderHeader() {
  const st = hub.state;
  if (hub.error) {
    envEl.className = 'env-chip bad';
    envEl.textContent = 'Backend offline';
  } else if (st) {
    const b = st.broker;
    envEl.className = `env-chip ${b.available ? '' : 'bad'}`;
    const c = b.clock;
    envEl.textContent = !b.available ? 'Broker offline' : `${b.environment === 'paper' ? 'Internal paper' : `${b.broker[0].toUpperCase()}${b.broker.slice(1)} paper`}${c ? ` · US market ${c.is_open ? 'open' : 'closed'}` : ''}`;
    const next = c && (c.is_open ? c.next_close : c.next_open);
    envEl.title = `${b.available ? `Account ${b.account}. Live trading is disabled in this build.` : b.error}${next ? ` ${c.is_open ? 'Closes' : 'Opens'} ${fmt.time(next)} your time.` : ''}`;
  }
  const issues = hub.error ? [{ level: 'danger' }] : (st?.alerts || []).filter((a) => a.level !== 'info');
  const bad = issues.some((a) => a.level === 'danger');
  alertsBtn.className = `hbtn ${bad ? 'bad' : issues.length ? 'warn' : ''}`;
  alertsBtn.querySelector('.label').textContent = hub.error ? 'Offline' : issues.length ? `${issues.length} alert${issues.length === 1 ? '' : 's'}` : 'All clear';
  for (const a of tabsEl.querySelectorAll('a')) {
    a.querySelector('.badge')?.remove();
    if (!st) continue;
    if (a.dataset.sec === 'safety' && st.kill_switch.engaged) a.append(badge('stop', 'neg'));
    if (a.dataset.sec === 'trading' && !st.broker.available) a.append(badge('offline', 'neg'));
  }
}

// ------------------------------------------------------------ alerts center
function toggleAlerts() {
  if (dropEl) { dropEl.remove(); dropEl = null; return; }
  const al = hub.state?.alerts || [];
  dropEl = h('div', { class: 'drop', role: 'dialog', 'aria-label': 'Alerts', onclick: (e) => e.stopPropagation() },
    h('div', { class: 'panel-h', style: { paddingBottom: '10px' } }, h('h2', null, 'Needs your attention'), h('span', { class: 'meta' }, 'checked every 10 seconds')),
    al.length ? al.map((a) => h('div', { class: `alert-item ${a.level}` }, h('div', { class: 'bar' }),
      h('div', null, h('div', { style: { fontWeight: 500 } }, a.title), a.detail ? h('div', { class: 'small muted' }, a.detail) : null, a.at ? h('div', { class: 'small dim' }, fmt.ago(a.at)) : null)))
      : h('div', { class: 'state' }, h('div', { class: 't' }, 'All clear'), h('div', { class: 'small' }, 'Nothing needs your attention.')));
  document.body.append(dropEl);
}
document.addEventListener('click', () => { if (dropEl) { dropEl.remove(); dropEl = null; } });
// click ripple on buttons; removed by timer because reduced motion cancels the animation
document.addEventListener('pointerdown', (e) => {
  const b = e.target.closest?.('.btn');
  if (!b || b.disabled) return;
  const r = b.getBoundingClientRect(), d = Math.max(r.width, r.height) * 2;
  const s = h('span', { class: 'ripple', style: { width: `${d}px`, height: `${d}px`, left: `${e.clientX - r.left - d / 2}px`, top: `${e.clientY - r.top - d / 2}px` } });
  b.append(s);
  setTimeout(() => s.remove(), 600);
});

// ------------------------------------------------------------ routing
function parseRoute() {
  const [path, qs] = (location.hash.replace(/^#\/?/, '') || 'overview').split('?');
  return { id: PAGES.some((p) => p.id === path) ? path : 'overview', params: Object.fromEntries(new URLSearchParams(qs || '')) };
}

function mount() {
  app.route = parseRoute();
  app.page?.destroy?.();
  const pg = PAGES.find((p) => p.id === app.route.id);
  for (const a of tabsEl.querySelectorAll('a')) a.classList.toggle('active', a.dataset.sec === pg.sec);
  moveTabInd();
  document.title = `${pg.name} · Trading OS`;
  if (!hub.state) { content.replaceChildren(errorState(hub.error || 'no state', () => refreshState().then(mount))); return; }
  const siblings = NAV.find(([sec]) => sec === pg.sec)[3];
  const hr = new Date().getHours();
  const title = pg.id === 'overview' ? `Good ${hr < 12 ? 'morning' : hr < 18 ? 'afternoon' : 'evening'}` : pg.name;
  const letters = [...title].map((c, i) => h('span', { 'aria-hidden': 'true', style: { animationDelay: `${i * 22 + 60}ms` } }, c));
  const head = h('div', { class: 'page-head' }, h('div', null, h('h1', { 'aria-label': title }, letters), pg.intro ? h('p', null, pg.intro) : null),
    siblings.length > 1 ? h('nav', { class: 'subnav', 'aria-label': 'Section pages' }, siblings.map(([id, name]) => h('a', { href: `#/${id}`, class: id === pg.id ? 'on' : '', 'aria-current': id === pg.id ? 'page' : null }, name))) : null);
  try {
    // ctx.state is a getter so pages always read the latest polled state
    app.page = pages[app.route.id]({ get state() { return hub.state; }, params: app.route.params, act, companion, go: (hash) => { location.hash = hash; } });
    const pageEl = h('div', { class: 'page entering' }, head, app.page.el);
    // the old page dissolves while the new one reveals; browsers without View Transitions just swap
    if (document.startViewTransition) document.startViewTransition(() => content.replaceChildren(pageEl));
    else content.replaceChildren(pageEl);
    // staggered reveal order; 'entering' scopes one-shot animations so later polls don't replay them
    pageEl.querySelectorAll('.kpi, .panel, .brain-stage').forEach((x, i) => x.style.setProperty('--i', Math.min(i, 10)));
    setTimeout(() => pageEl.classList.remove('entering'), 1600);
  } catch (e) {
    console.error(e);
    content.replaceChildren(head, errorState(e));
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
    if (q) shown.push({ label: `Ask the assistant: "${input.value.trim()}"`, group: 'assistant', run: () => companion.ask(input.value.trim()) });
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
  // actions follow the confirmed state, like the buttons: no "start" for something already running
  const st = hub.state;
  const checking = !!hub.activity?.running.some((j) => j.kind === 'check') || hub.pending.has('run-once');
  return [
    ...PAGES.map((p) => ({ label: `Go to ${p.name}`, group: 'page', run: () => { location.hash = `#/${p.id}`; } })),
    checking ? null : { label: 'Run a check now', group: 'action', kw: 'run-once agent decision cycle', run: () => act('run-once') },
    { label: 'Refresh price data', group: 'action', kw: 'ingest bars market data', run: () => act('ingest') },
    { label: 'Refresh news & calendar', group: 'action', kw: 'ingest events', run: () => act('news-ingest') },
    st && !st.kill_switch.engaged ? { label: 'Stop all new orders (emergency stop)', group: 'action', kw: 'kill switch halt pause', run: () => act('kill') } : null,
    st?.kill_switch.engaged ? { label: 'Release the emergency stop', group: 'action', kw: 'kill switch reset resume', run: () => act('reset-kill', { confirm: 'RESET' }) } : null,
    { label: 'Reload account from the broker', group: 'action', kw: 'alpaca account refresh', run: () => api.post('/api/actions/refresh-broker').then(refreshState) },
    { label: 'Ask the assistant', group: 'assistant', kw: 'companion', run: () => companion.open() },
  ].filter(Boolean);
}

// ------------------------------------------------------------ boot
document.addEventListener('keydown', (e) => {
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
  else if (mod && e.key === '.') { e.preventDefault(); companion.toggle(); }
  else if (e.key === 'Escape' && dropEl) { dropEl.remove(); dropEl = null; }
});
window.addEventListener('hashchange', mount);

buildShell();
document.documentElement.dataset.motion = store.get('motion', 'full');  // animations on by default, even when the OS asks for reduced motion
setTheme(store.get('theme', 'dark'));
companion = createCompanion({ act, getState: () => hub.state, route: () => app.route });
document.body.append(companion.el);
content.replaceChildren(h('div', { class: 'state' }, h('span', { class: 'spin' }), h('div', { class: 't' }, 'Connecting to the backend…'), h('div', { class: 'small' }, 'The first broker read can take a few seconds.')));
refreshState().then(mount);
loadPromotion();
setInterval(refreshState, POLL_MS);
setInterval(emitStatus, 5000);  // keeps "updated Xs ago" honest and flips to Stale if polls stop answering
