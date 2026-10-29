// Screens. Each page is (ctx) => { el, destroy? }. Every number shown comes from
// the backend; anything unavailable is labelled as such.
import { h, s, api, fmt, when, panel, flush, badge, table, kv, stat, meter, loadInto, emptyState, errorState, pre, icon, store } from './lib.js';

const LWC = window.LightweightCharts;
const C = { bg: '#111418', grid: '#191d22', text: '#7d8590', border: '#22272e', pos: '#2ebd85', neg: '#f0565b', ai: '#8b7cf6', info: '#5aa9ff', warn: '#e8a13a', sma50: '#5aa9ff', sma200: '#c79bff' };
const ACTION_TONE = { enter_long: 'pos', enter_short: 'neg', exit: 'warn', hold: '', abstain: 'ai' };
const STANCE_TONE = { long: 'pos', short: 'neg', neutral: '', abstain: 'ai' };
const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : null);

function chartBase(el, opts = {}) {
  if (!LWC) { el.replaceChildren(emptyState('Chart library failed to load')); return null; }
  return LWC.createChart(el, {
    autoSize: true,
    layout: { background: { type: 'solid', color: C.bg }, textColor: C.text, fontFamily: getComputedStyle(document.body).fontFamily, fontSize: 11 },
    grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
    rightPriceScale: { borderColor: C.border },
    timeScale: { borderColor: C.border, rightOffset: 4 },
    crosshair: { mode: 0 },
    ...opts,
  });
}

// Candles + overlays + markers from /api/bars. Returns { chart, destroy }.
function priceChart(el, d, show = {}) {
  const chart = chartBase(el);
  if (!chart) return { destroy() {} };
  const candles = chart.addCandlestickSeries({ upColor: C.pos, downColor: C.neg, borderVisible: false, wickUpColor: C.pos, wickDownColor: C.neg, priceLineVisible: false });
  candles.setData(d.candles.filter((c) => c.open != null && c.close != null));
  const vols = d.candles.filter((c) => c.volume != null);
  if (show.volume !== false && vols.length) {
    const v = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: '', lastValueVisible: false, priceLineVisible: false });
    v.priceScale().applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
    v.setData(vols.map((c) => ({ time: c.time, value: c.volume, color: c.close >= c.open ? 'rgba(46,189,133,.28)' : 'rgba(240,86,91,.28)' })));
  }
  if (show.sma !== false) {
    for (const [k, color] of [['sma50', C.sma50], ['sma200', C.sma200]]) {
      if (d.overlays[k].length) chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }).setData(d.overlays[k]);
    }
  }
  const marks = [];
  if (show.signals !== false) {
    for (const m of d.markers) {
      marks.push(m.target === 1 ? { time: m.time, position: 'belowBar', color: C.pos, shape: 'arrowUp', text: 'L' }
        : m.target === -1 ? { time: m.time, position: 'aboveBar', color: C.neg, shape: 'arrowDown', text: 'S' }
          : { time: m.time, position: 'aboveBar', color: C.text, shape: 'circle', text: 'flat' });
    }
  }
  if (show.decisions !== false) {
    for (const x of d.decisions) marks.push({ time: x.time, position: 'inBar', color: C.ai, shape: 'square', text: x.action });
  }
  candles.setMarkers(marks.sort((a, b) => a.time - b.time));
  if (show.lines !== false) for (const l of d.price_lines) candles.createPriceLine({ price: l.price, color: C.warn, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: l.label });
  const legend = h('div', { class: 'legend' });
  el.append(legend);
  const last = d.candles[d.candles.length - 1];
  const setLegend = (c) => {
    if (!c) return;
    legend.textContent = `${d.instrument} ${d.timeframe}  O ${fmt.num(c.open)}  H ${fmt.num(c.high)}  L ${fmt.num(c.low)}  C ${fmt.num(c.close)}`;
  };
  setLegend(last);
  chart.subscribeCrosshairMove((p) => setLegend(p?.seriesData?.get(candles) || last));
  chart.timeScale().fitContent();
  return { chart, destroy: () => chart.remove() };
}

function lineChart(el, series) {
  const chart = chartBase(el);
  if (!chart) return { destroy() {} };
  for (const sr of series) chart.addLineSeries({ color: sr.color, lineWidth: sr.width || 1.5, priceLineVisible: false, title: sr.title }).setData(sr.data.filter((p) => p.value != null));
  chart.timeScale().fitContent();
  return { chart, destroy: () => chart.remove() };
}

function head(title, sub, ...tools) {
  return h('div', { class: 'page-head' }, h('p', null, sub), tools.length ? h('div', { class: 'row', style: { marginLeft: 'auto' } }, tools) : null);
}

function sourceBadge(src) {
  if (!src) return badge('no data');
  return src === 'SYNTHETIC' ? badge('SYNTHETIC', 'warn') : badge(src, 'info');
}

function actionBadge(a) { return badge(a.replace('_', ' '), ACTION_TONE[a] || ''); }

function envLabel(st) {
  const b = st.broker;
  if (!b.available) return { text: 'DISCONNECTED', tone: 'bad' };
  return { text: b.environment === 'paper' ? 'INTERNAL PAPER' : `PAPER · ${b.broker.toUpperCase()}`, tone: 'env' };
}

// ============================================================ Overview
export function overview(ctx) {
  const st = ctx.state;
  const el = h('div', { class: 'stack', style: { gap: '12px' } });
  const strip = h('div', { class: 'strip' });
  const chartSlot = h('div', { class: 'chart' });
  const agentSlot = h('div');
  const watchSlot = h('div');
  const riskSlot = h('div');
  const alertSlot = h('div');
  const actSlot = h('div');
  let chartObj = null;

  el.append(strip,
    h('div', { class: 'grid' },
      h('div', { class: 'c8' }, panel(`${st.market.instrument} · ${st.market.timeframe}`, flush(chartSlot), {
        meta: 'stored daily bars · strategy signals · recorded decisions',
        tools: h('a', { class: 'btn sm ghost', href: '#/markets' }, 'Open in Markets'),
      })),
      h('div', { class: 'c4' }, panel('Agent', agentSlot, { tools: h('a', { class: 'btn sm ghost', href: '#/agents' }, 'Inspect') })),
      h('div', { class: 'c4' }, panel('Watchlist', flush(watchSlot), { meta: 'stored series' })),
      h('div', { class: 'c4' }, panel('Risk', riskSlot, { tools: h('a', { class: 'btn sm ghost', href: '#/risk' }, 'Risk center') })),
      h('div', { class: 'c4' }, panel('Alerts & events', flush(alertSlot))),
      h('div', { class: 'c12' }, panel('Recent activity', flush(actSlot), { meta: 'decisions and order attempts' }))));

  renderStrip(strip, st);
  loadInto(chartSlot, (sg) => api.get(`/api/bars?limit=260`, sg), (d) => {
    chartObj?.destroy();
    if (!d.available) return emptyState(d.error);
    const box = h('div', { class: 'chart' });
    queueMicrotask(() => { chartObj = priceChart(box, d, { volume: true }); });
    return box;
  });
  agentSlot.replaceChildren(agentCard(st));
  loadInto(watchSlot, (sg) => api.get('/api/watchlist', sg), (rows) => table([
    { label: 'Symbol', render: (r) => h('a', { href: `#/markets?symbol=${encodeURIComponent(r.instrument)}` }, r.instrument) },
    { label: 'Last close', num: true, render: (r) => fmt.num(r.last_close, r.last_close > 50 ? 2 : 5) },
    { label: 'Chg', num: true, render: (r) => h('span', { class: fmt.tone(r.change_pct) }, fmt.pct(r.change_pct, 2, true)) },
    { label: 'Spread', num: true, render: (r) => (r.quote ? fmt.num(r.quote.spread, 2) : h('span', { class: 'dim', title: 'live quote only for the traded instrument' }, '—')) },
    { label: 'Bar', render: (r) => h('span', { class: 'muted small' }, fmt.date(r.last)) },
  ], rows, { empty: 'No stored bars. Run Refresh data.' }));
  loadInto(riskSlot, (sg) => api.get('/api/risk', sg), riskSummary);
  loadInto(alertSlot, (sg) => api.get('/api/news', sg), (n) => alertList(st.alerts, n.events));
  loadInto(actSlot, (sg) => Promise.all([api.get('/api/decisions?limit=8', sg), api.get('/api/audit?limit=8', sg)]), ([ds, au]) => activityTable(ds, au));

  return {
    el,
    onState(nst) { renderStrip(strip, nst); agentSlot.replaceChildren(agentCard(nst)); },
    destroy() { chartObj?.destroy(); },
  };
}

