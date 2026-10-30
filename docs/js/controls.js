// One status model for every trading process, derived only from the polled backend state
// (/api/state), the job table (/api/activity) and requests still in flight. Every control that
// starts or stops something renders from it, so all screens always agree.
import { h, fmt, icon, SNAPSHOT } from './lib.js';
import { stepLine, jobOutcome } from './activity.js';

// app.js fills this in; `pending` maps action name -> request start (ms) until the backend confirms it
export const hub = { state: null, stateAt: 0, error: null, activity: null, pending: new Map(), promoted: null, act: null };
const STALE_MS = 35000;  // state polls every 10 s; three missed polls = stale

export const killReason = (r) => (r || 'no reason recorded').replace(/^ui: engaged from UI$/, 'turned on from this app').replace(/^ui: /, '');

// Each entry: { key, label, tone: pos|neg|warn|accent|mute, sub, busy?, live? }
export function deriveStatus(x = hub, now = Date.now()) {
  const st = x.state;
  const offline = !!x.error || !st;
  const p = x.pending;
  const unknown = { key: 'unknown', label: 'Unknown', tone: 'mute', sub: 'Can’t be verified while disconnected' };
  const out = {};
  const ago = fmt.ago(x.stateAt || null);

  if (SNAPSHOT && st) out.conn = { key: 'snapshot', label: 'Snapshot', tone: 'warn', sub: `Recorded ${fmt.time(st.server_time)} · read-only, not live` };
  else if (!st && !x.error) out.conn = { key: 'connecting', label: 'Connecting…', tone: 'accent', busy: true, sub: 'Waiting for the first reply from the app backend' };
  else if (offline) out.conn = { key: 'offline', label: 'Disconnected', tone: 'neg', sub: `App backend unreachable${st ? ` · last data ${ago}` : ''}. Retrying…` };
  else if (now - x.stateAt > STALE_MS) out.conn = { key: 'stale', label: 'Stale', tone: 'warn', sub: `No fresh data since ${ago}` };
  else if (!st.broker.available) out.conn = { key: 'broker', label: 'Broker offline', tone: 'neg', sub: st.broker.error || 'Account can’t be read' };
  else out.conn = { key: 'live', label: 'Connected', tone: 'pos', live: true, sub: `${st.broker.environment === 'paper' ? 'Internal paper' : `${cap(st.broker.broker)} paper`} · updated ${ago}` };

  if (offline) out.engine = unknown;
  else if (st.kill_switch.engaged) out.engine = { key: 'halted', label: 'Halted', tone: 'neg', sub: 'Emergency stop blocks every new order' };
  else if (!st.mode.execution_enabled) out.engine = { key: 'observe', label: 'Watching only', tone: 'mute', sub: 'Orders are off in config; decisions are only recorded' };
  else if (st.mode.require_promotion && x.promoted === false) out.engine = { key: 'blocked', label: 'On · blocked', tone: 'warn', sub: `${st.strategy.name} hasn’t passed testing, so nothing is sent` };
  else out.engine = { key: 'armed', label: 'Paper orders on', tone: 'pos', sub: `${st.strategy.name} can send paper orders` };

  if (p.has('kill')) out.kill = { key: 'engaging', label: 'Engaging…', tone: 'warn', busy: true, sub: 'Waiting for the backend to confirm' };
  else if (p.has('reset-kill')) out.kill = { key: 'releasing', label: 'Releasing…', tone: 'warn', busy: true, sub: 'Waiting for the backend to confirm' };
  else if (offline) out.kill = unknown;
  else if (st.kill_switch.engaged) out.kill = { key: 'on', label: 'Engaged', tone: 'neg', sub: `${killReason(st.kill_switch.reason)}${st.kill_switch.at ? ` · ${fmt.ago(st.kill_switch.at)}` : ''}` };
  else out.kill = { key: 'off', label: 'Off', tone: 'pos', sub: 'New orders still need every limit to pass' };

  const act = x.activity || st?.activity;
  const svc = act?.service || {};
  if (offline) out.sched = unknown;
  else if (svc.alive) out.sched = { key: 'running', label: 'Running', tone: 'pos', live: true, sub: `Next check ${fmt.ago(svc.next_at)} · every ${Math.round((svc.interval_s || 0) / 60)} min` };
  else if (svc.overdue) out.sched = { key: 'error', label: 'Not responding', tone: 'neg', sub: `Last heartbeat ${fmt.ago(svc.at)} · restart tradeintel service` };
  else out.sched = { key: 'stopped', label: 'Stopped', tone: 'mute', sub: 'Start with `tradeintel service` in a terminal' };

  const b = st?.broker;
  if (offline || !b?.available) out.pos = { ...unknown, sub: offline ? unknown.sub : 'Broker offline: holdings unknown' };
  else {
    const pos = b.positions || [];
    const orders = b.open_orders ? ` · ${b.open_orders.length} order${b.open_orders.length === 1 ? '' : 's'} waiting` : '';
    out.pos = pos.length
      ? { key: 'open', label: pos.map((q) => `${q.units > 0 ? 'Long' : 'Short'} ${fmt.num(Math.abs(q.units), 0)} ${q.instrument}`).join(', '), tone: 'accent', sub: `Avg ${pos.map((q) => fmt.num(q.avg_price)).join(', ')}${orders}` }
      : { key: 'flat', label: 'Flat', tone: 'mute', sub: `No open position${orders}` };
  }

  const run = act?.running.find((j) => j.kind === 'check');
  const last = act?.recent.find((j) => j.kind === 'check' && j.state !== 'running');
  if (run) out.check = { key: 'running', label: run.slow ? 'Running (slow)' : 'Running', tone: run.slow ? 'warn' : 'accent', busy: true, sub: stepLine(run) };
  else if (p.has('run-once')) {
    // pending maps action -> request start (ms); a check that ended after that is ours, now loading its result
    const ended = last && p.get?.('run-once') <= Date.parse(last.finished_at || '');
    out.check = ended ? { key: 'finishing', label: 'Finishing…', tone: 'accent', busy: true, sub: 'Loading the result' }
      : { key: 'starting', label: 'Starting…', tone: 'accent', busy: true, sub: 'Asking the backend to start a check' };
  }
  else if (offline) out.check = unknown;
  else if (last && last.state !== 'succeeded') out.check = { key: 'failed', label: 'Last check failed', tone: 'neg', sub: last.error || 'It stopped unexpectedly' };
  else out.check = { key: 'idle', label: 'Idle', tone: 'mute', sub: last ? `Last ${fmt.ago(last.finished_at || last.started_at)}: ${jobOutcome(last)}` : 'No check has run yet' };
  return out;
}
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : '');

