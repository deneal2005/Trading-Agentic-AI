// Screens. Each page is (ctx) => { el, destroy? }. Every number shown comes from
// the backend; anything unavailable is labelled as such.
import { h, s, api, fmt, when, panel, flush, badge, table, kv, stat, meter, loadInto, loading, emptyState, errorState, pre, icon, store, animateNumber } from './lib.js';
import { killControl, checkControl, killReason, deriveStatus, statusBadge } from './controls.js';
import { stateBadge, jobOutcome, stepList, stepLine } from './activity.js';

// The single most useful next action, from the real state. Order = most urgent first.
function nextStep(st, d) {
  if (st.kill_switch.engaged) return 'The emergency stop is engaged. If you didn’t mean to engage it, press “Release stop…” in the status bar.';
  if (!st.broker.available) return 'The broker can’t be reached. Check your internet connection and the keys in trader_ai/.env.';
  if (st.data.stale) return 'Price data is out of date. Press “Refresh price data” on the Chart page.';
  if (st.alerts.some((a) => a.key.startsWith('calendar:'))) return 'This week’s economic calendar is missing. Press “Refresh news & calendar” on the News & calendar page.';
  if (!d) return 'Press “Run check” in the status bar to see what the AI makes of the latest prices.';
  if (!st.activity?.service?.alive) return 'Nothing urgent. Checks only run when you press “Run check”; to run them automatically, start `tradeintel service` in a terminal.';
  return 'Nothing to do. The scheduler runs the next check automatically.';
}


// Currencies whose announcements can move the traded market: both legs of an FX pair, else the account currency.
function relevantCcys(st) {
  const i = st.market.instrument;
  return st.market.asset_class === 'forex' && /^[A-Z]{6}$/.test(i) ? [i.slice(0, 3), i.slice(3)] : [st.broker.currency || 'USD'];
}

const ACTION_WORDS = { abstain: 'No trade', hold: 'No change', exit: 'Close position', enter_long: 'Buy', enter_short: 'Sell short' };
function decisionText(d) {
  const r = (d.reasons || []).join('; ');
  const why = r.startsWith('objections to new entry: ')
    ? `held back because ${r.slice(25).split('; ').map(plainObjection).join(', and ')}`
    : r.replace(/^signal unchanged$/, 'the signal hasn’t changed').replace(/^signal flat$/, 'the signal turned flat');
  return `${ACTION_WORDS[d.action] || d.action} on ${d.instrument}: ${why}`;
}

// The emergency stop control is shared (controls.js) so every screen shows the same confirmed state.
const killButton = () => killControl();

export { brain } from './brain.js';

const LWC = window.LightweightCharts;
// a chart may be destroyed by a page switch and again by a later reload; removing it twice throws
const once = (fn) => { let done = false; return () => { if (!done) { done = true; fn(); } }; };
const tok = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
// chart colours follow the current theme; re-read whenever a chart is built
const C = {};
const readTheme = () => Object.assign(C, { bg: tok('--chart-bg'), grid: tok('--chart-grid'), text: tok('--chart-text'), border: tok('--border'), pos: tok('--pos'), neg: tok('--neg'), ai: tok('--ai'), info: tok('--info'), warn: tok('--warn'), sma50: tok('--info'), sma200: tok('--ai') });
readTheme();
const ACTION_TONE = { enter_long: 'pos', enter_short: 'neg', exit: 'warn', hold: '', abstain: 'ai' };
const STANCE_TONE = { long: 'pos', short: 'neg', neutral: '', abstain: 'ai' };
const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : null);

function chartBase(el, opts = {}) {
  if (!LWC) { el.replaceChildren(emptyState('Chart library failed to load')); return null; }
  readTheme();
  const chart = LWC.createChart(el, {
    autoSize: true,
    layout: { background: { type: 'solid', color: C.bg }, textColor: C.text, fontFamily: getComputedStyle(document.body).fontFamily, fontSize: 11 },
    grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
    rightPriceScale: { borderColor: C.border },
    timeScale: { borderColor: C.border, rightOffset: 4 },
    crosshair: { mode: 0 },
    ...opts,
  });
  // autoSize keeps bar spacing on resize, which leaves the chart half empty; refit when the width changes
  let w = 0;
  new ResizeObserver(([e]) => { const nw = Math.round(e.contentRect.width); if (w && nw && nw !== w) { try { chart.timeScale().fitContent(); } catch { /* chart already removed */ } } w = nw || w; }).observe(el);
  return chart;
}

// Candles + overlays + markers from /api/bars. Returns { chart, destroy }.
function priceChart(el, d, show = {}) {
  const chart = chartBase(el);
  if (!chart) return { destroy() {} };
  const bars = d.candles.filter((c) => c.open != null && c.close != null);
  const area = show.kind === 'area';
  const up = bars.length < 2 || bars[bars.length - 1].close >= bars[0].close;
  const line = up ? C.pos : C.neg;
  const candles = area
    ? chart.addAreaSeries({ lineColor: line, lineWidth: 2, topColor: `${line}38`, bottomColor: `${line}00`, priceLineVisible: false })
    : chart.addCandlestickSeries({ upColor: C.pos, downColor: C.neg, borderVisible: false, wickUpColor: C.pos, wickDownColor: C.neg, priceLineVisible: false });
  candles.setData(area ? bars.map((c) => ({ time: c.time, value: c.close })) : bars);
  const vols = d.candles.filter((c) => c.volume != null);
  if (show.volume !== false && vols.length) {
    const v = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: '', lastValueVisible: false, priceLineVisible: false });
    v.priceScale().applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
    v.setData(vols.map((c) => ({ time: c.time, value: c.volume, color: `${c.close >= c.open ? C.pos : C.neg}47` })));
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
    legend.textContent = c.open == null ? `${d.instrument} ${d.timeframe}  ${fmt.num(c.value ?? c.close)}` : `${d.instrument} ${d.timeframe}  O ${fmt.num(c.open)}  H ${fmt.num(c.high)}  L ${fmt.num(c.low)}  C ${fmt.num(c.close)}`;
  };
  setLegend(last);
  chart.subscribeCrosshairMove((p) => setLegend(p?.seriesData?.get(candles) || last));
  chart.timeScale().fitContent();
  return { chart, destroy: once(() => chart.remove()) };
}

function lineChart(el, series) {
  const chart = chartBase(el);
  if (!chart) return { destroy() {} };
  for (const sr of series) chart.addLineSeries({ color: sr.color, lineWidth: sr.width || 1.5, priceLineVisible: false, title: sr.title }).setData(sr.data.filter((p) => p.value != null));
  chart.timeScale().fitContent();
  return { chart, destroy: once(() => chart.remove()) };
}

function sourceBadge(src) {
  if (!src) return badge('no data');
  return src === 'SYNTHETIC' ? badge('SYNTHETIC', 'warn') : badge(src, 'info');
}

function actionBadge(a) { return badge((ACTION_WORDS[a] || a.replace('_', ' ')).toLowerCase(), ACTION_TONE[a] || ''); }

function envLabel(st) {
  const b = st.broker;
  if (!b.available) return { text: 'DISCONNECTED', tone: 'bad' };
  return { text: b.environment === 'paper' ? 'INTERNAL PAPER' : `PAPER · ${b.broker.toUpperCase()}`, tone: 'env' };
}

// ============================================================ Today (overview)
// Answers three questions in order: what is the AI doing, is the account safe, what needs me.
const REGIME_WORDS = {
  flat_lowvol: 'quiet and sideways', flat_highvol: 'choppy and sideways', up_lowvol: 'calmly rising', up_highvol: 'rising but volatile',
  down_lowvol: 'calmly falling', down_highvol: 'falling and volatile',
};
const STRAT_NAMES = { sma_cross: 'moving-average trend', donchian_breakout: 'breakout', rsi_mean_reversion: 'RSI bounce',
  sma_trend_filter: '200-day trend', tsmom: '12-month momentum', turn_of_month: 'turn-of-month' };