function renderStrip(strip, st) {
  const b = st.broker;
  const q = b.quote;
  const ccy = b.currency || '';
  const pos = b.positions || [];
  let unreal = null;
  if (b.available) {
    unreal = 0;
    for (const p of pos) {
      if (q && p.instrument === q.instrument) unreal += ((q.bid + q.ask) / 2 - p.avg_price) * p.units;
      else { unreal = null; break; }
    }
  }
  const acc = st.account_state || {};
  const day = b.available && acc.day_start_equity ? b.equity - acc.day_start_equity : null;
  const dd = b.available && acc.hwm ? (Math.max(acc.hwm, b.equity) - b.equity) / Math.max(acc.hwm, b.equity) : null;
  const notional = pos.reduce((a, p) => a + Math.abs(p.units) * p.avg_price, 0);
  strip.replaceChildren(
    stat('Equity', b.available ? fmt.money(b.equity, ccy) : '—', b.available ? `${b.broker} · ${b.environment}` : 'broker unavailable'),
    stat('Cash', b.available ? fmt.money(b.cash, ccy) : '—'),
    stat('Unrealized P&L', unreal == null ? '—' : fmt.signed(unreal), unreal == null ? 'needs a quote per position' : 'mid vs average entry', fmt.tone(unreal)),
    stat('Day P&L', day == null ? '—' : fmt.signed(day), day == null ? 'no day-start equity recorded yet' : `vs ${fmt.money(acc.day_start_equity)}`, fmt.tone(day)),
    stat('Exposure', b.available ? fmt.money(notional, ccy, 0) : '—', b.available && b.equity ? `${fmt.num(notional / b.equity, 2)}x equity` : ''),
    stat('Drawdown', dd == null ? '—' : fmt.pct(dd), dd == null ? 'no high-water mark yet' : 'from high-water mark', dd > 0 ? 'neg' : ''),
    stat('Positions / orders', b.available ? `${pos.length} / ${b.open_orders ? b.open_orders.length : '—'}` : '—', b.open_orders ? 'broker open orders' : 'broker has no order list'),
    stat(`${st.market.instrument} quote`, q ? `${fmt.num(q.bid)} / ${fmt.num(q.ask)}` : '—', q ? `spread ${fmt.num(q.spread)} · ${fmt.ago(q.time)}` : (b.quote_error || 'no quote')),
  );
}

function agentCard(st) {
  const d = st.agent.last_decision;
  const kids = [h('div', { class: 'row' }, badge(st.agent.state, st.agent.state === 'halted' ? 'neg' : 'ai'),
    h('span', { class: 'muted small' }, st.mode.label))];
  if (!d) {
    kids.push(emptyState('No decision recorded yet', 'Run a decision cycle (Ctrl+K → "Run decision cycle") or `tradeintel run-once`.'));
  } else {
    kids.push(kv([
      ['Last decision', h('span', null, actionBadge(d.action), ' ', when(d.decided_at, 'ago'))],
      ['Strategy', `${d.strategy} · ${d.instrument}`],
      ['Rationale', (d.reasons || []).join('; ')],
    ]));
    kids.push(h('div', { class: 'row' }, (d.reports || []).map((r) => h('span', { title: [...(r.evidence || []), ...(r.missing || []), ...(r.objections || [])].join('\n') },
      badge(`${r.agent}: ${r.stance}`, STANCE_TONE[r.stance])))));
    if (d.action.startsWith('enter_')) {
      const inp = d.inputs || {};
      const sig = inp.signal || {};
      const dir = d.action === 'enter_long' ? 1 : -1;
      kids.push(h('div', { class: 'note ai' }, h('b', null, 'Trade proposal (recorded) '),
        `${d.action === 'enter_long' ? 'Long' : 'Short'} ${d.instrument}. Invalidation: stop ≈ ${fmt.num(inp.last_close - dir * (sig.stop_dist || NaN))} (close ∓ stop distance). `,
        `Valid for the bar after ${fmt.date(inp.last_bar)} only. No confidence score exists; the risk engine and promotion gate decide.`));
    }
  }
  kids.push(h('div', { class: 'small muted' }, st.agent.note));
  return h('div', { class: 'stack' }, kids);
}

function riskSummary(r) {
  const rows = r.utilisation.filter((u) => ['Daily loss', 'Drawdown from high-water mark', 'Leverage', 'Spread (current quote)'].includes(u.limit));
  return h('div', { class: 'stack' },
    h('div', { class: 'row' }, 'Kill switch', r.kill_switch.engaged ? badge('engaged', 'neg') : badge('clear', 'pos')),
    rows.map(utilRow));
}

function utilRow(u) {
  const shown = u.value == null ? '—' : u.unit === 'pct' ? fmt.pct(u.value) : u.unit === 'x' ? `${fmt.num(u.value)}x` : fmt.num(u.value, u.unit === 'count' ? 0 : 2);
  const max = u.unit === 'pct' ? fmt.pct(u.max) : u.unit === 'x' ? `${u.max}x` : u.max;
  return h('div', { title: u.note || '' },
    h('div', { class: 'row', style: { justifyContent: 'space-between' } }, h('span', { class: 'small' }, u.limit), h('span', { class: 'mono small' }, `${shown} / ${max}`)),
    u.value == null ? h('div', { class: 'small dim' }, u.note || 'unavailable') : meter(u.value / u.max));
}

function alertList(alerts, events) {
  const now = Date.now();
  const upcoming = (events || []).filter((e) => e.impact === 'high' && new Date(e.event_time) > now).slice(0, 6);
  const items = alerts.map((a) => h('div', { class: `alert-item ${a.level}` }, h('div', { class: 'bar' }),
    h('div', null, h('div', null, a.title), a.detail ? h('div', { class: 'small muted' }, a.detail) : null, a.at ? h('div', { class: 'small dim' }, when(a.at, 'ago')) : null)));
  for (const e of upcoming) {
    items.push(h('div', { class: 'alert-item' }, h('div', { class: 'bar' }),
      h('div', null, h('div', null, `${e.currency} · ${e.title}`), h('div', { class: 'small muted' }, 'high impact · ', when(e.event_time)))));
  }
  return items.length ? h('div', null, items) : emptyState('No alerts', 'No upcoming high-impact events stored. Refresh news on the News page.');
}

