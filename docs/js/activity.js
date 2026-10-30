// Live activity: what the system is doing right now, from the backend job table (/api/activity).
// Everything shown is a recorded state; nothing here animates on its own.
import { h, api, fmt } from './lib.js';

export const KIND_OF = { 'run-once': 'check', ingest: 'ingest', 'news-ingest': 'news', backtest: 'backtest', research: 'research', reconcile: 'reconcile' };
const SOURCE = { app: 'from this app', service: 'by the scheduler', cli: 'from the terminal' };
const STATE = {
  running: ['Running', 'run'], succeeded: ['Done', 'ok'], failed: ['Failed', 'bad'], lost: ['Stopped unexpectedly', 'bad'],
};

export function jobState(j) {
  if (j.state === 'running' && j.slow) return ['Taking longer than usual', 'slow'];
  return STATE[j.state] || [j.state, ''];
}

export function stateBadge(j) {
  const [text, tone] = jobState(j);
  return h('span', { class: `jstate ${tone}` }, h('i', { 'aria-hidden': 'true' }), text);
}

const decisionWord = (a) => ({ abstain: 'no trade, the AI held back', hold: 'no change', exit: 'wants to close the position', enter_long: 'wants to buy', enter_short: 'wants to sell short' })[a] || a || 'done';

// one sentence for how a finished job ended
export function jobOutcome(j) {
  if (j.state === 'failed' || j.state === 'lost') return j.error || 'failed';
  const r = j.result || {};
  if (j.kind === 'check') {
    const d = r.result || {};
    if (!d.decision) return d.reason || d.status || 'done';
    return decisionWord(d.decision) + (d.execution ? ` · order ${String(d.execution.status).replace(/_/g, ' ')}` : '');
  }
  if (j.kind === 'ingest') return r.result ? `${r.result.rows} daily bars, latest ${fmt.date(r.result.last)}` : 'done';
  if (j.kind === 'backtest') return r.metrics ? `return ${fmt.pct(r.metrics.net_return, 1)}, Sharpe ${fmt.num(r.metrics.sharpe)}` : 'done';
  if (j.kind === 'research') return r.gate ? (r.gate.passed ? 'passed the safety gate' : 'failed the safety gate') : 'done';
  if (j.kind === 'reconcile') return `${r.updated ?? 0} order(s) updated`;
  return 'done';
}

export function stepLine(j) {
  const i = Math.max(0, j.steps.indexOf(j.step));
  return j.steps.length > 1 ? `Step ${i + 1} of ${j.steps.length}: ${j.step}` : j.step;
}

export function stepList(j) {
  const cur = j.steps.indexOf(j.step);
  return h('ol', { class: 'jsteps' }, j.steps.map((st, i) => {
    const done = j.done_steps.includes(st);
    const live = j.state === 'running' && st === j.step;
    const failedHere = (j.state === 'failed' || j.state === 'lost') && st === j.step;
    const cls = done ? 'done' : live ? 'live' : failedHere ? 'bad' : j.state === 'running' || i < cur ? 'todo' : 'skip';
    return h('li', { class: cls }, h('i', { 'aria-hidden': 'true' }), st, cls === 'skip' ? h('span', { class: 'dim' }, ' (not needed)') : null);
  }));
}