function plainObjection(o) {
  let m = o.match(/trend strategy in (\w+) regime/);
  if (m) return `the market is ${REGIME_WORDS[m[1]] || m[1]}, which has historically been a bad time for trend rules`;
  m = o.match(/mean-reversion strategy in trending regime/);
  if (m) return 'the market is trending, which is a bad time for a bounce rule';
  m = o.match(/high-impact event within [^:]+: (.+) \((\w+)\)/);
  if (m) return `a major announcement (${m[1]}, ${m[2]}) is due within 24 hours`;
  m = o.match(/^(\w+) disagrees/);
  if (m) return `the ${m[1]} agent disagrees`;
  return o;
}

function story(st, d) {
  const sym = st.market.instrument;
  const rule = STRAT_NAMES[st.strategy.name] || st.strategy.name;
  if (st.kill_switch.engaged) return { h: ['Trading is halted. ', h('span', { class: 'ai' }, 'The emergency stop is engaged.')], why: `Reason: ${killReason(st.kill_switch.reason)}${st.kill_switch.at ? ` (${fmt.time(st.kill_switch.at)})` : ''}. New orders are refused until you release it (Emergency stop, in the status bar).` };
  if (!d) return { h: [`Watching ${sym}. `, h('span', { class: 'ai' }, 'No check has run yet.')], why: 'Run a check to see what the strategy and the AI agents make of the latest prices.' };
  const market = (d.reports || []).find((r) => r.agent === 'market');
  const side = market?.stance === 'long' ? 'buy' : market?.stance === 'short' ? 'sell' : 'stay out';
  const adv = (d.reports || []).find((r) => r.agent === 'adversarial');
  if (d.action === 'abstain') {
    if (market?.stance === 'abstain') return { h: [`Watching ${sym}. `, h('span', { class: 'ai' }, 'No trade: the data is out of date.')], why: (market.missing || []).join('; ') };
    const objs = (adv?.objections || []).map(plainObjection);
    return { h: [`Watching ${sym}. `, h('span', { class: 'ai' }, 'No trade: the AI held back.')],
      why: `The ${rule} rule says ${side}, but ${objs.join(', and ') || 'another agent objected'}. When the agents don't agree, the AI does nothing.` };
  }
  if (d.action === 'hold') return { h: [`Holding steady on ${sym}. `, h('span', { class: 'ai' }, 'Nothing changed.')], why: `The ${rule} rule still says ${side}, so there is nothing to do.` };
  if (d.action === 'exit') return { h: [`The AI wants to close the ${sym} position.`], why: `The ${rule} rule turned flat.` };
  const verb = d.action === 'enter_long' ? 'buy' : 'sell short';
  return { h: [`The AI wants to ${verb} ${sym}. `, st.mode.execution_enabled ? '' : h('span', { class: 'ai' }, 'Orders are off, so nothing was sent.')],
    why: `The ${rule} rule says ${side} and no agent objected.` };
}

function rail(st, d) {
  const station = (cls, n, nm, ds) => h('li', { class: `station ${cls}` }, h('div', { class: 'dotc' }, n), h('div', { class: 'nm' }, nm), h('div', { class: 'ds' }, ds));
  const data = st.data;
  const s1 = data.stale ? station('stopped', '!', 'Price data', `Out of date (last bar ${fmt.date(data.last_bar)})`) : station('passed', '✓', 'Price data', `Latest daily bar ${fmt.date(data.last_bar)}`);
  if (!d) return h('ol', { class: 'rail', 'aria-label': 'Where the latest check stopped' }, s1, station('', '2', 'Strategy signal', 'Not checked yet'), station('', '3', 'AI review', 'Not checked yet'),
    station('', '4', 'Safety check', 'Not checked yet'), station('', '5', 'Order', st.mode.execution_enabled ? 'Paper orders on' : 'Orders are off'));
  const market = (d.reports || []).find((r) => r.agent === 'market');
  const sideWord = { long: 'Says buy', short: 'Says sell', neutral: 'Says stay out', abstain: 'No reading' }[market?.stance] || '—';
  const s2 = market?.stance === 'abstain' ? station('stopped', '!', 'Strategy signal', 'No reading: data missing') : station('passed', '✓', 'Strategy signal', `${sideWord} (${STRAT_NAMES[d.strategy] || d.strategy})`);
  const adv = (d.reports || []).find((r) => r.agent === 'adversarial');
  const nObj = (adv?.objections || []).length;
  const objected = d.action === 'abstain' && market?.stance !== 'abstain';
  const s3 = market?.stance === 'abstain' ? station('', '3', 'AI review', 'Skipped')
    : objected ? station('stopped', '✕', 'AI review', `Held back: ${nObj || 1} objection${nObj === 1 ? '' : 's'}`)
      : station('passed', '✓', 'AI review', d.action === 'hold' ? 'Nothing new to do' : 'No objections');
  const a = (d.audit || [])[0];
  const s4 = !a ? station('', '4', 'Safety check', d.action.startsWith('enter_') ? 'No record found' : 'Not needed: no order')
    : a.risk?.approved ? station('passed', '✓', 'Safety check', 'All limits passed') : station('blocked', '✕', 'Safety check', `Refused: ${(a.risk?.reasons || [])[0] || 'limit breached'}`);
  const res = a?.order_result;
  const s5 = !st.mode.execution_enabled ? station('', '5', 'Order', 'Orders are off (watching only)')
    : res?.status === 'filled' ? station('passed', '✓', 'Order', `Filled at ${fmt.num(res.fill_price)}`)
      : res ? station('stopped', '!', 'Order', res.status) : station('', '5', 'Order', 'Nothing sent');
  return h('ol', { class: 'rail', 'aria-label': 'Where the latest check stopped' }, s1, s2, s3, s4, s5);
}

// Live mid price for a position, only when the broker's quote is for that instrument.
function markOf(st, p) {
  const q = st.broker.quote;
  return q && q.instrument === p.instrument ? (q.bid + q.ask) / 2 : null;
}
// Position value in the account currency (mirrors api._position_notional); null when it can't be converted.
function notional(p, px, ccy) {
  const i = p.instrument;
  const u = Math.abs(p.units);
  if (/^[A-Z]{6}$/.test(i)) return i.slice(3) === ccy ? u * px : i.slice(0, 3) === ccy ? u : null;
  return u * px;
}
function accountMetrics(st) {
  const b = st.broker;
  const acc = st.account_state || {};
  const day = acc.day_start_equity && acc.day === new Date().toISOString().slice(0, 10) ? b.equity - acc.day_start_equity : null;
  let unreal = 0; let expo = 0; let marked = true;
  for (const p of b.positions || []) {
    const m = markOf(st, p);
    marked = marked && m != null;
    unreal = unreal == null || m == null ? null : unreal + (m - p.avg_price) * p.units;
    const n = notional(p, m ?? p.avg_price, b.currency);
    expo = expo == null || n == null ? null : expo + n;
  }
  return { day, dayPct: day == null ? null : day / acc.day_start_equity, unreal, expo, expoPct: expo == null || !b.equity ? null : expo / b.equity, marked };
}

function positionsTable(st) {
  const b = st.broker;
  if (!b.available) return emptyState('Holdings unknown', `The broker can’t be reached, so what the account holds can’t be shown. ${b.error || ''}`);
  const pos = b.positions || [];
  if (!pos.length) return emptyState('No open positions', 'The account is flat. Positions appear here only when the broker reports them; a running scheduler or a “buy” decision alone doesn’t open one.');
  const pnl = (p) => { const m = markOf(st, p); return m == null ? null : (m - p.avg_price) * p.units; };
  return table([
    { label: 'Instrument', render: (p) => h('b', null, p.instrument) },
    { label: 'Side', render: (p) => badge(p.units > 0 ? 'Long' : 'Short', p.units > 0 ? 'pos' : 'neg') },
    { label: 'Units', num: true, render: (p) => fmt.num(Math.abs(p.units), 0) },
    { label: 'Avg price', num: true, render: (p) => fmt.num(p.avg_price) },
    { label: 'Mark', num: true, render: (p) => { const m = markOf(st, p); return m == null ? h('span', { class: 'dim', title: 'No live price for this instrument' }, '—') : fmt.num(m); } },
    { label: 'Unrealised P&L', num: true, render: (p) => { const v = pnl(p); return v == null ? h('span', { class: 'dim', title: 'Needs a live price' }, '—') : h('span', { class: fmt.tone(v) }, fmt.signed(v)); } },
  ], pos);
}