function activityTable(ds, au) {
  const rows = [
    ...ds.map((d) => ({ at: d.decided_at, kind: 'decision', summary: [actionBadge(d.action), ' ', `${d.strategy} · ${d.instrument}: ${(d.reasons || []).join('; ')}`], link: `#/agents?decision=${d.id}` })),
    ...au.map((a) => ({ at: a.at, kind: 'order', summary: [badge(a.order_result?.status || '—', a.risk?.approved ? 'pos' : 'warn'), ' ', a.idempotency_key, a.risk && !a.risk.approved ? ` (${(a.risk.reasons || []).join('; ')})` : ''], link: '#/execution' })),
  ].sort((a, b) => (a.at < b.at ? 1 : -1)).slice(0, 12);
  return table([
    { label: 'Time', render: (r) => when(r.at) },
    { label: 'Type', render: (r) => badge(r.kind, r.kind === 'decision' ? 'ai' : 'info') },
    { label: 'Summary', render: (r) => h('a', { href: r.link, style: { color: 'inherit' } }, r.summary) },
  ], rows, { empty: 'No decisions or order attempts recorded yet.' });
}

// ============================================================ Markets
export function markets(ctx) {
  const st = ctx.state;
  let symbol = ctx.params.symbol || store.get('mk.symbol', st.market.instrument);
  let range = store.get('mk.range', 252);
  const show = store.get('mk.show', { volume: true, sma: true, signals: true, decisions: true, lines: true });
  let chartObj = null;
  const chartSlot = h('div', { class: 'chart tall' });
  const side = h('div', { class: 'stack' });
  const symSel = h('select', { class: 'input', 'aria-label': 'Symbol', onchange: (e) => { symbol = e.target.value; store.set('mk.symbol', symbol); load(); } });
  const ranges = [['3M', 63], ['6M', 126], ['1Y', 252], ['5Y', 1260], ['All', 0]];
  const rangeSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Range' }, ranges.map(([l, n]) => h('button', {
    class: n === range ? 'on' : '', onclick: (e) => { range = n; store.set('mk.range', n); [...rangeSeg.children].forEach((b) => b.classList.remove('on')); e.target.classList.add('on'); load(); },
  }, l)));
  const tfSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Timeframe' }, ['1h', '4h', '1d'].map((tf) => h('button', {
    class: tf === st.market.timeframe ? 'on' : '', disabled: tf !== st.market.timeframe, title: tf === st.market.timeframe ? '' : 'not stored: only the configured timeframe is ingested',
  }, tf.toUpperCase())));
  const toggles = [['volume', 'Volume'], ['sma', 'SMA 50/200'], ['signals', 'Signals'], ['decisions', 'Decisions'], ['lines', 'Position lines']].map(([k, l]) =>
    h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: show[k] !== false, onchange: (e) => { show[k] = e.target.checked; store.set('mk.show', show); load(); } }), l));

  const el = h('div', { class: 'stack', style: { gap: '12px' } },
    h('div', { class: 'row' }, symSel, tfSeg, rangeSeg, h('span', { style: { flex: 1 } }), toggles),
    h('div', { class: 'grid' }, h('div', { class: 'c9' }, panel('Chart', flush(chartSlot), { meta: 'closed bars from the local store · no live ticks' })),
      h('div', { class: 'c3' }, side)));

  api.get('/api/watchlist').then((rows) => {
    const syms = [...new Set([st.market.instrument, ...rows.map((r) => r.instrument)])];
    if (!syms.includes(symbol)) symbol = st.market.instrument;
    symSel.replaceChildren(...syms.map((x) => h('option', { value: x, selected: x === symbol }, x)));
  }).catch(() => symSel.replaceChildren(h('option', null, symbol)));

  function load() {
    loadInto(chartSlot, (sg) => api.get(`/api/bars?instrument=${encodeURIComponent(symbol)}&timeframe=${st.market.timeframe}&limit=${range}`, sg), (d) => {
      chartObj?.destroy(); chartObj = null;
      renderSide(d);
      if (!d.available) return emptyState(d.error);
      const box = h('div', { class: 'chart tall' });
      queueMicrotask(() => { chartObj = priceChart(box, d, show); });
      return box;
    });
  }

  function renderSide(d) {
    const cur = ctx.state;
    const q = cur.broker.quote;
    const live = symbol === cur.market.instrument;
    const parts = [];
    if (d.available) {
      const sg = d.strategy;
      const last = d.candles[d.candles.length - 1];
      const side = { 1: 'long', '-1': 'short', 0: 'flat' }[sg.target];
      parts.push(panel(live ? 'Why the agent watches this' : 'Not traded', live ? h('div', { class: 'stack' },
        h('div', null, `${symbol} is the configured instrument (market.instrument). The live strategy is `, h('span', { class: 'mono' }, sg.describe), '.'),
        kv([['Rule says now', badge(side, side === 'long' ? 'pos' : side === 'short' ? 'neg' : '')],
          ['Stop distance', sg.stop_dist == null ? null : fmt.num(sg.stop_dist)],
          ['Implied stop', sg.stop_dist == null || sg.target === 0 ? null : fmt.num(last.close - sg.target * sg.stop_dist)],
          ['Regime', d.regime]]),
        h('div', { class: 'small muted' }, sg.note),
        h('div', { class: 'note warn small' }, 'No strategy has passed the promotion gate on real data. These are observations, not trade advice.'))
        : h('div', { class: 'muted' }, `${symbol} has stored bars but the agent only trades ${cur.market.instrument}. Signals shown are what the live rule would say on this series.`)));
    }
    parts.push(panel('Data freshness', kv([
      ['Source', sourceBadge(d.source)], ['Bars', d.rows], ['Last bar', d.available ? fmt.date(new Date(d.candles[d.candles.length - 1].time * 1000).toISOString()) : null],
      ['Status', live ? (cur.data.stale ? badge('stale', 'warn') : badge('fresh', 'pos')) : badge('reference')],
    ]), { tools: h('button', { class: 'btn sm', onclick: () => ctx.act('ingest').then(load) }, icon('refresh'), 'Refresh') }));
    if (live) {
      parts.push(panel('Live quote', q ? kv([['Bid / Ask', `${fmt.num(q.bid)} / ${fmt.num(q.ask)}`], ['Spread', `${fmt.num(q.spread)} (${q.spread_pips} pips/cents)`], ['Quote time', when(q.time)],
        ['Feed', cur.broker.broker === 'alpaca' ? 'Alpaca IEX (single exchange, best of 3 snapshots)' : cur.broker.broker]])
        : h('div', { class: 'muted small' }, cur.broker.quote_error || cur.broker.error || 'no quote'), { meta: fmt.ago(cur.broker.fetched_at) }));
    }
    side.replaceChildren(...parts);
  }

  load();
  return { el, destroy() { chartObj?.destroy(); } };
}

