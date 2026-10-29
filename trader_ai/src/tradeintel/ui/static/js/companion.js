// The companion: a floating orb + compact conversation panel inside the app.
// It answers through POST /api/companion and never performs an action itself:
// proposals go through the shared action runner (confirmation + backend result).
// This is a web app, so the orb floats over this window only, not over other apps.
import { h, api, icon, badge, store } from './lib.js';

const QUICK = ['Explain current positions', 'Why is the agent waiting?', 'Summarize today', 'Show recent errors',
  'Why was that trade rejected?', 'Explain the last decision'];
const SIZE = 52;
const MARGIN = 16;

export function createCompanion({ act, getState, route }) {
  const wrap = h('div', { class: 'orb-wrap' });
  const orb = h('button', { class: 'orb', 'aria-label': 'Open companion', title: 'Companion (Ctrl+.)' });
  const count = h('span', { class: 'orb-count', style: { display: 'none' } });
  const bubble = h('div', { class: 'orb-bubble', role: 'status', style: { display: 'none' } });
  wrap.append(orb, count, bubble);

  const msgs = h('div', { class: 'cmsgs', 'aria-live': 'polite' });
  const status = h('div', { class: 'st' });
  const input = h('input', { class: 'input', placeholder: 'Ask about positions, decisions, risk…', 'aria-label': 'Message the companion' });
  const pinBtn = h('button', { class: 'btn icon ghost sm', title: 'Pin position', 'aria-label': 'Pin position', onclick: () => setPinned(!pinned) }, icon('pin'));
  const panelEl = h('div', { class: 'cpanel', role: 'dialog', 'aria-label': 'Companion', style: { display: 'none' } },
    h('div', { class: 'cpanel-h' }, h('span', { class: 'brand-mark' }), h('div', { style: { minWidth: 0 } }, h('div', { class: 't' }, 'Companion'), status),
      h('div', { class: 'tools' }, pinBtn,
        h('button', { class: 'btn icon ghost sm', title: 'Clear conversation', 'aria-label': 'Clear conversation', onclick: clear }, icon('refresh')),
        h('button', { class: 'btn icon ghost sm', title: 'Hide companion', 'aria-label': 'Hide companion', onclick: () => setHidden(true) }, icon('eyeoff')),
        h('button', { class: 'btn icon ghost sm', title: 'Collapse', 'aria-label': 'Collapse', onclick: close }, icon('minus')))),
    msgs,
    h('div', { class: 'chips' }, QUICK.map((q) => h('button', { class: 'chip', onclick: () => ask(q) }, q))),
    h('form', { class: 'cinput', onsubmit: (e) => { e.preventDefault(); ask(input.value); } }, input,
      h('button', { class: 'btn icon primary', type: 'submit', 'aria-label': 'Send' }, icon('send'))));
  const el = h('div', null, wrap, panelEl);

  let pos = store.get('cmp.pos', null);
  let pinned = store.get('cmp.pinned', false);
  let busy = null;          // 'thinking' | 'working' | null
  let lastState = null; let lastErr = null;
  const shownAlerts = new Set();
  let history = [];
  try { history = JSON.parse(sessionStorage.getItem('ti.cmp.history') || '[]'); } catch { history = []; }

  // ---------------------------------------------------------- placement & drag
  function clamp(p) {
    return { x: Math.min(Math.max(p.x, MARGIN), innerWidth - SIZE - MARGIN), y: Math.min(Math.max(p.y, MARGIN), innerHeight - SIZE - MARGIN) };
  }
  // pos stays null until the user drags, so the default follows the window's bottom-right corner
  const cur = () => clamp(pos || { x: innerWidth - SIZE - 24, y: innerHeight - SIZE - 24 });
  function place() {
    const p = cur();
    Object.assign(wrap.style, { left: `${p.x}px`, top: `${p.y}px` });
    const right = p.x > innerWidth / 2;
    const pw = Math.min(380, innerWidth - 24);
    const ph = Math.min(560, innerHeight - 90);
    const left = right ? p.x + SIZE - pw : p.x;
    const top = p.y - ph - 10 > 8 ? p.y - ph - 10 : Math.min(p.y + SIZE + 10, innerHeight - ph - 8);
    Object.assign(panelEl.style, { left: `${Math.max(12, Math.min(left, innerWidth - pw - 12))}px`, top: `${Math.max(8, top)}px` });
    Object.assign(bubble.style, right ? { right: '0', left: 'auto' } : { left: '0', right: 'auto' });
  }
  let drag = null;
  orb.addEventListener('pointerdown', (e) => {
    if (pinned) return;
    const p = cur();
    drag = { sx: e.clientX, sy: e.clientY, ox: p.x, oy: p.y, moved: false };
    orb.setPointerCapture(e.pointerId);
  });
  orb.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.sx; const dy = e.clientY - drag.sy;
    if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
    if (drag.moved) { pos = clamp({ x: drag.ox + dx, y: drag.oy + dy }); place(); }
  });
  orb.addEventListener('pointerup', () => {
    if (drag?.moved) { // snap to the nearest side edge
      pos = clamp({ x: pos.x + SIZE / 2 < innerWidth / 2 ? MARGIN : innerWidth - SIZE - MARGIN, y: pos.y });
      place(); store.set('cmp.pos', pos);
      orb._suppressClick = true;
    }
    drag = null;
  });
  orb.addEventListener('click', () => { if (orb._suppressClick) { orb._suppressClick = false; return; } toggle(); });
  window.addEventListener('resize', place);
  panelEl.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });

  function setPinned(v) {
    pinned = v; store.set('cmp.pinned', v);
    pinBtn.classList.toggle('on', v);
    pinBtn.title = v ? 'Unpin (allow dragging)' : 'Pin position';
    pinBtn.style.color = v ? 'var(--ai)' : '';
  }

  // ---------------------------------------------------------- visibility
  function open() { setHidden(false); panelEl.style.display = 'flex'; bubble.style.display = 'none'; place(); renderMsgs(); input.focus(); }
  function close() { panelEl.style.display = 'none'; orb.focus(); }
  function toggle() { if (store.get('cmp.hidden', false) || panelEl.style.display === 'none') open(); else close(); }
  function setHidden(v) {
    store.set('cmp.hidden', v);
    wrap.style.display = v ? 'none' : '';
    if (v) panelEl.style.display = 'none';
  }
  function resetPosition() { pos = null; store.set('cmp.pos', null); place(); }

  // ---------------------------------------------------------- state & notifications
  function update(st, err) {
    lastState = st; lastErr = err;
    renderOrb();
    if (!st) return;
    status.textContent = `${st.agent.state} · ${st.mode.label} · ${st.broker.available ? st.broker.environment : 'broker offline'}`;
    const fresh = st.alerts.filter((a) => (a.level === 'danger' || a.level === 'warn') && !shownAlerts.has(a.key));
    fresh.forEach((a) => shownAlerts.add(a.key));
    if (fresh.length && panelEl.style.display === 'none' && wrap.style.display !== 'none') {
      bubble.textContent = fresh[0].title + (fresh.length > 1 ? ` (+${fresh.length - 1} more)` : '');
      bubble.style.display = '';
      clearTimeout(bubble._t);
      bubble._t = setTimeout(() => { bubble.style.display = 'none'; }, 6000);
    }
  }
  function renderOrb() {
    const st = lastState;
    const alerts = (st?.alerts || []).filter((a) => a.level !== 'info');
    const danger = lastErr || st?.kill_switch.engaged || alerts.some((a) => a.level === 'danger');
    wrap.className = `orb-wrap ${busy || (lastErr ? 'error' : danger ? 'error' : alerts.length ? 'alert' : '')} ${pinned ? 'pinned' : ''}`;
    count.style.display = alerts.length ? '' : 'none';
    count.textContent = String(alerts.length);
    orb.setAttribute('aria-label', `Companion: ${lastErr ? 'backend disconnected' : st ? `agent ${st.agent.state}, ${alerts.length} alert(s)` : 'connecting'}`);
  }
  function setActivity(kind) { busy = kind; renderOrb(); }

  // ---------------------------------------------------------- conversation
  function save() { try { sessionStorage.setItem('ti.cmp.history', JSON.stringify(history.slice(-40))); } catch { /* ignore */ } }
  function push(m) { history.push(m); save(); renderMsgs(); }
  function clear() { history = []; save(); renderMsgs(); }

  function renderMsgs() {
    if (!history.length) {
      msgs.replaceChildren(h('div', { class: 'msg bot' }, h('div', { class: 'txt' },
        'I answer from this system\'s own records: broker, decision log, order audit, risk state. Actions I suggest only run after you confirm, and I report what the backend returns.')));
      return;
    }
    msgs.replaceChildren(...history.map(renderMsg));
    if (busy === 'thinking') msgs.append(h('div', { class: 'msg bot' }, h('div', { class: 'txt typing', 'aria-label': 'thinking' }, h('span'), h('span'), h('span'))));
    msgs.scrollTop = msgs.scrollHeight;
  }

  function renderMsg(m) {
    if (m.role === 'me') return h('div', { class: 'msg me' }, m.text);
    const src = { records: badge('from records', 'info'), llm: badge(`LLM-generated${m.model ? ' · ' + m.model : ''}`, 'ai'), system: badge('assistant'), backend: badge('backend result', 'pos'), error: badge('error', 'neg') }[m.source] || null;
    const kids = [h('div', { class: 'src' }, src), h('div', { class: 'txt' }, m.text)];
    if (m.link) kids.push(h('a', { href: m.link, class: 'small' }, 'Open related view →'));
    const p = m.proposal;
    if (p && !m.resolved) {
      kids.push(h('div', { class: `proposal ${p.danger ? 'danger' : ''}` }, h('div', { class: 'small' }, h('b', null, 'Proposed: '), p.label),
        h('div', { class: 'row' },
          h('button', { class: `btn sm ${p.danger ? 'danger' : 'primary'}`, onclick: () => runProposal(m) }, 'Confirm'),
          h('button', { class: 'btn sm ghost', onclick: () => { m.resolved = 'cancelled'; push({ role: 'bot', source: 'system', text: 'Cancelled. Nothing was done.' }); } }, 'Cancel'))));
    } else if (p) {
      kids.push(h('div', { class: 'small dim' }, `Proposal ${m.resolved}.`));
    }
    return h('div', { class: 'msg bot' }, kids);
  }

  async function runProposal(m) {
    const p = m.proposal;
    m.resolved = 'pending';
    renderMsgs();
    // the Confirm click is the confirmation for safe actions; dangerous ones get the full dialog too
    const r = await act(p.action, p.args || {}, { skipConfirm: !p.danger });
    if (!r) { m.resolved = 'cancelled'; push({ role: 'bot', source: 'system', text: 'Cancelled. Nothing was done.' }); return; }
    m.resolved = r.ok === false ? 'failed' : 'done';
    push({ role: 'bot', source: r.ok === false ? 'error' : 'backend', text: describeResult(p.action, r), link: p.action === 'backtest' || p.action === 'research' ? '#/research' : null });
  }

  function describeResult(action, r) {
    if (r.ok === false) return `The backend reported a failure: ${r.error}`;
    if (action === 'kill') return `Kill switch engaged at ${r.kill_switch?.at || 'now'}. ${r.note}`;
    if (action === 'run-once') return `Cycle result: ${JSON.stringify(r.result, null, 1)}`;
    if (action === 'backtest') {
      const x = r.metrics;
      return `In-sample backtest of ${r.strategy} (${r.source}): return ${(x.net_return * 100).toFixed(1)}%, Sharpe ${x.sharpe?.toFixed(2)}, max DD ${(x.max_drawdown * 100).toFixed(1)}%, ${x.n_trades} trades.\n${r.warning}`;
    }
    if (action === 'research') return `Experiment #${r.experiment_id}: ${r.gate.passed ? 'PASSED' : 'rejected'}${r.gate.reasons?.length ? '\n- ' + r.gate.reasons.join('\n- ') : ''}`;
    if (action === 'ingest') return `Stored ${r.result.rows} bars from ${r.result.source}, last ${r.result.last}.`;
    return JSON.stringify(r);
  }

  async function ask(text) {
    text = (text || '').trim();
    if (!text || busy === 'thinking') return;
    if (panelEl.style.display === 'none') open();
    input.value = '';
    push({ role: 'me', text });
    setActivity('thinking'); renderMsgs();
    const st = getState();
    const ctx = { page: route()?.id, symbol: route()?.id === 'markets' ? store.get('mk.symbol', st?.market.instrument) : st?.market.instrument, strategy: st?.strategy.name };
    try {
      const r = await api.post('/api/companion', { message: text, context: ctx });
      setActivity(null);
      push({ role: 'bot', source: r.source, model: r.model, text: r.reply, proposal: r.proposal || null, link: r.link || null });
    } catch (e) {
      setActivity(null);
      push({ role: 'bot', source: 'error', text: `I couldn't reach the backend: ${e.message}` });
    }
  }

  if (store.get('cmp.hidden', false)) wrap.style.display = 'none';
  setPinned(pinned);
  place();
  renderOrb();
  return { el, open, close, toggle, ask, update, setActivity, setHidden, resetPosition };
}