function kpiTile(label, hint, cls = '') {
  const v = h('div', { class: 'kpi-v na' }, '—');
  const sub = h('div', { class: 'kpi-s' });
  return { el: h('div', { class: `kpi ${cls}`, title: hint }, h('div', { class: 'kpi-k' }, label), v, sub), v, sub };
}
// `chip` is a small signed change before the sub line, like the reference's "+5% this month"
function setKpi(t, value, format, sub, toned = false, chip = null) {
  animateNumber(t.v, value, format);
  t.v.classList.toggle('pos', toned && value > 0);
  t.v.classList.toggle('neg', toned && value < 0);
  t.sub.replaceChildren(chip ? h('span', { class: `chip ${chip.tone}` }, chip.text) : '', sub);
  t.sub.title = sub;
}

// segmented control; `opts` = [[label, value]]
function segCtl(label, opts, get, set) {
  const el = h('div', { class: 'seg', role: 'group', 'aria-label': label });
  const draw = () => el.replaceChildren(...opts.map(([l, v]) => h('button', { class: v === get() ? 'on' : '', 'aria-pressed': String(v === get()), onclick: () => { set(v); draw(); } }, l)));
  draw();
  return el;
}
const RANGES = [['1M', 21], ['3M', 63], ['6M', 126], ['1Y', 252], ['All', 0]];
const SRC_WORDS = { app: 'from this app', service: 'by the scheduler', cli: 'from the terminal' };

export function overview(ctx) {
  const sym = ctx.state.market.instrument;
  const T = {
    equity: kpiTile('Equity', 'Total account value reported by the broker', 'hero'),
    cash: kpiTile('Cash', 'Cash balance reported by the broker'),
    day: kpiTile('Today’s P&L', 'Equity now minus equity at today’s first check (UTC day)'),
    unreal: kpiTile('Unrealised P&L', 'Open positions marked at the live mid price'),
    expo: kpiTile('Exposure', 'Value of open positions as a share of equity'),
    orders: kpiTile('Open orders', 'Orders waiting at the broker'),
  };
  const decBody = h('div');
  const decLink = h('a', { class: 'btn sm ghost', hidden: true }, 'How it decided →');
  const chartSlot = h('div', { class: 'chart' });
  const chgEl = h('span', { class: 'chg-pill' });
  const posSlot = h('div');
  const safety = h('div');
  const attention = h('div');
  const recent = h('div');
  let chartObj = null;
  let lastDecision;  // undefined = still loading, null = none recorded
  let liveCheck = ctx.state.activity?.running.find((j) => j.kind === 'check') || null;
  let events = [];
  let range = store.get('ov.range', 126);
  let kind = store.get('ov.kind', 'area');
  const decPanel = panel('Latest AI decision', decBody, { cls: 'decision', tools: decLink });

  const el = h('div', { class: 'stack', style: { gap: '14px' } },
    h('div', { class: 'kpis', style: { marginBottom: 0 } }, Object.values(T).map((t) => t.el)),
    h('div', { class: 'grid' },
      h('div', { class: 'c8' }, panel(`${sym} price`, flush(chartSlot), {
        meta: chgEl,
        tools: [segCtl('Chart type', [['Line', 'area'], ['Candles', 'candles']], () => kind, (v) => { kind = v; store.set('ov.kind', v); loadChart(); }),
          segCtl('Range', RANGES, () => range, (v) => { range = v; store.set('ov.range', v); loadChart(); })],
      })),
      h('div', { class: 'c4' }, decPanel)),
    h('div', { class: 'grid' },
      h('div', { class: 'c8' }, panel('Open positions', flush(posSlot), { meta: 'as reported by the broker', tools: h('a', { class: 'btn sm ghost', href: '#/execution' }, 'Orders →') })),
      h('div', { class: 'c4' }, panel('Safety limits', safety, { tools: h('a', { class: 'btn sm ghost', href: '#/risk' }, 'Details →') }))),
    h('div', { class: 'grid' },
      h('div', { class: 'c6' }, panel('Needs your attention', attention)),
      h('div', { class: 'c6' }, panel('Recent activity', recent, { tools: h('a', { class: 'btn sm ghost', href: '#/activity' }, 'History →') }))));

  function renderKpis(st) {
    const b = st.broker;
    if (!b.available) {
      for (const t of Object.values(T)) setKpi(t, null, String, 'broker offline');
      return;
    }
    const m = accountMetrics(st);
    const npos = (b.positions || []).length;
    setKpi(T.equity, b.equity, (v) => fmt.money(v), `${b.currency} · account ${b.account}`);
    setKpi(T.cash, b.cash, (v) => fmt.money(v), b.currency);
    setKpi(T.day, m.day, (v) => fmt.signed(v), m.day == null ? 'starts after today’s first check' : 'since the day started', true,
      m.day == null ? null : { text: fmt.pct(m.dayPct, 2, true), tone: fmt.tone(m.dayPct) });
    setKpi(T.unreal, npos ? m.unreal : 0, (v) => fmt.signed(v), !npos ? 'no open position' : m.unreal == null ? 'needs a live price' : 'at live mid price', true);
    setKpi(T.expo, npos ? m.expoPct : 0, (v) => fmt.pct(v, 1), !npos ? 'nothing invested' : m.expo == null ? 'can’t convert to account currency' : `${fmt.money(m.expo)} ${b.currency}${m.marked ? '' : ' at cost'}`);
    setKpi(T.orders, b.open_orders ? b.open_orders.length : null, (v) => fmt.num(v, 0), b.open_orders ? 'waiting at the broker' : 'not reported by this broker');
  }

  function renderDecision(st) {
    const d = lastDecision;
    if (liveCheck) {  // a check is running right now (from any tab, the terminal or the scheduler)
      decBody.replaceChildren(
        h('div', { class: 'eyebrow' }, `started ${SRC_WORDS[liveCheck.source] || ''} · ${Math.round(liveCheck.elapsed_s)} s so far`),
        h('h3', null, `Checking ${st.market.instrument}… `, h('span', { class: 'ai' }, `${liveCheck.step}.`)),
        h('p', { class: 'why' }, liveCheck.slow ? `Taking longer than usual (normally about ${liveCheck.expected_s} s). It is still running.` : 'The result replaces this as soon as the check finishes.'),
        stepList(liveCheck));
      decLink.hidden = true;
      return;
    }
    if (d === undefined) { decBody.replaceChildren(loading(5)); return; }
    const s = story(st, d);
    decBody.replaceChildren(
      h('div', { class: 'eyebrow' }, `${st.market.instrument} · ${st.market.timeframe}`, d ? [' · checked ', when(d.decided_at, 'ago')] : null),
      h('h3', null, s.h), h('p', { class: 'why' }, s.why), rail(st, d),
      h('div', { class: 'next' }, h('b', null, 'Next: '), nextStep(st, d)));
    decLink.hidden = !d;
    if (d) decLink.href = `#/agents?decision=${d.id}`;
  }

  function renderAttention(st) {
    const now = Date.now();
    const ccys = relevantCcys(st);
    const upcoming = (events || []).filter((e) => e.impact === 'high' && ccys.includes(e.currency) && new Date(e.event_time) > now).slice(0, 3);
    const items = st.alerts.filter((a) => a.level !== 'info').map((a) => h('div', { class: `alert-item ${a.level}`, style: { padding: '10px 0' } }, h('div', { class: 'bar' }),
      h('div', null, h('div', { style: { fontWeight: 500 } }, a.title), a.detail ? h('div', { class: 'small muted' }, a.detail) : null)));
    for (const e of upcoming) items.push(h('div', { class: 'alert-item', style: { padding: '10px 0' } }, h('div', { class: 'bar' }),
      h('div', null, h('div', { style: { fontWeight: 500 } }, `${e.title} (${e.currency})`), h('div', { class: 'small muted' }, 'High-impact announcement · ', when(e.event_time)))));
    attention.replaceChildren(items.length ? h('div', null, items) : emptyState('Nothing needs your attention', 'Warnings and big economic announcements show up here.'));
  }

  // Recent work from the job table (every check, refresh and test, including failures).
  function renderJobs(jobs) {
    if (!jobs?.length) return false;
    recent.replaceChildren(h('div', { class: 'stack', style: { gap: '10px' } }, jobs.slice(0, 6).map((j) => h('div', { class: 'jrow' },
      stateBadge(j), h('div', { style: { minWidth: 0 } }, h('div', { class: 'jlabel' }, j.label), h('div', { class: 'small muted' }, j.state === 'running' ? stepLine(j) : jobOutcome(j))),
      h('span', { class: 'small dim nowrap' }, fmt.ago(j.started_at))))));
    return true;
  }
  function renderRecent(ds, au) {
    if (renderJobs(ctx.state.activity?.recent)) return;
    const rows = [
      ...ds.map((d) => ({ at: d.decided_at, text: [actionBadge(d.action), ' ', `${STRAT_NAMES[d.strategy] || d.strategy} check on ${d.instrument}`], link: `#/agents?decision=${d.id}` })),
      ...au.map((a) => ({ at: a.at, text: [badge(a.risk?.approved ? 'order approved' : 'order refused', a.risk?.approved ? 'pos' : 'warn'), ' ', a.intent?.instrument || ''], link: '#/execution' })),
    ].sort((x, y) => (x.at < y.at ? 1 : -1)).slice(0, 6);
    recent.replaceChildren(rows.length ? h('div', { class: 'stack', style: { gap: '8px' } }, rows.map((r) => h('a', { href: r.link, class: 'row', style: { color: 'inherit', justifyContent: 'space-between', flexWrap: 'nowrap' } },
      h('span', null, r.text), h('span', { class: 'small dim nowrap' }, fmt.ago(r.at)))))
      : emptyState('Nothing yet', 'Each check and order attempt is listed here.'));
  }

  function live(st) { renderKpis(st); renderDecision(st); posSlot.replaceChildren(positionsTable(st)); renderAttention(st); }

  function refresh(st) {
    live(st);
    Promise.all([api.get('/api/decisions?limit=6'), api.get('/api/audit?limit=6'), api.get('/api/risk'), api.get('/api/news')]).then(([ds, au, r, n]) => {
      const fresh = ds[0] && store.get('ov.seen') !== ds[0].id;
      lastDecision = ds[0] || null;
      if (fresh) {  // a check you haven't seen yet: draw the eye to it once
        store.set('ov.seen', lastDecision.id);
        decPanel.classList.remove('fresh');
        void decPanel.offsetWidth;
        decPanel.classList.add('fresh');
      }
      renderDecision(ctx.state);
      renderRecent(ds, au);
      safety.replaceChildren(riskSummary(r));
      events = n.events;
      renderAttention(ctx.state);
    }).catch((e) => { if (lastDecision === undefined) { lastDecision = null; renderDecision(ctx.state); } safety.replaceChildren(errorState(e)); });
  }

  function loadChart() {
    const label = RANGES.find(([, n]) => n === range)?.[0] || '';
    loadInto(chartSlot, (sg) => api.get(`/api/bars?limit=${range}`, sg), (d) => {
      chartObj?.destroy(); chartObj = null;
      if (!d.available) { chgEl.textContent = ''; return emptyState('No price data', d.error); }
      const cs = d.candles.filter((c) => c.close != null);
      const ch = cs.length > 1 ? cs[cs.length - 1].close / cs[0].close - 1 : null;
      chgEl.className = `chg-pill ${fmt.tone(ch)}`;
      chgEl.textContent = ch == null ? '' : `${fmt.pct(ch, 1, true)} · ${label}`;
      const box = h('div', { class: 'chart' });
      queueMicrotask(() => { chartObj = priceChart(box, d, { kind, volume: kind === 'candles', sma: false, decisions: false }); });
      return box;
    }, { placeholder: () => h('div', { class: 'ph', style: { padding: '12px', height: '360px' } }, h('div', { class: 'skeleton block' })) });
  }

  refresh(ctx.state);
  loadChart();
  let n = 0;
  return {
    el,
    // state polls every 10 s; the heavier panels refresh every minute
    onState(st) { live(st); if (++n % 6 === 0) refresh(st); },
    onActivity(act) {
      const was = liveCheck;
      liveCheck = act?.running.find((j) => j.kind === 'check') || null;
      if (was && !liveCheck) refresh(ctx.state);  // it just finished: load the new decision
      else renderDecision(ctx.state);
      renderJobs(act?.recent);
    },
    destroy() { chartObj?.destroy(); },
  };
}