// ============================================================ Execution
export function execution(ctx) {
  const el = h('div', { class: 'stack', style: { gap: '12px' } });
  const audSlot = h('div');
  const detail = h('div');
  function render(st) {
    const b = st.broker;
    const env = envLabel(st);
    const enabled = st.mode.execution_enabled;
    el.replaceChildren(
      h('div', { class: `note ${b.available ? '' : 'neg'}` }, h('b', null, `${env.text} `),
        b.available ? `Account ${b.account} · ${st.mode.label}. Live trading is not supported by this build.` : `Broker unavailable: ${b.error}`),
      panel('Execution policy', h('div', { class: 'stack' },
        h('div', { class: 'ladder' },
          h('div', { class: `step ${!enabled ? 'on' : ''}` }, h('b', null, 'Observation only'), 'Decisions are recorded; no orders. execution.enabled: false'),
          h('div', { class: 'step on' }, h('b', null, 'Research & analysis'), 'Backtests and walk-forward research. Always available.'),
          h('div', { class: `step ${enabled ? 'on' : ''}` }, h('b', null, 'Paper trading'), 'Paper/practice orders for promoted strategies, after risk approval.'),
          h('div', { class: 'step off' }, h('b', null, 'Human approval'), 'Not implemented: cycles either dry-run or submit.'),
          h('div', { class: 'step off' }, h('b', null, 'Live trading'), 'Refused by code and config validation.')),
        h('div', { class: 'small muted' }, 'The policy is set in config/local.yaml (execution.enabled) and needs a restart. The UI can\'t change it, by design.'))),
      h('div', { class: 'grid' },
        h('div', { class: 'c6' }, panel('Open positions', flush(table([
          { label: 'Instrument', key: 'instrument' }, { label: 'Units', num: true, render: (p) => fmt.signed(p.units, 0) },
          { label: 'Avg price', num: true, render: (p) => fmt.num(p.avg_price) }, { label: 'Stop', num: true, render: (p) => (p.stop_price ? fmt.num(p.stop_price) : h('span', { class: 'dim', title: 'not reported by broker' }, '—')) },
        ], b.positions || [], { empty: b.available ? 'No open positions' : 'Broker unavailable' })), { meta: b.available ? `${b.broker} · ${fmt.ago(b.fetched_at)}` : '' })),
        h('div', { class: 'c6' }, panel('Open broker orders', flush(b.open_orders ? table([
          { label: 'Symbol', key: 'symbol' }, { label: 'Side', key: 'side' }, { label: 'Type', key: 'type' }, { label: 'Qty', key: 'qty', num: true },
          { label: 'Stop', key: 'stop_price', num: true }, { label: 'Status', render: (o) => badge(o.status) }, { label: 'Submitted', render: (o) => when(o.submitted_at) },
        ], b.open_orders, { empty: 'No open orders' }) : emptyState('Not available', `The ${b.broker} broker interface has no order list.`))))),
      panel('Kill switch', h('div', { class: 'row' },
        st.kill_switch.engaged ? [badge('engaged', 'neg'), h('span', null, `${st.kill_switch.reason || ''} · `, when(st.kill_switch.at))] : [badge('clear', 'pos'), h('span', { class: 'muted' }, 'New orders are allowed only if every other check passes.')],
        h('span', { style: { flex: 1 } }),
        h('button', { class: 'btn danger', disabled: st.kill_switch.engaged, onclick: () => ctx.act('kill') }, icon('bolt'), 'Engage kill switch')),
      { foot: 'Engaging blocks all new orders in the backend. It does NOT close positions or cancel resting broker orders. Closing positions and cancelling orders aren\'t available from this UI: use the broker\'s dashboard. Clearing the switch needs `tradeintel risk reset` in a terminal.' }),
      h('div', { class: 'grid' },
        h('div', { class: 'c7' }, panel('Order audit', flush(audSlot), { meta: 'every intent the pipeline evaluated, approved or not' })),
        h('div', { class: 'c5' }, panel('Selected attempt', detail))));
    detail.replaceChildren(emptyState('Select an order attempt'));
    loadInto(audSlot, (sg) => api.get('/api/audit?limit=200', sg), (rows) => table([
      { label: 'Time', render: (a) => when(a.at) }, { label: 'Mode', key: 'mode' },
      { label: 'Instrument', render: (a) => a.intent?.instrument || '—' }, { label: 'Side', render: (a) => (a.intent ? (a.intent.direction === 1 ? 'long' : 'short') : '—') },
      { label: 'Units', num: true, render: (a) => (a.intent ? fmt.num(a.intent.units, 0) : '—') },
      { label: 'Risk', render: (a) => (a.risk ? (a.risk.approved ? badge('approved', 'pos') : badge('rejected', 'neg')) : badge('n/a')) },
      { label: 'Result', render: (a) => badge(a.order_result?.status || '—', a.order_result?.status === 'filled' ? 'pos' : '') },
    ], rows, { empty: 'No order intents yet. They appear when a decision cycle proposes an entry.', onRow: (a) => detail.replaceChildren(auditDetail(a)), maxHeight: '420px' }));
  }
  render(ctx.state);
  return { el };
}

function auditDetail(a) {
  const i = a.intent || {};
  return h('div', { class: 'stack' },
    kv([['Key', h('span', { class: 'mono small' }, a.idempotency_key)], ['Time', when(a.at)], ['Mode', a.mode], ['Strategy', i.strategy],
      ['Order', i.instrument ? `${i.direction === 1 ? 'BUY' : 'SELL'} ${i.units} ${i.instrument} @ ~${fmt.num(i.entry_price)} market` : null],
      ['Stop', i.stop_price != null ? fmt.num(i.stop_price) : null], ['Risk amount', a.risk?.risk_amount != null ? fmt.num(a.risk.risk_amount) : null],
      ['Result', a.order_result ? `${a.order_result.status}${a.order_result.message ? ': ' + a.order_result.message : ''}` : null],
      ['Fill', a.order_result?.fill_price != null ? fmt.num(a.order_result.fill_price) : null]]),
    a.risk ? h('div', null, h('div', { class: 'small muted' }, 'Risk checks'), table([{ label: 'Check', key: 'k' }, { label: '', render: (r) => (r.v ? badge('pass', 'pos') : badge('fail', 'neg')) }],
      Object.entries(a.risk.checks || {}).map(([k, v]) => ({ k, v })))) : null,
    a.risk?.reasons?.length ? h('div', { class: 'note neg small' }, a.risk.reasons.join('\n')) : null,
    h('div', { class: 'small dim' }, 'Slippage, fees and latency are not recorded per order yet.'));
}

