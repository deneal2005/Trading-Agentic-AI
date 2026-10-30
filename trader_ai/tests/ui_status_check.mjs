// self-check for controls.deriveStatus with a stubbed DOM
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
// a live page: the server filled in the token (no token = published snapshot mode)
globalThis.document = { querySelector: () => ({ content: 'test-token' }), createElement: () => ({}), addEventListener() {}, dispatchEvent() {} };
globalThis.matchMedia = () => ({ matches: true });
globalThis.Event = class {};
const { deriveStatus } = await import(pathToFileURL(process.argv[2]).href);
const now = Date.now();
const base = () => ({ kill_switch: { engaged: false }, mode: { execution_enabled: false, require_promotion: true }, strategy: { name: 'sma_cross' },
  broker: { available: true, broker: 'alpaca', environment: 'alpaca-paper', positions: [], open_orders: [] },
  activity: { running: [], recent: [], service: { alive: false } } });
const hub = (st, o = {}) => ({ state: st, stateAt: now, error: null, activity: null, pending: new Map(), promoted: null, ...o });
let s = deriveStatus(hub(base()), now);
assert.equal(s.conn.key, 'live'); assert.equal(s.engine.key, 'observe'); assert.equal(s.kill.key, 'off');
assert.equal(s.sched.key, 'stopped'); assert.equal(s.pos.key, 'flat'); assert.equal(s.check.key, 'idle');
// disconnected: nothing claims to be running
s = deriveStatus(hub(base(), { error: new Error('x') }), now);
for (const k of ['engine', 'kill', 'sched', 'pos', 'check']) assert.equal(s[k].key, 'unknown', k);
assert.equal(s.conn.key, 'offline');
// stale data
assert.equal(deriveStatus(hub(base(), { stateAt: now - 60000 }), now).conn.key, 'stale');
// running check beats pending; pending shows starting
const st = base(); st.activity.running = [{ kind: 'check', steps: ['a', 'b'], step: 'b', done_steps: ['a'] }];
assert.equal(deriveStatus(hub(st, { pending: new Map([['run-once', now - 1000]]) }), now).check.key, 'running');
assert.equal(deriveStatus(hub(base(), { pending: new Map([['run-once', now - 1000]]) }), now).check.key, 'starting');
// failed last check
const f = base(); f.activity.recent = [{ kind: 'check', state: 'failed', error: 'boom' }];
assert.equal(deriveStatus(hub(f), now).check.key, 'failed');
// kill transitions only flip after the state confirms
const k = base(); k.kill_switch = { engaged: true, reason: 'ui: engaged from UI' };
assert.equal(deriveStatus(hub(base(), { pending: new Map([['kill', now]]) }), now).kill.key, 'engaging');
assert.equal(deriveStatus(hub(k), now).kill.key, 'on'); assert.equal(deriveStatus(hub(k), now).engine.key, 'halted');
// orders on but strategy not promoted = blocked, not "running"
const e = base(); e.mode.execution_enabled = true;
assert.equal(deriveStatus(hub(e, { promoted: false }), now).engine.key, 'blocked');
assert.equal(deriveStatus(hub(e, { promoted: true }), now).engine.key, 'armed');
// scheduler alive / overdue; broker offline -> holdings unknown
const sv = base(); sv.activity.service = { alive: true, next_at: new Date(now + 6e4).toISOString(), interval_s: 900 };
assert.equal(deriveStatus(hub(sv), now).sched.key, 'running');
const ov = base(); ov.activity.service = { alive: false, overdue: true, at: new Date(now - 9e5).toISOString() };
assert.equal(deriveStatus(hub(ov), now).sched.key, 'error');
const bo = base(); bo.broker = { available: false, error: 'net' };
assert.equal(deriveStatus(hub(bo), now).pos.key, 'unknown'); assert.equal(deriveStatus(hub(bo), now).conn.key, 'broker');
const lp = base(); lp.broker.positions = [{ instrument: 'SPY', units: 10, avg_price: 500 }];
assert.equal(deriveStatus(hub(lp), now).pos.label, 'Long 10 SPY');
// our check ended (after the request started) -> finishing, not starting again
const fin = base(); fin.activity.recent = [{ kind: 'check', state: 'succeeded', finished_at: new Date(now).toISOString(), result: {} }];
assert.equal(deriveStatus(hub(fin, { pending: new Map([['run-once', now - 1000]]) }), now).check.key, 'finishing');
console.log('deriveStatus: all checks passed');