function riskSummary(r) {
  const pick = ['Loss today', 'Drop from the account’s peak', 'Buy/sell price gap now'];
  return h('div', { class: 'stack', style: { gap: '14px' } },
    h('div', { class: 'row', style: { justifyContent: 'space-between' } }, h('span', null, 'Emergency stop'),
      r.kill_switch.engaged ? badge('on: orders blocked', 'neg') : badge('off', 'pos')),
    r.utilisation.filter((u) => pick.includes(u.limit)).map(utilRow));
}

function utilRow(u) {
  const shown = u.value == null ? '—' : u.unit === 'pct' ? fmt.pct(u.value) : u.unit === 'x' ? `${fmt.num(u.value)}×` : fmt.num(u.value, u.unit === 'count' ? 0 : 2);
  const max = u.unit === 'pct' ? fmt.pct(u.max) : u.unit === 'x' ? `${u.max}×` : u.max;
  return h('div', { title: u.note || '' },
    h('div', { class: 'row', style: { justifyContent: 'space-between', flexWrap: 'nowrap' } }, h('span', null, u.limit), h('span', { class: 'mono small muted' }, `${shown} of ${max}`)),
    u.value == null ? h('div', { class: 'small dim' }, u.note || 'Not available yet') : meter(u.value / u.max));
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
    h('div', { class: 'grid' }, h('div', { class: 'c9' }, panel('Daily price', flush(chartSlot), { meta: 'one candle per trading day · arrows = strategy signal changes · squares = AI checks' })),
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
      parts.push(panel(live ? `Why the AI watches ${symbol}` : 'Not traded', live ? h('div', { class: 'stack' },
        h('div', null, `${symbol} is the one market this AI trades. It follows the `, h('b', null, sg.describe.split('(')[0]), ' rule.'),
        kv([['Rule says now', badge(side, side === 'long' ? 'pos' : side === 'short' ? 'neg' : '')],
          ['Stop distance', sg.stop_dist == null ? null : fmt.num(sg.stop_dist)],
          ['Implied stop', sg.stop_dist == null || sg.target === 0 ? null : fmt.num(last.close - sg.target * sg.stop_dist)],
          ['Regime', d.regime]]),
        h('div', { class: 'small muted' }, sg.note),
        h('div', { class: 'note warn small' }, 'No strategy has passed every test on real data yet, so this is what the rule says, not advice.'))
        : h('div', { class: 'muted' }, `${symbol} has stored bars but the agent only trades ${cur.market.instrument}. Signals shown are what the live rule would say on this series.`)));
    }
    parts.push(panel('Price data', kv([
      ['Source', sourceBadge(d.source)], ['Bars', d.rows], ['Last bar', d.available ? fmt.date(new Date(d.candles[d.candles.length - 1].time * 1000).toISOString()) : null],
      ['Status', live ? (cur.data.stale ? badge('stale', 'warn') : badge('fresh', 'pos')) : badge('reference')],
    ]), { tools: h('button', { class: 'btn sm', dataset: { job: 'ingest' }, onclick: (e) => ctx.act('ingest', {}, { button: e.currentTarget }).then(load) }, icon('refresh'), 'Refresh price data') }));
    if (live) {
      parts.push(panel('Live price', q ? kv([['Sell / buy price', `${fmt.num(q.bid)} / ${fmt.num(q.ask)}`], ['Gap', `${fmt.num(q.spread)}`], ['As of', when(q.time)],
        ['Source', cur.broker.broker === 'alpaca' ? 'Alpaca (IEX exchange, best of 3 snapshots)' : cur.broker.broker]])
        : h('div', { class: 'muted small' }, cur.broker.quote_error || cur.broker.error || 'no quote'), { meta: fmt.ago(cur.broker.fetched_at) }));
    }
    side.replaceChildren(...parts);
  }

  load();
  return { el, destroy() { chartObj?.destroy(); } };
}