// ============================================================ Agents
export function agents(ctx) {
  const el = h('div', { class: 'stack', style: { gap: '12px' } });
  const graphSlot = h('div');
  const nodeSlot = h('div');
  const listSlot = h('div');
  const traceSlot = h('div');
  const modelSlot = h('div');
  let data = null;
  let selNode = 'coordinator';
  let selDecision = ctx.params.decision ? Number(ctx.params.decision) : null;
  let decisions = [];

  el.append(
    h('div', { class: 'note ai' }, h('b', null, 'How this system actually works. '),
      'Four rule-based agents and a coordinator run once inside each decision cycle (one Python process, no LLM in the loop). ',
      'They are not long-running workers, so there is nothing to start, pause or resume; the controls that exist are below.'),
    h('div', { class: 'row' },
      h('button', { class: 'btn primary', onclick: () => ctx.act('run-once').then(reload) }, icon('bolt'), 'Run decision cycle'),
      h('button', { class: 'btn danger', onclick: () => ctx.act('kill') }, 'Engage kill switch'),
      h('span', { class: 'muted small' }, 'A cycle runs: data → strategy signal → agents → coordinator → risk engine → execution pipeline (dry run unless enabled).')),
    h('div', { class: 'grid' },
      h('div', { class: 'c7' }, panel('Workflow', graphSlot, { meta: 'click a node' })),
      h('div', { class: 'c5' }, panel('Component', nodeSlot))),
    h('div', { class: 'grid' },
      h('div', { class: 'c5' }, panel('Decisions', flush(listSlot), { meta: 'decision log' })),
      h('div', { class: 'c7' }, panel('Trace inspector', traceSlot, { meta: 'recorded data only' }))),
    panel('Models & knowledge', modelSlot));

  function reload() {
    loadInto(graphSlot, (sg) => api.get('/api/agents', sg), (d) => {
      data = d;
      renderNode();
      modelSlot.replaceChildren(h('div', { class: 'grid' },
        h('div', { class: 'c6' }, h('div', { class: 'small muted' }, 'Direction model versions (models/forecast.py)'),
          table([{ label: 'Version', key: 'version' }, { label: 'Verdict', render: (m) => badge(m.verdict || '—', m.verdict === 'accepted' ? 'pos' : 'neg') },
            { label: 'Data', render: (m) => sourceBadge(m.data_source) }, { label: 'Created', render: (m) => when(m.created_at) }], d.models, { empty: 'No model versions saved. `tradeintel model-eval` creates one.' })),
        h('div', { class: 'c6' }, h('div', { class: 'small muted' }, 'Knowledge base (SQLite FTS5)'), pre(d.knowledge_base),
          h('div', { class: 'small dim' }, `Not recorded anywhere: ${d.not_recorded.join(', ')}.`))));
      return graph(d);
    });
    loadInto(listSlot, (sg) => api.get('/api/decisions?limit=100', sg), (rows) => {
      decisions = rows;
      if (selDecision == null && rows.length) selDecision = rows[0].id;
      renderTrace();
      return decisionTable();
    });
  }

  function decisionTable() {
    return table([
      { label: 'Time', render: (d) => when(d.decided_at) }, { label: 'Action', render: (d) => actionBadge(d.action) },
      { label: 'Strategy', render: (d) => `${d.strategy} · ${d.instrument}` },
    ], decisions, { empty: 'No decisions yet. Click "Run decision cycle".', maxHeight: '480px', selected: (d) => d.id === selDecision,
      onRow: (d) => { selDecision = d.id; listSlot.replaceChildren(decisionTable()); renderTrace(); } });
  }

  function graph(d) {
    const pos = { market: [10, 20], news: [10, 100], quant: [10, 180], adversarial: [215, 140], coordinator: [420, 60], risk: [625, 60], execution: [625, 160] };
    const W = 180; const H = 56;
    const nodes = Object.fromEntries(d.nodes.map((n) => [n.id, n]));
    const svg = s('svg', { class: 'graph', viewBox: '0 0 815 240', role: 'img', 'aria-label': 'Agent workflow graph' },
      s('defs', null, s('marker', { id: 'arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse' }, s('path', { d: 'M0 0L10 5L0 10z', fill: '#3a424c' }))));
    for (const [a, b] of d.edges) {
      const [x1, y1] = pos[a]; const [x2, y2] = pos[b];
      const sx = x1 + W; const sy = y1 + H / 2; const ex = x2; const ey = y2 + H / 2;
      const p = a === 'risk' ? `M${x1 + W / 2} ${y1 + H} L${x2 + W / 2} ${y2}` : `M${sx} ${sy} C${sx + 30} ${sy}, ${ex - 30} ${ey}, ${ex} ${ey}`;
      svg.append(s('path', { d: p }));
    }
    for (const [id, [x, y]] of Object.entries(pos)) {
      const n = nodes[id];
      if (!n) continue;
      const sub = n.diagnostics.length ? '⚠ see diagnostics' : n.stats ? `${n.stats.runs} runs · last ${n.stats.last?.stance}` : n.kind === 'control' ? n.permissions.join(', ') : 'no runs recorded';
      svg.append(s('g', { class: `node ${n.kind} ${id === selNode ? 'sel' : ''} ${n.diagnostics.length ? 'diag' : ''}`, transform: `translate(${x},${y})`, tabindex: 0, role: 'button', 'aria-label': n.name,
        onclick: () => { selNode = id; graphSlot.replaceChildren(graph(d)); renderNode(); },
        onkeydown: (e) => { if (e.key === 'Enter') { selNode = id; graphSlot.replaceChildren(graph(d)); renderNode(); } } },
      s('rect', { width: W, height: H }), s('text', { x: 10, y: 22 }, n.name), s('text', { x: 10, y: 40, class: 'sub' }, sub.slice(0, 30))));
    }
    return svg;
  }

  function renderNode() {
    const n = data?.nodes.find((x) => x.id === selNode);
    if (!n) return nodeSlot.replaceChildren(emptyState('Select a node'));
    const st = n.stats;
    nodeSlot.replaceChildren(h('div', { class: 'stack' },
      h('div', { class: 'row' }, h('b', null, n.name), badge(n.kind, n.kind === 'agent' ? 'ai' : 'info'), h('span', { class: 'mono small muted' }, n.fn)),
      h('div', null, n.purpose),
      kv([['Reads', n.reads.join(', ')], ['Permissions', n.permissions.join(', ')], ['Version', h('span', { class: 'mono' }, data.version)],
        ['Runs recorded', st ? st.runs : 0], ['Stances', st ? Object.entries(st.stances).map(([k, v]) => `${k} ${v}`).join(' · ') : null],
        ['Last run', st ? when(st.last_at) : null], ['Avg duration', h('span', { class: 'dim' }, 'not recorded')]]),
      st?.last ? h('div', null, h('div', { class: 'small muted' }, 'Last output (recorded)'), pre(st.last)) : null,
      n.config ? h('div', null, h('div', { class: 'small muted' }, 'Configuration (read-only; config/*.yaml)'), pre(n.config)) : null,
      n.diagnostics.map((x) => h('div', { class: 'note warn small' }, x))));
  }

  function renderTrace() {
    const d = decisions.find((x) => x.id === selDecision);
    if (!d) return traceSlot.replaceChildren(emptyState('Select a decision'));
    const inp = d.inputs || {};
    const au = d.audit || [];
    const last = au[0];
    const step = (n, title, status, body, at) => h('li', null, h('div', { class: `n ${status}` }, n),
      h('div', null, h('h4', null, title, at ? h('span', { class: 'muted small', style: { fontWeight: 400, marginLeft: '8px' } }, when(at)) : null), body));
    const nr = (msg) => h('div', { class: 'small dim' }, msg);
    traceSlot.replaceChildren(h('ol', { class: 'trace' },
      step(1, 'Objective', 'ok', h('div', null, `Decide the ${d.strategy} position on ${d.instrument} for the bar closing ${fmt.date(inp.last_bar)}`, h('span', { class: 'small dim' }, ' (derived from the record)')), d.decided_at),
      step(2, 'Context & market data', 'ok', pre(inp)),
      step(3, 'Strategy & mode', 'ok', h('div', null, `${d.strategy}, coordinator mode "${d.mode}"`)),
      step(4, 'Tools invoked', 'no', nr('Not recorded: agents are pure functions with no tool calls.')),
      step(5, 'Agent outputs & evidence', 'ok', h('div', { class: 'stack' }, (d.reports || []).map((r) => h('div', null,
        h('div', { class: 'row' }, h('b', null, r.agent), badge(r.stance, STANCE_TONE[r.stance]), badge(r.confidence)),
        [...(r.evidence || []).map((x) => `evidence: ${x}`), ...(r.missing || []).map((x) => `missing: ${x}`), ...(r.objections || []).map((x) => `objection: ${x}`)]
          .map((x) => h('div', { class: 'small muted' }, x)))))),
      step(6, 'Decision & rationale', 'ok', h('div', null, actionBadge(d.action), ' ', (d.reasons || []).join('; '), h('div', { class: 'small dim' }, 'Recorded rationale (rule output), not a generated explanation.'))),
      step(7, 'Risk checks', last?.risk ? (last.risk.approved ? 'ok' : 'bad') : 'no', last?.risk ? h('div', null, Object.entries(last.risk.checks || {}).map(([k, v]) => badge(k, v ? 'pos' : 'neg')), last.risk.reasons?.length ? h('div', { class: 'small neg' }, last.risk.reasons.join('; ')) : null)
        : nr(d.action.startsWith('enter_') ? 'No audit record found for this bar.' : `Not reached: "${d.action}" creates no order intent.`), last?.at),
      step(8, 'Proposed action', last?.intent ? 'ok' : 'no', last?.intent ? pre(last.intent) : nr('No order intent.')),
      step(9, 'Approval', last?.risk ? (last.risk.approved ? 'ok' : 'bad') : 'no', last ? h('div', null, last.risk?.approved ? 'Risk engine approved' : 'Risk engine rejected', '. Human approval step: not implemented.') : nr('Not reached.')),
      step(10, 'Execution result', last?.order_result ? (last.order_result.status === 'filled' ? 'ok' : 'no') : 'no', last?.order_result ? pre(last.order_result) : nr('No order sent.')),
      step(11, 'Post-trade monitoring', 'no', nr('Not implemented yet (no fill reconciliation).')),
    ), ...(au.length > 1 ? [h('div', { class: 'small muted' }, `${au.length} audit records share this bar (repeat cycles are blocked as duplicates).`)] : []));
  }

  reload();
  return { el };
}