export function createActivity({ onChange }) {
  const el = h('button', { class: 'hbtn activity', type: 'button', 'aria-expanded': 'false', 'aria-label': 'Background work. Click for details.' });
  const drop = h('div', { class: 'act-drop', hidden: true });
  let data = null; let open = false; let timer = 0; let offline = false;

  el.addEventListener('click', (e) => { e.stopPropagation(); open = !open; render(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && open) { open = false; render(); } });
  document.addEventListener('click', (e) => { if (open && !drop.contains(e.target)) { open = false; render(); } });

  function render() {
    if (!data && !offline) { el.replaceChildren(h('span', { class: 'act-main' }, h('span', { class: 'jdot' }), h('span', { class: 't muted' }, 'Checking…'))); return; }
    const run = data?.running[0];
    const last = data?.recent.find((j) => j.state !== 'running');
    const svc = data?.service || {};
    el.className = `hbtn activity ${offline ? 'bad' : run ? (run.slow ? 'slow' : 'run') : last && last.state !== 'succeeded' ? 'bad' : ''}`;
    let main;
    if (offline) {
      main = [h('span', { class: 'jdot' }), h('span', { class: 't' }, 'Backend offline')];
      el.title = 'Can’t reach the app’s backend. Is `tradeintel ui` still running? Retrying…';
    } else if (run) {
      const pct = run.steps.length ? Math.round(100 * (run.done_steps.length + 0.5) / run.steps.length) : 50;
      main = [h('span', { class: 'jdot' }), h('span', { class: 't' }, run.label, data.running.length > 1 ? ` +${data.running.length - 1}` : ''),
        h('span', { class: 'jbar', role: 'progressbar', 'aria-label': `${run.label} progress`, 'aria-valuenow': pct, 'aria-valuemin': 0, 'aria-valuemax': 100 }, h('i', { style: { width: `${pct}%` } })),
        h('span', { class: 'mono small muted' }, `${Math.round(run.elapsed_s)}s`)];
      el.title = `${stepLine(run)}${run.slow ? ` · usually about ${run.expected_s} s` : ''}`;
    } else {
      main = [h('span', { class: 'jdot' }), h('span', { class: 't' }, 'Idle')];
      el.title = last ? `Last: ${last.label}: ${last.state === 'succeeded' ? jobOutcome(last) : 'FAILED, ' + jobOutcome(last)} (${fmt.ago(last.finished_at || last.started_at)})` : 'Nothing has run yet';
    }
    el.replaceChildren(h('span', { class: 'act-main' }, main));
    el.setAttribute('aria-expanded', String(open));
    drop.hidden = !open || !data;
    if (open && data) {
      drop.replaceChildren(
        run ? h('div', { class: 'act-sec' }, h('div', { class: 'act-h' }, `Now: ${run.label}`), stepList(run)) : null,
        h('div', { class: 'act-sec' }, h('div', { class: 'act-h' }, 'Recent work (newest first)'),
          data.recent.length ? h('table', { class: 'tbl' }, h('tbody', null, data.recent.slice(0, 8).map((j) => h('tr', null,
            h('td', null, stateBadge(j)), h('td', null, j.label, h('div', { class: 'small muted' }, j.state === 'running' ? stepLine(j) : jobOutcome(j))),
            h('td', { class: 'small muted nowrap' }, SOURCE[j.source] || j.source), h('td', { class: 'small muted nowrap num' }, `${Math.round(j.elapsed_s)} s`),
            h('td', { class: 'small dim nowrap' }, fmt.ago(j.started_at)))))) : h('div', { class: 'small muted' }, 'Nothing has run yet.')),
        h('div', { class: 'small dim', style: { padding: '0 16px 14px' } },
          svc.alive ? `The scheduler (tradeintel service) runs a check every ${Math.round(svc.interval_s / 60)} min.` : 'Checks run only when you press “Run a new check”. For automatic checks, run `tradeintel service` in a terminal.'));
    }
  }

  async function poll() {
    clearTimeout(timer);
    try {
      data = await api.get('/api/activity');
      offline = false;
    } catch {
      offline = true;
    }
    render();
    onChange?.(data, offline);
    timer = setTimeout(poll, data?.running.length ? 1000 : 4000);
  }

  // Follow one job until it ends. Survives brief network drops; gives up after ~60 s with no contact.
  async function track(id, onTick) {
    let misses = 0;
    for (;;) {
      let j;
      try {
        j = await api.get(`/api/jobs/${id}`);
        misses = 0;
      } catch {
        if (++misses > 60) return { state: 'failed', error: 'Lost contact with the app. The task may still be running: see the activity bar.' };
        onTick?.(null);
        await new Promise((r) => setTimeout(r, 1000));
        continue;
      }
      onTick?.(j);
      if (j.state !== 'running') { poll(); return j; }
      await new Promise((r) => setTimeout(r, 700));
    }
  }

  const isRunning = (kind) => !!data?.running.some((j) => j.kind === kind);

  render();
  poll();
  return { el, drop, track, isRunning, refresh: poll };
}