// ============================================================ Execution
// What happened to an order, in one word plus a sentence. Derived only from the recorded audit row.
const ORDER_STATUS = {
  pending: ['Waiting at broker', 'info', 'Sent to the broker; waiting for it to fill. “Check status with broker” asks again.'],
  partially_filled: ['Partly filled', 'info', 'Some of it filled; the rest is still open at the broker.'],
  filled: ['Filled', 'pos', 'The broker filled it.'],
  refused: ['Refused by safety check', 'warn', 'The safety check stopped it before anything was sent.'],
  not_sent: ['Not sent', '', 'Nothing went to the broker: orders are switched off, or the broker couldn’t be reached first.'],
  not_received: ['Never reached broker', '', 'The request failed and the broker has no record of it, so nothing was placed. The next check may send it again.'],
  rejected: ['Rejected by broker', 'neg', 'The broker refused it.'],
  error: ['Failed: status unknown', 'neg', 'The request failed (e.g. network) and may still have reached the broker. It isn’t sent again until “Check status with broker” (or the next check) confirms it never arrived.'],
  cancelled: ['Cancelled', '', 'The broker cancelled or expired it.'],
  closed: ['Position closed', '', 'An exit closed the position.'],
};
function orderStatus(a) {
  const s = a.order_result?.status;
  const key = a.risk && !a.risk.approved ? 'refused' : ORDER_STATUS[s] ? s : 'error';
  const [label, tone, hint] = ORDER_STATUS[key];
  return { key, label, tone, hint };
}
const isOrder = (a) => a.mode !== 'manual' && a.mode !== 'emergency';
function orderBadge(a) {
  const st = orderStatus(a);
  return h('span', { class: `badge ${st.tone}` }, st.key === 'pending' || st.key === 'partially_filled' ? h('span', { class: 'spin sm' }) : null, st.label);
}
function orderLine(a) {
  const i = a.intent;
  if (!i) return a.decision?.action === 'exit' ? 'Exit: close the position' : a.idempotency_key;
  return `${i.direction === 1 ? 'Buy' : 'Sell'} ${fmt.num(i.units, 0)} ${i.instrument} at about ${fmt.num(i.entry_price)}, stop ${i.stop_price != null ? fmt.num(i.stop_price) : 'none'}`;
}
function orderWhy(a) {
  if (a.risk && !a.risk.approved) return (a.risk.reasons || []).join('; ');
  const r = a.order_result || {};
  return r.message || (r.fill_price != null ? `filled at ${fmt.num(r.fill_price)}` : '');
}

export function execution(ctx) {
  const el = h('div', { class: 'stack', style: { gap: '16px' } });
  const liveSlot = h('div');
  const summary = h('div');
  const listSlot = h('div');
  const detail = h('div');
  let rows = [];
  let sel = null;

  function renderList() {
    const orders = rows.filter(isOrder);
    const counts = {};
    for (const a of orders) { const k = orderStatus(a).key; counts[k] = (counts[k] || 0) + 1; }
    const order = ['pending', 'partially_filled', 'filled', 'refused', 'not_sent', 'not_received', 'rejected', 'error', 'cancelled', 'closed'];
    summary.replaceChildren(h('div', { class: 'ostrip' }, order.filter((k) => counts[k] || ['pending', 'filled', 'refused', 'error'].includes(k)).map((k) => {
      const [label, tone] = ORDER_STATUS[k];
      return h('div', { class: `ostat ${counts[k] ? tone : 'zero'}` }, h('div', { class: 'n' }, String(counts[k] || 0)), h('div', { class: 'l' }, label));
    })));
    if (!orders.length) {
      const enabled = ctx.state.mode.execution_enabled;
      listSlot.replaceChildren(h('div', { class: 'state' }, h('div', { class: 't' }, 'No orders yet'),
        h('div', { class: 'small', style: { maxWidth: '560px' } }, 'An order appears here when a check decides to buy or sell. So far every check has decided not to trade (see “AI decisions”).',
          enabled ? '' : ' Orders are also switched off, so even a buy signal would only be recorded here as “Not sent”.'),
        h('div', { class: 'row', style: { justifyContent: 'center', marginTop: '6px' } },
          checkControl(),
          h('a', { class: 'btn ghost', href: '#/agents' }, 'See past checks'))));
      detail.replaceChildren(emptyState('Nothing to show', 'Order details appear here.'));
      return;
    }
    listSlot.replaceChildren(table([
      { label: 'Status', render: orderBadge },
      { label: 'When', render: (a) => when(a.at) },
      { label: 'Order', render: (a) => orderLine(a) },
      { label: 'What happened', render: (a) => h('span', { class: 'small muted' }, orderWhy(a) || orderStatus(a).hint) },
    ], orders, { onRow: (a) => { sel = a.id; renderList(); }, selected: (a) => a.id === sel, maxHeight: '460px' }));
    const a = orders.find((x) => x.id === sel) || orders[0];
    sel = a.id;
    detail.replaceChildren(orderDetail(a));
  }

  function renderLive(act) {
    const run = act?.running.find((j) => j.kind === 'check' || j.kind === 'reconcile');
    liveSlot.replaceChildren(run ? h('div', { class: 'note ai row', style: { gap: '10px' } }, h('span', { class: 'spin' }),
      h('span', null, h('b', null, `${run.label}: `), `${run.steps.length > 1 ? `step ${run.steps.indexOf(run.step) + 1} of ${run.steps.length}, ` : ''}${run.step}. `,
        'Any order it creates will appear below.')) : []);
  }

  function load() {
    return api.get('/api/audit?limit=300').then((r) => { rows = r; renderList(); }, (e) => listSlot.replaceChildren(errorState(e, load)));
  }

  const st = ctx.state;
  const b = st.broker;
  const enabled = st.mode.execution_enabled;
  const posSlot = h('div', null, positionsTable(st));
  const killState = h('div', { class: 'row' });
  const drawKill = () => { const k = deriveStatus().kill; killState.replaceChildren(statusBadge(k), h('span', { class: 'small muted' }, k.sub)); };
  drawKill();
  el.append(
    liveSlot,
    panel('Open positions', flush(posSlot), { meta: `as reported by the broker · ${fmt.ago(b.fetched_at)}` }),
    panel('Orders', h('div', { class: 'stack', style: { gap: '14px' } }, summary, listSlot), {
      meta: enabled ? 'paper orders on' : 'orders off: watching only',
      tools: h('button', { class: 'btn sm', dataset: { job: 'reconcile' }, title: 'Ask the broker what happened to orders still waiting',
        onclick: (e) => ctx.act('reconcile', {}, { button: e.currentTarget }).then(load) }, icon('refresh'), 'Check status with broker'),
    }),
    h('div', { class: 'grid' },
      h('div', { class: 'c7' }, panel('Selected order', detail)),
      h('div', { class: 'c5' }, h('div', { class: 'stack', style: { gap: '16px' } },
        panel('Account', kv([['Account', b.available ? `${b.broker} paper ${b.account}` : 'not connected'], ['Order mode', enabled ? 'Paper orders on (passed strategies only)' : 'Watching only: nothing is sent'],
          ['Waiting at broker', b.open_orders ? String(b.open_orders.length) : 'not available']]),
        { foot: 'Change the order mode in config/local.yaml (execution.enabled) and restart. Real-money trading is not possible in this app.' }),
        panel('Emergency stop', h('div', { class: 'stack' },
          killState,
          h('div', null, killButton()),
          h('div', { class: 'small muted' }, 'Blocks new orders only. It doesn\'t sell what you hold or cancel orders already at the broker.')))))));
  listSlot.replaceChildren(loading(4));
  load();
  renderLive(st.activity);
  return {
    el,
    onState: (cur) => { posSlot.replaceChildren(positionsTable(cur)); drawKill(); },
    onActivity: (act) => { const was = !!liveSlot.childNodes.length; renderLive(act); if (was && !liveSlot.childNodes.length) load(); },
  };
}