// ============================================================ Risk
export function risk(ctx) {
  const slot = h('div');
  const el = h('div', null, slot);
  const load = (quiet) => loadInto(slot, (sg) => api.get('/api/risk', sg), (r) => h('div', { class: 'stack', style: { gap: '12px' } },
    h('div', { class: 'grid' },
      h('div', { class: 'c5' }, panel('Kill switch', h('div', { class: 'stack' },
        h('div', { class: 'row' }, r.kill_switch.engaged ? badge('engaged', 'neg') : badge('clear', 'pos'),
          r.kill_switch.engaged ? h('span', null, r.kill_switch.reason, ' · ', when(r.kill_switch.at)) : h('span', { class: 'muted' }, 'Engages automatically on a daily-loss or drawdown breach.')),
        h('div', { class: 'row' }, h('button', { class: 'btn danger', disabled: r.kill_switch.engaged, onclick: () => ctx.act('kill').then(() => load(true)) }, icon('bolt'), 'Engage kill switch')),
        h('div', { class: 'small muted' }, 'Blocks new orders only. Positions and resting orders are untouched. To clear it, review the cause, then run `tradeintel risk reset` in a terminal and type RESET. The UI can\'t clear it.')))),
      h('div', { class: 'c7' }, panel('Utilisation', h('div', { class: 'stack' }, r.utilisation.map(utilRow)), { meta: 'live where the broker reports it' }))),
    h('div', { class: 'grid' },
      h('div', { class: 'c5' }, panel('Configured limits', flush(table([{ label: 'Limit', key: 'k' }, { label: 'Value', render: (x) => h('span', { class: 'mono' }, String(x.v)) }],
        Object.entries(r.limits).map(([k, v]) => ({ k, v })))), { foot: r.editing })),
      h('div', { class: 'c7' }, h('div', { class: 'stack', style: { gap: '12px' } },
        panel('Event blackout', flush(table([{ label: 'Time', render: (e) => when(e.event_time) }, { label: 'Ccy', key: 'currency' }, { label: 'Event', key: 'title' }],
          r.blackout_events, { empty: 'No high-impact events stored for the next 2 days.' })), { meta: `±${r.limits.event_blackout_minutes} min · ${r.blackout_currencies.join(', ')}` }),
        panel('Recent risk rejections', flush(table([{ label: 'Time', render: (a) => when(a.at) }, { label: 'Key', render: (a) => h('span', { class: 'mono small' }, a.idempotency_key) },
          { label: 'Reasons', render: (a) => (a.risk.reasons || []).join('; ') }], r.rejections, { empty: 'No rejected order intents.' }))),
        h('div', { class: 'small dim' }, 'Audit trail: rejections and emergency stops are in the order audit. Risk-setting changes are config-file edits and are tracked by git, not by this app.'))))), { quiet });
  load();
  return { el };
}