export function statusBadge(s) {
  return h('span', { class: `sbadge ${s.tone}${s.busy ? ' busy' : ''}${s.live ? ' live' : ''}` }, h('i', { 'aria-hidden': 'true' }), s.label);
}

// ------------------------------------------------------------ live wiring
// app.js dispatches 'ti:status' whenever state, activity or a pending request changes.
export const emitStatus = () => document.dispatchEvent(new Event('ti:status'));
function onStatus(el, draw) {
  draw();
  const fn = () => { if (el._mounted && !el.isConnected) document.removeEventListener('ti:status', fn); else { el._mounted ||= el.isConnected; draw(); } };
  document.addEventListener('ti:status', fn);
  return el;
}

// The same <button> is updated in place (never replaced), so keyboard focus survives re-renders.
function liveButton(spec, { sm = false } = {}) {
  const b = h('button', { type: 'button', class: 'btn' });
  let cur = null;
  b.addEventListener('click', () => { if (cur?.run && !b.disabled) cur.run(); });
  return onStatus(b, () => {
    cur = spec(deriveStatus());
    if (SNAPSHOT) cur = { ...cur, busy: false, disabled: true, title: 'Read-only snapshot: run `tradeintel ui` on your PC to use this' };
    b.className = `btn ${sm ? 'sm ' : ''}${cur.cls || ''}`;
    b.disabled = !!cur.disabled;
    b.title = cur.title || '';
    b.setAttribute('aria-busy', String(!!cur.busy));
    b.replaceChildren(cur.busy ? h('span', { class: 'spin' }) : cur.icon ? icon(cur.icon) : '', cur.label);
  });
}

// Emergency stop: one button whose action follows the confirmed state.
export const killControl = (o) => liveButton((s) => ({
  off: { label: 'Stop new orders', cls: 'danger', icon: 'bolt', title: 'Block every new order until you release it', run: () => hub.act('kill') },
  on: { label: 'Release stop…', icon: 'unlock', title: 'Allow new orders again (you type RESET)', run: () => hub.act('reset-kill', { confirm: 'RESET' }) },
  engaging: { label: 'Engaging…', busy: true, disabled: true, cls: 'danger' },
  releasing: { label: 'Releasing…', busy: true, disabled: true },
}[s.kill.key] || { label: 'Stop new orders', icon: 'bolt', disabled: true, title: 'State can’t be verified while disconnected' }), o);

// Decision check: never offers "Run" while one is starting or running (from any tab, the terminal or the scheduler).
export const checkControl = (o) => liveButton((s) => {
  if (s.check.key === 'running') return { label: 'Checking…', busy: true, disabled: true, cls: 'primary', title: 'A check is already running; it won’t start twice' };
  if (s.check.busy) return { label: s.check.label, busy: true, disabled: true, cls: 'primary' };
  if (s.check.key === 'unknown') return { label: 'Run check', icon: 'play', disabled: true, cls: 'primary', title: 'Disconnected' };
  return { label: s.check.key === 'failed' ? 'Retry check' : 'Run check', icon: 'play', cls: 'primary', title: 'Look at the latest prices and record what the AI would do', run: () => hub.act('run-once') };
}, o);

// ------------------------------------------------------------ the status strip under the header
export function statusStrip() {
  const cells = [
    ['conn', 'Connection'], ['engine', 'Order engine', 'Orders are switched on or off in config/local.yaml (execution.enabled), on purpose not from the app.'],
    ['kill', 'Emergency stop', null, killControl], ['sched', 'Scheduler', 'Automatic checks run only while `tradeintel service` runs in a terminal.'],
    ['pos', 'Position', 'What the broker account actually holds. A running scheduler does not mean a position is open.'], ['check', 'AI check', null, checkControl],
  ].map(([key, title, hint, ctl]) => {
    const v = h('div', { class: 'cell-v' });
    const sub = h('div', { class: 'cell-s' });
    const el = h('div', { class: 'cell', role: 'group', 'aria-label': title, title: hint || '' },
      h('div', { class: 'cell-k' }, title), v, sub, ctl ? h('div', { class: 'cell-a' }, ctl({ sm: true })) : null);
    return { key, el, v, sub, last: null };
  });
  const el = h('section', { class: 'strip', 'aria-label': 'System status' }, cells.map((c) => c.el));
  return onStatus(el, () => {
    const s = deriveStatus();
    for (const c of cells) {
      const x = s[c.key];
      const badgeEl = statusBadge(x);
      if (c.last && c.last !== x.label) badgeEl.classList.add('chg');  // animate only real transitions
      c.last = x.label;
      c.v.replaceChildren(badgeEl);
      c.sub.textContent = x.sub;
      c.sub.title = x.sub;
    }
  });
}