// The life of one order, step by step, from what was recorded.
function orderDetail(a) {
  const i = a.intent || {};
  const st = orderStatus(a);
  const res = a.order_result || {};
  const step = (state, title, body) => h('li', { class: state }, h('i', { 'aria-hidden': 'true' }), h('div', null, h('b', null, title), body ? h('div', { class: 'small muted' }, body) : null));
  const refused = st.key === 'refused';
  const sent = ['pending', 'partially_filled', 'filled', 'rejected', 'cancelled', 'error'].includes(st.key);
  const brokerState = { filled: 'done', partially_filled: 'live', pending: 'live', rejected: 'bad', cancelled: 'skip', error: 'bad' }[st.key] || 'skip';
  return h('div', { class: 'stack' },
    h('div', { class: 'row' }, orderBadge(a), h('span', { class: 'small muted' }, st.hint)),
    h('ol', { class: 'jsteps big' },
      step('done', 'The AI decided to trade', `${orderLine(a)} · ${i.strategy || ''} · ${fmt.time(a.at)}`),
      step(refused ? 'bad' : 'done', refused ? 'Safety check refused it' : 'Safety check passed', refused ? (a.risk.reasons || []).join('; ') : a.risk?.risk_amount != null ? `money at risk if the stop is hit: ${fmt.num(a.risk.risk_amount)}` : ''),
      step(refused ? 'skip' : sent ? 'done' : 'skip', sent ? 'Sent to the broker' : 'Not sent', sent ? `order key ${a.idempotency_key}` : refused ? 'stopped by the safety check' : res.message || 'orders are switched off'),
      step(sent ? brokerState : 'skip', { filled: 'Filled', partially_filled: 'Partly filled', pending: 'Waiting for the broker', rejected: 'Rejected by the broker', cancelled: 'Cancelled', error: 'No answer (request failed)' }[st.key] || 'Broker',
        sent ? [res.fill_price != null ? `fill price ${fmt.num(res.fill_price)}` : '', res.filled_units ? ` · ${fmt.num(res.filled_units, 0)} filled` : '', res.message ? ` · ${res.message}` : '',
          res.checked_at ? ` · checked ${fmt.ago(res.checked_at)}` : ''].join('') : '')),
    a.risk ? h('details', null, h('summary', { class: 'small' }, `All ${Object.keys(a.risk.checks || {}).length} safety checks`),
      h('div', { class: 'row', style: { gap: '6px', marginTop: '8px' } }, Object.entries(a.risk.checks || {}).map(([k, v]) => badge(k.replace(/_/g, ' '), v ? 'pos' : 'neg')))) : null);
}