// ============================================================ Research
export function research(ctx) {
  const el = h('div', { class: 'stack', style: { gap: '12px' } });
  const stratSlot = h('div');
  const expSlot = h('div');
  const detSlot = h('div');
  const runSlot = h('div');
  let data = null;
  let sel = ctx.params.exp ? Number(ctx.params.exp) : null;
  let runChart = null;
  const strategySel = h('select', { class: 'input', 'aria-label': 'Strategy' });

  el.append(
    h('div', { class: 'note warn' }, h('b', null, 'Historical simulations, not profits. '),
      'Walk-forward results are out-of-sample backtests; in-sample backtests are for inspection only. No paper or live trading results exist yet.'),
    h('div', { class: 'grid' },
      h('div', { class: 'c12' }, panel('Strategies', flush(stratSlot)))),
    h('div', { class: 'grid' },
      h('div', { class: 'c7' }, panel('Experiments', flush(expSlot), { meta: 'experiment registry (every run, including failures)' })),
      h('div', { class: 'c5' }, panel('Experiment detail', detSlot))),
    panel('Run', h('div', { class: 'stack' },
      h('div', { class: 'row' }, strategySel,
        h('button', { class: 'btn', onclick: () => run('backtest') }, 'Backtest (in-sample)'),
        h('button', { class: 'btn primary', onclick: () => run('research') }, 'Walk-forward research'),
        h('span', { class: 'small muted' }, `On the stored ${ctx.state.market.instrument} bars with the configured costs.`)),
      runSlot)),
    panel('Paper trading vs backtest', emptyState('No paper or demo trades yet', 'This comparison appears once paper orders have been filled and reconciled.')));

  function load() {
    loadInto(expSlot, (sg) => api.get('/api/research', sg), (d) => {
      data = d;
      strategySel.replaceChildren(...d.strategies.filter((x) => !x.baseline).map((x) => h('option', { value: x.name, selected: x.is_live }, x.name)));
      stratSlot.replaceChildren(table([
        { label: 'Strategy', render: (x) => h('span', null, h('b', null, x.name), x.is_live ? ' ' : '', x.is_live ? badge('live', 'ai') : null, x.baseline ? ' ' : '', x.baseline ? badge('baseline') : null) },
        { label: 'Family', key: 'family' }, { label: 'Defaults', render: (x) => h('span', { class: 'mono small' }, Object.entries(x.defaults).map(([k, v]) => `${k}=${v}`).join(' ')) },
        { label: 'Grid', key: 'grid_size', num: true, d: 0 }, { label: 'Trials', key: 'total_trials', num: true, d: 0 },
        { label: 'Verdicts (latest)', render: (x) => x.verdicts.map((v) => badge(`${v.instrument} ${v.verdict}`, v.verdict === 'promotable' ? 'pos' : 'neg')) },
        { label: 'Promoted', render: (x) => (x.promoted ? badge('yes', 'pos') : h('span', { class: 'dim' }, 'no')) },
      ], d.strategies));
      if (sel == null && d.experiments.length) sel = d.experiments[0].id;
      renderDetail();
      return expTable();
    });
  }

  function expTable() {
    const o = (e) => e.metrics.oos || e.metrics;
    return table([
      { label: '#', key: 'id', num: true, d: 0 }, { label: 'Date', render: (e) => fmt.date(e.created_at) },
      { label: 'Kind', key: 'kind' }, { label: 'Strategy', key: 'strategy' }, { label: 'Inst', key: 'instrument' },
      { label: 'Data', render: (e) => sourceBadge(e.data_source) }, { label: 'Trials', key: 'n_trials', num: true, d: 0 },
      { label: 'Sharpe', num: true, render: (e) => fmt.num(o(e).sharpe) }, { label: 'Return', num: true, render: (e) => h('span', { class: fmt.tone(o(e).net_return) }, fmt.pct(o(e).net_return, 1)) },
      { label: 'Trades', num: true, render: (e) => fmt.num(o(e).n_trades, 0) },
      { label: 'Verdict', render: (e) => (e.verdict ? badge(e.verdict, e.verdict === 'promotable' || e.verdict === 'accepted' ? 'pos' : 'neg') : '—') },
    ], data.experiments, { empty: 'No experiments yet. Run research below.', maxHeight: '420px', selected: (e) => e.id === sel,
      onRow: (e) => { sel = e.id; expSlot.replaceChildren(expTable()); renderDetail(); } });
  }

  function renderDetail() {
    const e = data?.experiments.find((x) => x.id === sel);
    if (!e) return detSlot.replaceChildren(emptyState('Select an experiment'));
    const m = e.metrics;
    const o = m.oos || m;
    const warns = [];
    if (e.data_source === 'SYNTHETIC') warns.push('SYNTHETIC data: says nothing about real markets.');
    if (o.n_trades != null && o.n_trades < data.promotion.min_trades) warns.push(`Only ${o.n_trades} trades: too few for reliable statistics (gate needs ${data.promotion.min_trades}).`);
    const ci = o.sharpe_ci90;
    if (Array.isArray(ci) && ci[0] <= 0) warns.push(`Sharpe 90% CI ${fmt.num(ci[0])} … ${fmt.num(ci[1])} includes zero.`);
    if (e.n_trials > 100) warns.push(`${e.n_trials} trials run: the best result is inflated by selection (deflated Sharpe isn't implemented yet).`);
    const bl = m.baselines || {};
    detSlot.replaceChildren(h('div', { class: 'stack' },
      h('div', { class: 'row' }, h('b', null, `#${e.id} ${e.strategy}`), sourceBadge(e.data_source), e.verdict ? badge(e.verdict, e.verdict === 'promotable' ? 'pos' : 'neg') : null,
        e.report ? h('a', { class: 'btn sm', href: e.report, target: '_blank', rel: 'noopener' }, icon('ext'), 'Full report') : null),
      warns.map((w) => h('div', { class: 'note warn small' }, w)),
      kv([['Research period', m.research_period ? `${fmt.date(m.research_period[0])} → ${fmt.date(m.research_period[1])}` : null],
        ['Holdout', m.holdout_period ? `${fmt.date(m.holdout_period[0])} → ${fmt.date(m.holdout_period[1])} (untouched)` : null],
        ['Folds', m.n_folds], ['OOS Sharpe 90% CI', Array.isArray(ci) ? `${fmt.num(ci[0])} … ${fmt.num(ci[1])}` : null],
        ['Code', h('span', { class: 'mono small' }, e.code_version)]]),
      m.oos ? table([{ label: 'Run', key: 'run' }, { label: 'Return', num: true, render: (r) => fmt.pct(r.net_return, 1) }, { label: 'Sharpe', num: true, render: (r) => fmt.num(r.sharpe) },
        { label: 'Max DD', num: true, render: (r) => fmt.pct(r.max_drawdown, 1) }, { label: 'Trades', num: true, render: (r) => fmt.num(r.n_trades, 0) }, { label: 'PF', num: true, render: (r) => fmt.num(r.profit_factor) }],
      [{ run: 'strategy (OOS)', ...m.oos }, m.cost_stress ? { run: `${m.cost_stress.multiplier}x costs`, ...m.cost_stress } : null,
        bl.buy_and_hold ? { run: 'buy & hold', ...bl.buy_and_hold } : null, bl.no_trade ? { run: 'no trade', ...bl.no_trade } : null].filter(Boolean)) : pre(m),
      m.gate ? h('div', null, h('div', { class: 'small muted' }, 'Promotion gate'), (m.gate.reasons || []).length ? m.gate.reasons.map((x) => h('div', { class: 'small neg' }, '✕ ', x)) : h('div', { class: 'pos small' }, 'All checks passed')) : null));
  }

  async function run(kind) {
    const name = strategySel.value;
    runChart?.destroy(); runChart = null;
    runSlot.replaceChildren(h('div', { class: 'row muted' }, h('span', { class: 'typing' }, h('span'), h('span'), h('span')), kind === 'research' ? 'Running walk-forward research…' : 'Running backtest…'));
    const res = await ctx.act(kind, { strategy: name });
    if (!res) return runSlot.replaceChildren();
    if (!res.ok) return runSlot.replaceChildren(errorState(res.error));
    if (kind === 'research') { sel = res.experiment_id; load(); return runSlot.replaceChildren(h('div', { class: 'note' }, `Experiment #${res.experiment_id} recorded: ${res.gate.passed ? 'PASSED' : 'rejected'}. See the detail panel.`)); }
    const box = h('div', { class: 'chart short' });
    const mm = res.metrics;
    runSlot.replaceChildren(h('div', { class: 'note warn small' }, res.warning),
      h('div', { class: 'strip' }, stat('Return', fmt.pct(mm.net_return, 1), '', fmt.tone(mm.net_return)), stat('Sharpe', fmt.num(mm.sharpe)), stat('Max DD', fmt.pct(mm.max_drawdown, 1)),
        stat('Trades', fmt.num(mm.n_trades, 0)), stat('Win rate', fmt.pct(mm.win_rate, 0)), stat('Profit factor', fmt.num(mm.profit_factor)), stat('Costs', fmt.num(mm.total_costs, 0), 'spread+slippage+financing')),
      h('div', { class: 'row small muted' }, h('span', { style: { color: C.ai } }, '━ ', res.strategy), h('span', { style: { color: C.text } }, '━ buy & hold (benchmark)')), box);
    queueMicrotask(() => { runChart = lineChart(box, [{ data: res.benchmark, color: C.text, width: 1 }, { data: res.equity, color: C.ai }]); });
  }

  load();
  return { el, destroy() { runChart?.destroy(); } };
}

// ============================================================ News & events
export function news(ctx) {
  const slot = h('div');
  const load = () => loadInto(slot, (sg) => api.get('/api/news', sg), (n) => h('div', { class: 'grid' },
    h('div', { class: 'c7' }, panel('Economic calendar', flush(table([
      { label: 'Time', render: (e) => when(e.event_time) }, { label: 'Ccy', key: 'currency' },
      { label: 'Impact', render: (e) => badge(e.impact, e.impact === 'high' ? 'neg' : e.impact === 'medium' ? 'warn' : '') },
      { label: 'Event', key: 'title' }, { label: 'Fcst', key: 'forecast' }, { label: 'Prev', key: 'previous' }, { label: 'Actual', key: 'actual' },
    ], n.events, { empty: 'No events stored for the last 2 / next 7 days. Click Refresh news.', maxHeight: '520px' })), { meta: 'Forex Factory weekly export · times in your local zone' })),
    h('div', { class: 'c5' }, h('div', { class: 'stack', style: { gap: '12px' } },
      panel('Feed health', flush(table([{ label: 'Source', key: 'source' }, { label: 'Status', render: (x) => (x.ok ? badge('ok', 'pos') : badge('failed', 'neg')) },
        { label: 'Items', key: 'items', num: true, d: 0 }, { label: 'Checked', render: (x) => when(x.at, 'ago') }, { label: 'Error', render: (x) => h('span', { class: 'small muted' }, x.error || '') }],
      n.health, { empty: 'No fetches recorded yet.' }))),
      panel('Headlines', flush(table([{ label: 'Ingested', render: (x) => when(x.ingested_at, 'ago') },
        { label: 'Source', render: (x) => badge(x.source, x.reliability === 'official' ? 'info' : '') },
        { label: 'Title', render: (x) => (safeUrl(x.url) ? h('a', { href: safeUrl(x.url), target: '_blank', rel: 'noopener noreferrer' }, x.title) : x.title) }],
      n.news, { empty: 'No headlines stored yet.', maxHeight: '360px' })), { meta: 'untrusted external text' })))));
  const el = h('div', { class: 'stack', style: { gap: '12px' } },
    h('div', { class: 'row' }, h('button', { class: 'btn', onclick: () => ctx.act('news-ingest').then(load) }, icon('refresh'), 'Refresh news'),
      h('span', { class: 'small muted' }, 'Central-bank RSS feeds and the Forex Factory calendar. The calendar export covers the current week only.')), slot);
  load();
  return { el };
}