// ============================================================ Agents
export function agents(ctx) {
  const el = h('div', { class: 'stack', style: { gap: '12px' } });
  const graphSlot = h('div');
  const nodeSlot = h('div');
  const listSlot = h('div');
  const traceSlot = h('div');
  const modelSlot = h('div');
  const trackSlot = h('div');
  let data = null;
  let selNode = 'coordinator';
  let selDecision = ctx.params.decision ? Number(ctx.params.decision) : null;
  let decisions = [];

  el.append(
    h('div', { class: 'note ai' }, h('b', null, 'How a check works. '),
      'The strategy rule gives a signal, three agents review it, a coordinator decides, then the safety check runs. ',
      'They are fixed rules that run once per check (not chatbots), so there is nothing to start or pause.'),
    h('div', { class: 'row' },
      checkControl(),
      killButton()),
    h('div', { class: 'grid' },
      h('div', { class: 'c7' }, panel('How a check flows', graphSlot, { meta: 'click a step for details' })),
      h('div', { class: 'c5' }, panel('Step details', nodeSlot))),
    h('div', { class: 'grid' },
      h('div', { class: 'c5' }, panel('Past checks', flush(listSlot), { meta: 'newest first' })),
      h('div', { class: 'c7' }, panel('Step by step', traceSlot, { meta: 'only what was recorded' }))),
    panel('Track record: was the AI right?', trackSlot, { meta: 'each check scored against what the price did next' }),
    panel('AI models and knowledge base', modelSlot));

  // Every recorded check vs the real outcome: the signal's hit rate, and whether holding back paid off.
  function trackRecord(t) {
    if (t.error) return emptyState('No price data to score against', t.error);
    const scored = t.horizons.some((hz) => t.signal[hz]?.n);
    if (!scored) return emptyState('Not enough history yet', `${t.checks} checked day(s) recorded; ${t.pending} still waiting for the next ${Math.max(...t.horizons)} trading days of prices before they can be scored.`);
    const pctOr = (v, n) => (v == null ? h('span', { class: 'dim' }, '—') : h('span', { class: `mono ${fmt.tone(v)}` }, fmt.pct(v, n, true)));
    const rate = (v, n) => (v == null ? h('span', { class: 'dim' }, '—') : h('span', { class: 'mono' }, `${fmt.pct(v, 0)} `, h('span', { class: 'dim' }, `of ${n}`)));
    const small = Math.max(...t.horizons.map((hz) => t.signal[hz].n)) < 30;
    return h('div', { class: 'stack' },
      table([
        { label: 'After', render: (r) => `${r.hz} trading day${r.hz === 1 ? '' : 's'}` },
        { label: 'Signal right', num: true, render: (r) => rate(t.signal[r.hz].hit_rate, t.signal[r.hz].n) },
        { label: 'Avg move with signal', num: true, render: (r) => pctOr(t.signal[r.hz].avg_move, 2) },
        { label: 'Holding back was right', num: true, render: (r) => rate(t.held_back[r.hz].right_rate, t.held_back[r.hz].n) },
        { label: 'Avg loss avoided by holding back', num: true, render: (r) => pctOr(t.held_back[r.hz].avg_avoided, 2) },
      ], t.horizons.map((hz) => ({ hz }))),
      small ? h('div', { class: 'note warn small' }, 'Fewer than 30 scored days: far too few to judge the AI yet. This fills in as checks run.') : null,
      h('div', { class: 'small muted' }, `${t.checks} checked days · ${t.pending} waiting for later prices · one score per day (the latest check). Past calls only, not a forecast.`));
  }

  function reload() {
    loadInto(trackSlot, (sg) => api.get('/api/outcomes', sg), trackRecord);
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
    ], decisions, { empty: 'No checks yet. Press “Run check”.', maxHeight: '480px', selected: (d) => d.id === selDecision,
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

const pct = (v) => `${+(v * 100).toFixed(2)}%`;
const LIMIT_NAMES = {
  max_risk_per_trade: ['Most one trade can lose', pct], max_open_risk: ['Most all open trades can lose together', pct],
  max_daily_loss: ['Daily loss that triggers the emergency stop', pct], max_drawdown: ['Drop from peak that triggers the emergency stop', pct],
  max_leverage: ['Total positions vs account value', (v) => `${v}×`], max_position_units: ['Largest single order (units)', (v) => fmt.num(v, 0)],
  max_positions_per_instrument: ['Positions per market', String], max_correlated_exposure: ['Similar bets at the same time', String],
  max_spread_pips: ['Widest buy/sell gap allowed (cents or pips)', String], max_consecutive_losses: ['Pause after this many losses in a row', String],
  event_blackout_minutes: ['No orders this many minutes around big announcements', String], require_stop_loss: ['Every order needs a stop-loss', (v) => (v ? 'yes' : 'no')],
};

// ============================================================ Risk
export function risk(ctx) {
  const slot = h('div');
  const el = h('div', null, slot);
  const load = (quiet) => loadInto(slot, (sg) => api.get('/api/risk', sg), (r) => h('div', { class: 'stack', style: { gap: '12px' } },
    h('div', { class: 'grid' },
      h('div', { class: 'c5' }, panel('Emergency stop', h('div', { class: 'stack' },
        h('div', { class: 'row' }, r.kill_switch.engaged ? badge('on: orders blocked', 'neg') : badge('off', 'pos'),
          r.kill_switch.engaged ? h('span', null, killReason(r.kill_switch.reason), ' · ', when(r.kill_switch.at)) : h('span', { class: 'muted' }, 'Turns on by itself if the account loses too much in a day or from its peak.')),
        h('div', { class: 'row' }, killButton()),
        h('div', { class: 'small muted' }, 'It only blocks new orders; what you hold stays. To turn it off, check why it went on first; you\'ll be asked to type RESET.')))),
      h('div', { class: 'c7' }, panel('How close the account is to each limit', h('div', { class: 'stack', style: { gap: '14px' } }, r.utilisation.map(utilRow))))),
    h('div', { class: 'grid' },
      h('div', { class: 'c5' }, panel('The limits', flush(table([{ label: 'Rule', render: (x) => LIMIT_NAMES[x.k]?.[0] || x.k }, { label: 'Setting', num: true, render: (x) => h('span', { class: 'mono' }, (LIMIT_NAMES[x.k]?.[1] || String)(x.v)) }],
        Object.entries(r.limits).map(([k, v]) => ({ k, v })))), { foot: 'Fixed while the app runs, on purpose. To change one, edit config/local.yaml and restart.' })),
      h('div', { class: 'c7' }, h('div', { class: 'stack', style: { gap: '12px' } },
        panel('No-trade windows around big announcements', flush(table([{ label: 'Time', render: (e) => when(e.event_time) }, { label: 'Ccy', key: 'currency' }, { label: 'Event', key: 'title' }],
          r.blackout_events, { empty: 'No big announcements stored for the next 2 days.' })), { meta: `${r.limits.event_blackout_minutes} min before and after · ${r.blackout_currencies.join(', ')}` }),
        panel('Orders refused by a safety check', flush(table([{ label: 'Time', render: (a) => when(a.at) }, { label: 'Key', render: (a) => h('span', { class: 'mono small' }, a.idempotency_key) },
          { label: 'Reasons', render: (a) => (a.risk.reasons || []).join('; ') }], r.rejections, { empty: 'None so far.' }))))))), { quiet });
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
    h('div', { class: 'note warn' }, h('b', null, 'These are tests on past prices, not profits. '),
      'A good-looking result can still be luck, so each one must pass the safety gate: enough trades, a reliable edge, beating simply holding the market, and surviving double costs.'),
    h('div', { class: 'grid' },
      h('div', { class: 'c12' }, panel('Strategies', flush(stratSlot)))),
    h('div', { class: 'grid' },
      h('div', { class: 'c7' }, panel('Every test so far', flush(expSlot), { meta: 'including the failures' })),
      h('div', { class: 'c5' }, panel('Test details', detSlot))),
    panel('Test a strategy', h('div', { class: 'stack' },
      h('div', { class: 'row' }, strategySel,
        h('button', { class: 'btn', dataset: { job: 'backtest' }, onclick: (e) => run('backtest', e.currentTarget) }, 'Quick backtest (first look)'),
        h('button', { class: 'btn primary', dataset: { job: 'research' }, onclick: (e) => run('research', e.currentTarget) }, 'Full history test'),
        h('span', { class: 'small muted' }, `Uses all stored ${ctx.state.market.instrument} prices and realistic trading costs.`)),
      runSlot)),
    panel('Paper results vs history tests', emptyState('No paper trades yet', 'Once paper orders have filled, their results are compared with the history tests here.')));

  function load() {
    loadInto(expSlot, (sg) => api.get('/api/research', sg), (d) => {
      data = d;
      strategySel.replaceChildren(...d.strategies.filter((x) => !x.baseline).map((x) => h('option', { value: x.name, selected: x.is_live }, x.name)));
      stratSlot.replaceChildren(table([
        { label: 'Strategy', render: (x) => h('span', null, h('b', null, x.name), x.is_live ? ' ' : '', x.is_live ? badge('live', 'ai') : null, x.baseline ? ' ' : '', x.baseline ? badge('baseline') : null) },
        { label: 'What it does', render: (x) => h('span', { class: 'small muted' }, x.doc.split('\n').join(' ').split(/(?<=\.)\s/)[0]) },
        { label: 'Times tested', key: 'total_trials', num: true, d: 0 },
        { label: 'Latest results', render: (x) => h('span', { class: 'row', style: { gap: '4px' } }, x.verdicts.map((v) => badge(`${v.instrument} ${v.verdict === 'promotable' ? 'passed' : 'failed'}`, v.verdict === 'promotable' ? 'pos' : 'neg'))) },
        { label: 'Allowed to trade', render: (x) => (x.promoted ? badge('yes', 'pos') : h('span', { class: 'dim' }, 'no')) },
      ], d.strategies));
      if (sel == null && d.experiments.length) sel = d.experiments[0].id;
      renderDetail();
      return expTable();
    });
  }

  function expTable() {
    const o = (e) => e.metrics.oos || e.metrics;
    return table([
      { label: '#', key: 'id', num: true, d: 0 }, { label: 'Date', render: (e) => h('span', { class: 'nowrap' }, fmt.date(e.created_at)) },
      { label: 'Type', render: (e) => ({ walkforward: 'history test', model: 'AI model', holdout: 'final test' })[e.kind] || e.kind }, { label: 'Strategy', key: 'strategy' }, { label: 'Market', key: 'instrument' },
      { label: 'Data', render: (e) => sourceBadge(e.data_source) }, { label: 'Trials', key: 'n_trials', num: true, d: 0 },
      { label: 'Sharpe', num: true, render: (e) => fmt.num(o(e).sharpe) }, { label: 'Return', num: true, render: (e) => h('span', { class: fmt.tone(o(e).net_return) }, fmt.pct(o(e).net_return, 1)) },
      { label: 'Trades', num: true, render: (e) => fmt.num(o(e).n_trades, 0) },
      { label: 'Result', render: (e) => (e.verdict ? badge(e.verdict === 'promotable' || e.verdict === 'accepted' ? 'passed' : 'failed', e.verdict === 'promotable' || e.verdict === 'accepted' ? 'pos' : 'neg') : '—') },
    ], data.experiments, { empty: 'No tests yet. Run one below.', maxHeight: '420px', selected: (e) => e.id === sel,
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
    const dsr = m.deflated_sharpe;
    if (dsr && !(dsr.value >= data.promotion.min_deflated_sharpe)) warns.push(`Deflated Sharpe ${fmt.pct(dsr.value, 0)}: after ${dsr.n_trials} variations tried, this result could be luck (gate needs ${fmt.pct(data.promotion.min_deflated_sharpe, 0)}).`);
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
        bl.buy_and_hold ? { run: 'buy & hold', ...bl.buy_and_hold } : null, bl.no_trade ? { run: 'no trade', ...bl.no_trade } : null].filter(Boolean))
        : m.model ? kv([['Accuracy on unseen days', fmt.pct(m.model.accuracy, 1)], ['Always guessing “up”', fmt.pct(m.baselines?.base_rate?.accuracy, 1)],
          ['Predictions tested', fmt.num(m.n_predictions, 0)], ['Model', e.params.hidden ? `neural network ${e.params.hidden.join(' → ')}` : 'logistic regression']]) : pre(m),
      m.gate ? h('div', null, h('div', { class: 'small muted' }, 'Promotion gate'), (m.gate.reasons || []).length ? m.gate.reasons.map((x) => h('div', { class: 'small neg' }, '✕ ', x)) : h('div', { class: 'pos small' }, 'All checks passed')) : null));
  }

  let gone = false;
  async function run(kind, button) {
    const name = strategySel.value;
    runChart?.destroy(); runChart = null;
    runSlot.replaceChildren(h('div', { class: 'row muted' }, h('span', { class: 'typing' }, h('span'), h('span'), h('span')), kind === 'research' ? 'Running walk-forward research…' : 'Running backtest…'));
    const res = await ctx.act(kind, { strategy: name }, { button });
    if (gone) return;  // you left the page while it ran; the toast already showed the result
    if (!res) return runSlot.replaceChildren();
    if (!res.ok) return runSlot.replaceChildren(errorState(res.error));
    if (kind === 'research') { sel = res.experiment_id; load(); return runSlot.replaceChildren(h('div', { class: 'note' }, `Experiment #${res.experiment_id} recorded: ${res.gate.passed ? 'PASSED' : 'rejected'}. See the detail panel.`)); }
    const box = h('div', { class: 'chart short' });
    const mm = res.metrics;
    runSlot.replaceChildren(h('div', { class: 'note warn small' }, res.warning),
      h('div', { class: 'strip-stats' }, stat('Return', fmt.pct(mm.net_return, 1), '', fmt.tone(mm.net_return)), stat('Sharpe', fmt.num(mm.sharpe)), stat('Max DD', fmt.pct(mm.max_drawdown, 1)),
        stat('Trades', fmt.num(mm.n_trades, 0)), stat('Win rate', fmt.pct(mm.win_rate, 0)), stat('Profit factor', fmt.num(mm.profit_factor)), stat('Costs', fmt.num(mm.total_costs, 0), 'spread+slippage+financing')),
      h('div', { class: 'row small muted' }, h('span', { style: { color: C.ai } }, '━ ', res.strategy), h('span', { style: { color: C.text } }, '━ buy & hold (benchmark)')), box);
    queueMicrotask(() => { runChart = lineChart(box, [{ data: res.benchmark, color: C.text, width: 1 }, { data: res.equity, color: C.ai }]); });
  }

  load();
  return { el, destroy() { gone = true; runChart?.destroy(); } };
}

// ============================================================ News & events
export function news(ctx) {
  const slot = h('div');
  const ccys = relevantCcys(ctx.state);
  const allBox = h('input', { type: 'checkbox', checked: store.get('news.all', false), onchange: (e) => { store.set('news.all', e.target.checked); load(); } });
  const load = () => loadInto(slot, (sg) => api.get('/api/news', sg), (n) => h('div', { class: 'grid' },
    h('div', { class: 'c12' }, panel('Economic calendar', flush(table([
      { label: 'Time', render: (e) => when(e.event_time) }, { label: 'Ccy', key: 'currency' },
      { label: 'Impact', render: (e) => badge(e.impact, e.impact === 'high' ? 'neg' : e.impact === 'medium' ? 'warn' : '') },
      { label: 'Event', key: 'title' }, { label: 'Fcst', key: 'forecast' }, { label: 'Prev', key: 'previous' }, { label: 'Actual', key: 'actual' },
    ], allBox.checked ? n.events : n.events.filter((e) => ccys.includes(e.currency)), { empty: n.events.length ? `No ${ccys.join('/')} events stored for this week.` : 'No events stored for this week yet. Press “Refresh news & calendar”.', maxHeight: '520px' })),
      { meta: allBox.checked ? 'all countries · your local time' : `only ${ccys.join(' and ')}, which can move ${ctx.state.market.instrument} · your local time` })),
    h('div', { class: 'c12' }, h('div', { class: 'grid' }, h('div', { class: 'c5' },
      panel('News sources', flush(table([{ label: 'Source', key: 'source' }, { label: 'Status', render: (x) => (x.ok ? badge('ok', 'pos') : badge('failed', 'neg')) },
        { label: 'Items', key: 'items', num: true, d: 0 }, { label: 'Checked', render: (x) => when(x.at, 'ago') }, { label: 'Error', render: (x) => h('span', { class: 'small muted' }, x.error || '') }],
      n.health, { empty: 'No fetches recorded yet.' })))), h('div', { class: 'c7' },
      panel('Headlines', flush(table([{ label: 'Ingested', render: (x) => when(x.ingested_at, 'ago') },
        { label: 'Source', render: (x) => badge(x.source, x.reliability === 'official' ? 'info' : '') },
        { label: 'Title', render: (x) => (safeUrl(x.url) ? h('a', { href: safeUrl(x.url), target: '_blank', rel: 'noopener noreferrer' }, x.title) : x.title) }],
      n.news, { empty: 'No headlines stored yet.', maxHeight: '360px' })), { meta: 'from outside sources' }))))));
  const el = h('div', { class: 'stack', style: { gap: '12px' } },
    h('div', { class: 'row' }, h('button', { class: 'btn', dataset: { job: 'news' }, onclick: (e) => ctx.act('news-ingest', {}, { button: e.currentTarget }).then(load) }, icon('refresh'), 'Refresh news & calendar'),
      h('label', { class: 'check' }, allBox, 'Show every country'),
      h('span', { class: 'small muted' }, 'From central-bank news feeds and the Forex Factory calendar (this week only).')), slot);
  load();
  return { el };
}

// ============================================================ Activity & logs
export function activity(ctx) {
  const slot = h('div');
  const q = h('input', { class: 'input', placeholder: 'Filter…', value: ctx.params.q || '', 'aria-label': 'Filter activity', oninput: () => render() });
  const typeSel = h('select', { class: 'input', 'aria-label': 'Type', onchange: () => render() }, ['all', 'task', 'decision', 'order', 'log'].map((t) => h('option', { value: t }, t)));
  const levelSel = h('select', { class: 'input', 'aria-label': 'Log level', onchange: () => render() }, ['any level', 'ERROR', 'WARNING', 'INFO'].map((t) => h('option', { value: t }, t)));
  let rows = [];
  let open = null;
  const el = h('div', { class: 'stack', style: { gap: '12px' } }, h('div', { class: 'row' }, q, typeSel, levelSel,
    h('button', { class: 'btn sm', onclick: () => load() }, icon('refresh'), 'Reload')), panel('Everything that happened', flush(slot), { meta: 'checks, orders and system logs · newest first' }));

  function load() {
    loadInto(slot, (sg) => Promise.all([api.get('/api/decisions?limit=200', sg), api.get('/api/audit?limit=300', sg), api.get('/api/logs?limit=400', sg), api.get('/api/jobs?limit=200', sg)]), ([ds, au, lg, jobs]) => {
      rows = [
        ...jobs.map((j) => ({ at: j.started_at, type: 'task', level: j.state === 'succeeded' ? 'INFO' : j.state === 'running' ? 'RUNNING' : 'ERROR',
          text: `${j.label}: ${j.state === 'running' ? stepLine(j) : (j.state === 'succeeded' ? '' : 'FAILED, ') + jobOutcome(j)} (${Math.round(j.elapsed_s)} s)`, raw: j })),
        ...ds.map((d) => ({ at: d.decided_at, type: 'decision', level: 'INFO', text: decisionText(d), raw: d })),
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
      { label: 'Time', render: (r) => when(r.at) }, { label: 'Type', render: (r) => badge(r.type, r.type === 'decision' ? 'ai' : r.type === 'order' ? 'info' : r.type === 'task' ? 'pos' : '') },
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
        panel('Appearance', h('div', { class: 'stack' },
          h('div', { class: 'row' }, h('span', { style: { minWidth: '70px' } }, 'Motion'),
            segCtl('Motion', [['System', 'system'], ['Full', 'full'], ['Reduced', 'reduced']], () => store.get('motion', 'system'), (v) => {
              store.set('motion', v);
              document.documentElement.dataset.motion = v;
            })),
          h('div', { class: 'small muted' }, 'Page reveals, counting numbers, growing meters and drawing charts. “System” follows your computer’s setting; on Windows that is Settings → Accessibility → Visual effects → Animation effects.'),
          h('div', { class: 'small muted' }, 'Light or dark theme: the sun/moon button in the top-right corner.'))),
        panel('Keyboard', kv([['Command palette / search', h('span', null, h('kbd', null, 'Ctrl'), ' ', h('kbd', null, 'K'))], ['Companion', h('span', null, h('kbd', null, 'Ctrl'), ' ', h('kbd', null, '.'))],
          ['Close dialogs', h('kbd', null, 'Esc')]])),
        panel('Risk limits', pre(st.risk), { foot: 'Read-only. Edit config/local.yaml and restart; nothing at runtime writes risk configuration.' }),
        panel('Not available in this build', h('ul', { style: { margin: 0, paddingLeft: '18px' } },
          ['Enabling execution or live trading from the UI', 'Editing risk limits at runtime', 'Cancelling orders / closing positions',
            'Human-approval order queue', 'Agent start/pause (agents run inside decision cycles)', 'Custom dashboard layouts', 'Voice input/output'].map((x) => h('li', { class: 'muted' }, x)))),
        panel('About', h('div', { class: 'small muted' }, 'Charts by ', h('a', { href: 'https://www.tradingview.com/', target: '_blank', rel: 'noopener' }, 'TradingView Lightweight Charts™'),
          ' (Apache-2.0, see vendor/lightweight-charts.LICENSE.txt).')))));
  });
  return { el: slot };
}