// ============================================================ Activity & logs
export function activity(ctx) {
  const slot = h('div');
  const q = h('input', { class: 'input', placeholder: 'Filter…', value: ctx.params.q || '', 'aria-label': 'Filter activity', oninput: () => render() });
  const typeSel = h('select', { class: 'input', 'aria-label': 'Type', onchange: () => render() }, ['all', 'decision', 'order', 'log'].map((t) => h('option', { value: t }, t)));
  const levelSel = h('select', { class: 'input', 'aria-label': 'Log level', onchange: () => render() }, ['any level', 'ERROR', 'WARNING', 'INFO'].map((t) => h('option', { value: t }, t)));
  let rows = [];
  let open = null;
  const el = h('div', { class: 'stack', style: { gap: '12px' } }, h('div', { class: 'row' }, q, typeSel, levelSel,
    h('button', { class: 'btn sm', onclick: () => load() }, icon('refresh'), 'Reload')), panel('Timeline', flush(slot), { meta: 'decision log · order audit · application log (newest first)' }));

  function load() {
    loadInto(slot, (sg) => Promise.all([api.get('/api/decisions?limit=200', sg), api.get('/api/audit?limit=300', sg), api.get('/api/logs?limit=400', sg)]), ([ds, au, lg]) => {
      rows = [
        ...ds.map((d) => ({ at: d.decided_at, type: 'decision', level: 'INFO', text: `${d.action} · ${d.strategy} · ${d.instrument}: ${(d.reasons || []).join('; ')}`, raw: d })),
        ...au.map((a) => ({ at: a.at, type: 'order', level: a.risk && !a.risk.approved ? 'WARNING' : 'INFO', text: `${a.order_result?.status || '—'} · ${a.idempotency_key}${a.risk && !a.risk.approved ? ' · ' + (a.risk.reasons || []).join('; ') : ''}`, raw: a })),
        ...lg.lines.map((l) => ({ at: l.ts, type: 'log', level: l.level, text: `${l.logger || ''} ${l.msg}`, raw: l })),
      ].sort((a, b) => ((a.at || '') < (b.at || '') ? 1 : -1));
      return renderTable(lg);
    });
  }
  function renderTable(lg) {
    const t = q.value.trim().toLowerCase();
    const ty = typeSel.value; const lv = levelSel.value;
    const shown = rows.filter((r) => (ty === 'all' || r.type === ty) && (lv === 'any level' || r.level === lv) && (!t || r.text.toLowerCase().includes(t))).slice(0, 400);
    return h('div', null, table([
      { label: 'Time', render: (r) => when(r.at) }, { label: 'Type', render: (r) => badge(r.type, r.type === 'decision' ? 'ai' : r.type === 'order' ? 'info' : '') },
      { label: 'Level', render: (r) => badge(r.level, r.level === 'ERROR' ? 'neg' : r.level === 'WARNING' ? 'warn' : '') },
      { label: 'Event', render: (r) => h('div', null, r.text, open === r ? pre(r.raw) : null) },
    ], shown, { empty: 'Nothing matches.', onRow: (r) => { open = open === r ? null : r; render(); } }),
    lg && !lg.available ? h('div', { class: 'panel-f' }, `No log file yet at ${lg.path}`) : null);
  }
  function render() { slot.replaceChildren(renderTable()); }
  load();
  return { el };
}

// ============================================================ Settings
export function settings(ctx) {
  const slot = h('div');
  loadInto(slot, (sg) => api.get('/api/config', sg), (c) => {
    const st = c.settings;
    return h('div', { class: 'grid' },
      h('div', { class: 'c6' }, h('div', { class: 'stack', style: { gap: '12px' } },
        panel('Market & data', kv([['Instrument', st.market.instrument], ['Asset class', st.market.asset_class], ['Timeframe', st.market.timeframe],
          ['Pip size', st.market.pip_size], ['Data provider', st.data.provider], ['History from', st.data.start]])),
        panel('Execution', kv([['Broker', st.execution.broker], ['Order submission', st.execution.enabled ? badge('enabled (paper)', 'warn') : badge('disabled', 'pos')],
          ['Live trading', badge('not supported', 'pos')], ['Live strategy', `${st.live.strategy} ${JSON.stringify(st.live.params)}`], ['Requires promotion', String(st.live.require_promotion)]])),
        panel('Secrets (presence only)', flush(table([{ label: 'Variable', key: 'k' }, { label: 'Status', render: (x) => (x.v ? badge('set', 'pos') : badge('missing')) }],
          Object.entries(c.env).map(([k, v]) => ({ k, v })))), { foot: 'Values are never sent to the browser. Keys live in trader_ai/.env.' }),
        panel('Stored series', flush(table([{ label: 'Instrument', key: 'instrument' }, { label: 'TF', key: 'timeframe' }, { label: 'Source', render: (x) => sourceBadge(x.source) },
          { label: 'Rows', key: 'rows', num: true, d: 0 }, { label: 'Last', render: (x) => fmt.date(x.last) }], c.series))))),
      h('div', { class: 'c6' }, h('div', { class: 'stack', style: { gap: '12px' } },
        panel('Companion', h('div', { class: 'stack' },
          h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !store.get('cmp.hidden', false), onchange: (e) => ctx.companion.setHidden(!e.target.checked) }), 'Show the companion orb'),
          h('div', { class: 'row' }, h('button', { class: 'btn sm', onclick: () => ctx.companion.resetPosition() }, 'Reset position')),
          kv([['LLM for free-text questions', st.llm.provider === 'none' ? 'none (rule-based answers only)' : st.llm.provider], ['Voice', h('span', { class: 'dim' }, 'not implemented')],
            ['Desktop floating', h('span', { class: 'dim' }, 'in-app only: this is a web app and can\'t float over other Windows apps')]]))),
        panel('Keyboard', kv([['Command palette / search', h('span', null, h('kbd', null, 'Ctrl'), ' ', h('kbd', null, 'K'))], ['Companion', h('span', null, h('kbd', null, 'Ctrl'), ' ', h('kbd', null, '.'))],
          ['Close dialogs', h('kbd', null, 'Esc')], ['Toggle sidebar', h('span', null, h('kbd', null, 'Ctrl'), ' ', h('kbd', null, 'B'))]])),
        panel('Risk limits', pre(st.risk), { foot: 'Read-only. Edit config/local.yaml and restart; nothing at runtime writes risk configuration.' }),
        panel('Not available in this build', h('ul', { style: { margin: 0, paddingLeft: '18px' } },
          ['Enabling execution or live trading from the UI', 'Editing risk limits at runtime', 'Clearing the kill switch (CLI only)', 'Cancelling orders / closing positions',
            'Human-approval order queue', 'Agent start/pause (agents run inside decision cycles)', 'Custom dashboard layouts', 'Voice input/output'].map((x) => h('li', { class: 'muted' }, x)))),
        panel('About', h('div', { class: 'small muted' }, 'Charts by ', h('a', { href: 'https://www.tradingview.com/', target: '_blank', rel: 'noopener' }, 'TradingView Lightweight Charts™'),
          ' (Apache-2.0, see vendor/lightweight-charts.LICENSE.txt).')))));
  });
  return { el: slot };
}
