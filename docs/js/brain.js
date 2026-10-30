// 3D view of the trained neural network (direction_mlp): real weights and real
// activations from /api/brain. Node positions are layout only; everything that
// lights up is a value the network actually computed.
import * as THREE from '../vendor/three.module.min.js';
import { h, api, fmt, panel, badge, kv, emptyState, errorState, loading, store, reducedMotion } from './lib.js';

const LABELS = {
  r1: '1-day return', r5: '5-day return', r20: '20-day return', vol20: '20-day volatility', rsi14: 'RSI 14',
  dist_sma50: 'vs SMA 50', atr_pct: 'ATR %', mom_126: '6-month momentum', mom_252_21: '12-month momentum',
  dist_sma200: 'vs SMA 200', vol_ratio: 'volatility regime', tom: 'turn of month',
};
const RESEARCH = new Set(['mom_126', 'mom_252_21', 'dist_sma200', 'vol_ratio', 'tom']);
const STEM = ['market', 'news', 'quant', 'adversarial', 'coordinator', 'risk', 'execution'];
const COL = { pos: new THREE.Color('#2ebd85'), neg: new THREE.Color('#f0565b'), dim: new THREE.Color('#2b323b'), ai: new THREE.Color('#8b7cf6'), warn: new THREE.Color('#e8a13a'), info: new THREE.Color('#5aa9ff') };
const STANCE_COL = { long: COL.pos, short: COL.neg, neutral: COL.info, abstain: COL.ai };

function glowTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const g = c.getContext('2d');
  const grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grd.addColorStop(0, 'rgba(255,255,255,1)');
  grd.addColorStop(0.25, 'rgba(255,255,255,.55)');
  grd.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grd;
  g.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
}

// golden-angle disk so each layer reads as a cluster of neurons
function layerPositions(n, x, radius) {
  if (n === 1) return [new THREE.Vector3(x, 0, 0)];
  return Array.from({ length: n }, (_, i) => {
    const r = radius * Math.sqrt((i + 0.5) / n);
    const t = i * 2.39996;
    return new THREE.Vector3(x + Math.sin(t * 3) * 0.25, r * Math.cos(t), r * Math.sin(t) * 1.35);
  });
}

const tint = (a) => COL.dim.clone().lerp(a >= 0 ? COL.pos : COL.neg, Math.min(1, Math.abs(a)));

export function brain(ctx) {
  const reduce = reducedMotion();
  const stage = h('div', { class: 'brain-stage', tabindex: 0, role: 'img', 'aria-label': '3D view of the neural network. Drag to rotate, scroll to zoom, click a neuron for details.' });
  const labelsEl = h('div', { class: 'brain-labels', 'aria-hidden': 'true' });
  const tip = h('div', { class: 'brain-tip', style: { display: 'none' } });
  const side = h('div', { class: 'stack', style: { gap: '12px' } }, loading(6));
  const bar = h('div', { class: 'row' });
  const el = h('div', { class: 'stack', style: { gap: '10px' } }, bar,
    h('div', { class: 'grid' }, h('div', { class: 'c9' }, stage), h('div', { class: 'c3' }, side)));
  stage.append(labelsEl, tip);

  let data = null; let frame = 0; let playing = false; let speed = store.get('brain.speed', 1);
  let raf = 0; let playTimer = 0; let selected = null; let disposed = false;
  let renderer; let scene; let camera; let group; let edgeGeo; let pulseGeo; let ro;
  let dragging = false; let moved = false; let px = 0; let py = 0;
  const nodes = [];         // {mesh, glow, layer, idx, pos, shown, label?}
  const edges = [];         // {l, from, to, w}
  let pulses = [];
  let flow = [];            // per edge: |a_src * w|, normalised per layer
  const disposables = [];

  async function load(first) {
    try {
      const d = await api.get('/api/brain?history=250');
      if (disposed) return;
      if (!d.available) { side.replaceChildren(emptyState('No neural network yet', d.error)); stage.replaceChildren(emptyState('Nothing to show', d.error)); return; }
      const newer = !data || d.history.at(-1).time !== data.history.at(-1).time || d.version !== data.version;
      if (first || newer) {
        const rebuild = !data || d.version !== data.version;
        data = d;
        frame = d.history.length - 1;
        if (rebuild) build(); else applyFrame(false);
        renderControls();
        renderSide();
      }
    } catch (e) {
      if (first) side.replaceChildren(errorState(e, () => load(true)));
    }
  }

  // ------------------------------------------------------------ scene
  function build() {
    teardownScene();
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      stage.replaceChildren(emptyState('WebGL is not available in this browser'));
      return;
    }
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    stage.prepend(renderer.domElement);
    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(48, 1, 0.1, 200);
    camera.position.set(0, 0.6, store.get('brain.zoom', 19));
    group = new THREE.Group();
    group.rotation.set(0.18, -0.55, 0);
    scene.add(group);
    const tex = glowTexture();
    disposables.push(tex);

    // translucent two-lobed shell (layout decoration, explained in the legend)
    for (const z of [-1.15, 1.15]) {
      const g = new THREE.SphereGeometry(1, 40, 24);
      const m = new THREE.MeshBasicMaterial({ color: COL.ai, wireframe: true, transparent: true, opacity: 0.045, depthWrite: false });
      const shell = new THREE.Mesh(g, m);
      shell.scale.set(7.9, 4.6, 3.3);
      shell.position.set(0, 0.3, z);
      group.add(shell);
      disposables.push(g, m);
    }

    const L = data.layers;
    const xs = L.map((_, i) => -6.2 + (12.4 * i) / (L.length - 1));
    const radii = L.map((n) => (n === 1 ? 0 : 0.8 + 0.62 * Math.sqrt(n)));
    const sphere = new THREE.SphereGeometry(0.15, 16, 12);
    disposables.push(sphere);
    L.forEach((n, l) => layerPositions(n, xs[l], radii[l]).forEach((pos, i) => {
      const mat = new THREE.MeshBasicMaterial({ color: COL.dim.clone() });
      const mesh = new THREE.Mesh(sphere, mat);
      mesh.position.copy(pos);
      const glowMat = new THREE.SpriteMaterial({ map: tex, color: COL.dim.clone(), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false });
      const glow = new THREE.Sprite(glowMat);
      glow.position.copy(pos);
      group.add(mesh, glow);
      disposables.push(mat, glowMat);
      const node = { mesh, glow, layer: l, idx: i, pos, shown: 0 };
      mesh.userData.node = node;
      if (l === 0) node.label = LABELS[data.features[i]] || data.features[i];
      if (l === L.length - 1) node.label = 'P(SPY up next bar)';
      nodes.push(node);
    }));

    // every trained weight is one edge
    const posArr = [];
    data.weights.forEach((W, l) => W.forEach((row, from) => row.forEach((w, to) => {
      const a = nodeAt(l, from).pos; const b = nodeAt(l + 1, to).pos;
      edges.push({ l, from, to, w });
      posArr.push(a.x, a.y, a.z, b.x, b.y, b.z);
    })));
    edgeGeo = new THREE.BufferGeometry();
    edgeGeo.setAttribute('position', new THREE.Float32BufferAttribute(posArr, 3));
    edgeGeo.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(posArr.length), 3));
    const edgeMat = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false });
    group.add(new THREE.LineSegments(edgeGeo, edgeMat));
    disposables.push(edgeGeo, edgeMat);

    buildStem(tex, nodeAt(L.length - 1, 0).pos);

    // signal pulses travelling along the edges that carry the most signal right now
    const P = reduce ? 0 : 180;
    pulseGeo = new THREE.BufferGeometry();
    pulseGeo.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(P * 3), 3));
    pulseGeo.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(P * 3), 3));
    const pulseMat = new THREE.PointsMaterial({ size: 0.22, map: tex, vertexColors: true, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false });
    group.add(new THREE.Points(pulseGeo, pulseMat));
    disposables.push(pulseGeo, pulseMat);
    pulses = Array.from({ length: P }, () => ({ e: null, t: Math.random() }));

    for (const n of nodes) if (n.label) n.labelEl = labelsEl.appendChild(h('div', { class: `brain-label ${n.layer === 0 && RESEARCH.has(data.features[n.idx]) ? 'research' : ''} ${n.stem ? 'stem' : ''}` }, n.label));
    bindInput();
    ro = new ResizeObserver(resize);
    ro.observe(stage);
    resize();
    applyFrame(true);
    raf = requestAnimationFrame(tick);
  }

  // agent pipeline below the network: the "brainstem" that makes the actual decisions
  function buildStem(tex, outPos) {
    const d = ctx.state?.agent?.last_decision;
    const stances = Object.fromEntries((d?.reports || []).map((r) => [r.agent, r.stance]));
    const sphere = new THREE.SphereGeometry(0.22, 16, 12);
    disposables.push(sphere);
    const pts = STEM.map((id, i) => new THREE.Vector3(-5.4 + i * 1.8, -6.3, 0));
    const lineGeo = new THREE.BufferGeometry().setFromPoints(pts);
    const lineMat = new THREE.LineBasicMaterial({ color: '#3a424c' });
    group.add(new THREE.Line(lineGeo, lineMat));
    disposables.push(lineGeo, lineMat);
    STEM.forEach((id, i) => {
      const c = id === 'coordinator' ? (d ? ({ enter_long: COL.pos, enter_short: COL.neg, abstain: COL.ai, hold: COL.info, exit: COL.warn }[d.action] || COL.dim) : COL.dim)
        : STANCE_COL[stances[id]] || COL.dim;
      const mat = new THREE.MeshBasicMaterial({ color: c });
      const mesh = new THREE.Mesh(sphere, mat);
      mesh.position.copy(pts[i]);
      const gm = new THREE.SpriteMaterial({ map: tex, color: c, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.8 });
      const glow = new THREE.Sprite(gm);
      glow.position.copy(pts[i]);
      glow.scale.setScalar(1.3);
      group.add(mesh, glow);
      disposables.push(mat, gm);
      const node = { mesh, glow, layer: 'stem', id, pos: pts[i], stem: true,
        label: `${id}${stances[id] ? ': ' + stances[id] : id === 'coordinator' && d ? ': ' + d.action : ''}` };
      mesh.userData.node = node;
      nodes.push(node);
    });
    // the network's output is NOT connected to the quant agent yet: draw that gap honestly
    const gap = new THREE.BufferGeometry().setFromPoints([outPos, pts[2]]);
    const gapMat = new THREE.LineDashedMaterial({ color: '#e8a13a', dashSize: 0.25, gapSize: 0.2, transparent: true, opacity: 0.6 });
    const gapLine = new THREE.Line(gap, gapMat);
    gapLine.computeLineDistances();
    group.add(gapLine);
    disposables.push(gap, gapMat);
  }

  const nodeAt = (l, i) => nodes.find((n) => n.layer === l && n.idx === i);
  const actsAt = (k) => data.history[k].acts;

  // activation for display: inputs are z-scores (scaled to ~[-1,1]), hidden tanh, output centred on 0.5
  function shownAct(node, acts) {
    const v = acts[node.layer][node.idx];
    if (node.layer === 0) return Math.max(-1, Math.min(1, v / 2.5));
    if (node.layer === data.layers.length - 1) return Math.max(-1, Math.min(1, (v - 0.5) * 6));
    return v;
  }

  function applyFrame(instant) {
    if (!data || !edgeGeo) return;
    const acts = actsAt(frame);
    for (const n of nodes) if (!n.stem) { n.target = shownAct(n, acts); if (instant) n.shown = n.target; }
    const raw = edges.map((e) => Math.abs(acts[e.l][e.from] * e.w));
    const maxByLayer = {};
    edges.forEach((e, i) => { maxByLayer[e.l] = Math.max(maxByLayer[e.l] || 1e-9, raw[i]); });
    flow = raw.map((v, i) => v / maxByLayer[edges[i].l]);
    const col = edgeGeo.getAttribute('color');
    edges.forEach((e, i) => {
      const k = 0.06 + 0.94 * Math.pow(flow[i], 0.85);
      const sign = acts[e.l][e.from] * e.w >= 0 ? COL.pos : COL.neg;
      col.setXYZ(i * 2, sign.r * k * 0.55, sign.g * k * 0.55, sign.b * k * 0.55);
      col.setXYZ(i * 2 + 1, sign.r * k, sign.g * k, sign.b * k);
    });
    col.needsUpdate = true;
    for (const p of pulses) p.e = null;
    renderSide();
  }

  function pickEdge() { // sample an edge roughly in proportion to its current flow
    for (let tries = 0; tries < 12; tries++) {
      const i = (Math.random() * edges.length) | 0;
      if (Math.random() < flow[i] ** 1.5) return i;
    }
    return (Math.random() * edges.length) | 0;
  }

  let last = performance.now();
  function tick(now) {
    if (disposed) return;
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    if (!dragging && !reduce && store.get('brain.spin', true)) group.rotation.y += dt * 0.07;
    for (const n of nodes) {
      if (n.stem) continue;
      n.shown += ((n.target ?? 0) - n.shown) * (reduce ? 1 : Math.min(1, dt * 6));
      const c = tint(n.shown);
      n.mesh.material.color.copy(c);
      n.glow.material.color.copy(c);
      n.glow.scale.setScalar(0.55 + Math.abs(n.shown) * 1.9 + (n === selected ? 0.6 : 0));
    }
    if (pulses.length) {
      const pos = pulseGeo.getAttribute('position'); const col = pulseGeo.getAttribute('color');
      const acts = actsAt(frame);
      pulses.forEach((p, i) => {
        if (p.e == null) { p.e = pickEdge(); p.t = Math.random() * 0.3; }
        p.t += dt * (0.55 + 0.4 * speed);
        if (p.t >= 1) { p.e = pickEdge(); p.t = 0; }
        const e = edges[p.e];
        const a = nodeAt(e.l, e.from).pos; const b = nodeAt(e.l + 1, e.to).pos;
        pos.setXYZ(i, a.x + (b.x - a.x) * p.t, a.y + (b.y - a.y) * p.t, a.z + (b.z - a.z) * p.t);
        const c = acts[e.l][e.from] * e.w >= 0 ? COL.pos : COL.neg;
        const k = 0.25 + 0.75 * flow[p.e];
        col.setXYZ(i, c.r * k, c.g * k, c.b * k);
      });
      pos.needsUpdate = true; col.needsUpdate = true;
    }
    renderer.render(scene, camera);
    placeLabels();
    raf = requestAnimationFrame(tick);
  }

  const v3 = new THREE.Vector3();
  function placeLabels() {
    const w = stage.clientWidth; const hgt = stage.clientHeight;
    for (const n of nodes) {
      if (!n.labelEl) continue;
      v3.copy(n.pos).applyMatrix4(group.matrixWorld).project(camera);
      const vis = v3.z < 1;
      n.labelEl.style.display = vis ? '' : 'none';
      if (vis) n.labelEl.style.transform = `translate(${((v3.x + 1) / 2) * w}px, ${((1 - v3.y) / 2) * hgt}px) translate(-50%, -165%)`;
    }
  }

  function resize() {
    const w = stage.clientWidth; const hgt = stage.clientHeight;
    if (!w || !hgt) return;
    renderer.setSize(w, hgt, false);
    camera.aspect = w / hgt;
    camera.updateProjectionMatrix();
  }

  // ------------------------------------------------------------ input
  const ray = new THREE.Raycaster(); const ndc = new THREE.Vector2();
  function hit(e) {
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hits = ray.intersectObjects(nodes.map((n) => n.mesh), false);
    return hits[0]?.object.userData.node || null;
  }
  function bindInput() {
    const c = renderer.domElement;
    c.addEventListener('pointerdown', (e) => { dragging = true; moved = false; px = e.clientX; py = e.clientY; c.setPointerCapture(e.pointerId); });
    c.addEventListener('pointermove', (e) => {
      if (dragging) {
        const dx = e.clientX - px; const dy = e.clientY - py;
        if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
        group.rotation.y += dx * 0.006;
        group.rotation.x = Math.max(-1.2, Math.min(1.2, group.rotation.x + dy * 0.005));
        px = e.clientX; py = e.clientY;
        return;
      }
      const n = hit(e);
      c.style.cursor = n ? 'pointer' : 'grab';
      if (n) {
        const r = stage.getBoundingClientRect();
        tip.style.display = '';
        tip.style.left = `${e.clientX - r.left + 12}px`;
        tip.style.top = `${e.clientY - r.top + 12}px`;
        tip.textContent = describe(n);
      } else tip.style.display = 'none';
    });
    c.addEventListener('pointerup', (e) => { dragging = false; if (!moved) { selected = hit(e); renderSide(); } });
    c.addEventListener('pointerleave', () => { tip.style.display = 'none'; });
    c.addEventListener('wheel', (e) => {
      e.preventDefault();
      camera.position.z = Math.max(8, Math.min(34, camera.position.z + e.deltaY * 0.01));
      store.set('brain.zoom', camera.position.z);
    }, { passive: false });
    c.addEventListener('dblclick', () => { group.rotation.set(0.18, -0.55, 0); camera.position.z = 19; });
    stage.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowLeft') group.rotation.y -= 0.15;
      else if (e.key === 'ArrowRight') group.rotation.y += 0.15;
      else if (e.key === ' ') { e.preventDefault(); togglePlay(); }
    });
  }

  function describe(n) {
    if (n.stem) return `${n.label}. Rule-based agent in the decision pipeline (latest recorded decision).`;
    const a = actsAt(frame)[n.layer][n.idx];
    if (n.layer === 0) {
      const raw = frame === data.history.length - 1 ? data.raw_latest[data.features[n.idx]] : null;
      return `${n.label}: z-score ${fmt.num(a, 2)}${raw != null ? ` (raw ${fmt.num(raw, 4)})` : ''}`;
    }
    if (n.layer === data.layers.length - 1) return `${n.label}: ${fmt.pct(a, 1)}`;
    return `hidden layer ${n.layer} · neuron ${n.idx + 1} · activation ${fmt.num(a, 3)} (tanh, −1…1)`;
  }

  // ------------------------------------------------------------ controls & side panel
  function togglePlay() {
    if (!data) return;
    playing = !playing;
    clearInterval(playTimer);
    if (playing) {
      if (frame >= data.history.length - 1) frame = 0;
      playTimer = setInterval(() => {
        if (frame >= data.history.length - 1) { togglePlay(); return; }
        frame += 1; applyFrame(false); renderControls();
      }, 700 / speed);
    }
    renderControls();
  }

  function renderControls() {
    if (!data) return;
    const n = data.history.length;
    const cur = data.history[frame];
    const pred = cur.p_up >= 0.5 ? 'up' : 'down';
    bar.replaceChildren(
      h('button', { class: 'btn primary sm', onclick: togglePlay, 'aria-label': playing ? 'Pause replay' : 'Replay history' }, playing ? 'Pause' : 'Replay'),
      h('div', { class: 'seg', role: 'group', 'aria-label': 'Replay speed' }, [1, 3, 8].map((x) => h('button', { class: speed === x ? 'on' : '',
        onclick: () => { speed = x; store.set('brain.speed', x); if (playing) { togglePlay(); togglePlay(); } renderControls(); } }, `${x}x`))),
      h('input', { type: 'range', min: 0, max: n - 1, value: frame, class: 'brain-slider', 'aria-label': 'Bar in history',
        oninput: (e) => { frame = Number(e.target.value); applyFrame(false); renderControls(); } }),
      h('button', { class: 'btn sm', onclick: () => { frame = n - 1; applyFrame(false); renderControls(); } }, 'Latest'),
      h('span', { class: 'mono small' }, fmt.date(cur.time)),
      h('span', { class: 'small' }, `P(up) ${fmt.pct(cur.p_up, 1)} → `, badge(pred, pred === 'up' ? 'pos' : 'neg')),
      h('span', { class: 'small' }, 'actual: ', cur.actual_up == null ? badge('not known yet') : badge(cur.actual_up ? 'up' : 'down', cur.actual_up ? 'pos' : 'neg'),
        cur.actual_up == null ? '' : (cur.actual_up === (pred === 'up') ? ' ✓' : ' ✗')),
      h('label', { class: 'check', style: { marginLeft: 'auto' } }, h('input', { type: 'checkbox', checked: store.get('brain.spin', true) && !reduce, disabled: reduce,
        onchange: (e) => store.set('brain.spin', e.target.checked) }), 'Auto-rotate'));
  }

  function renderSide() {
    if (!data) return;
    const known = data.history.filter((x) => x.actual_up != null);
    const hits = known.filter((x) => (x.p_up >= 0.5) === x.actual_up).length;
    const ups = known.filter((x) => x.actual_up).length;
    const m = data.model || {}; const b = data.baseline || {};
    side.replaceChildren(
      panel('Neural network', h('div', { class: 'stack' },
        h('div', { class: 'row' }, badge(data.verdict || 'unknown', data.verdict === 'accepted' ? 'pos' : 'neg'), badge('not used by agents', 'warn')),
        kv([['Shape', `${data.layers.join(' → ')} (${data.activation})`], ['Parameters', `${data.n_params} trained weights and biases`],
          ['Version', h('span', { class: 'mono small' }, data.version)], ['Data', `${data.instrument} daily · ${data.data_source}`],
          ['Walk-forward accuracy', `${fmt.pct(m.accuracy, 1)} on ${data.n_predictions} unseen days`],
          ['Always guess “up”', `${fmt.pct(b.accuracy, 1)} (base rate)`]]),
        (data.reasons || []).map((r) => h('div', { class: 'small neg' }, '✕ ', r)),
        h('div', { class: 'small muted' }, `In this replay window: ${hits}/${known.length} right (${fmt.pct(known.length ? hits / known.length : null, 1)}); “always up” would get ${fmt.pct(known.length ? ups / known.length : null, 1)}.`))),
      panel('Selected', selected ? selectedDetail(selected) : h('div', { class: 'small muted' }, 'Click a neuron or agent.')),
      panel('How to read it', h('div', { class: 'stack small' },
        h('div', null, h('span', { class: 'pos' }, '●'), ' positive  ', h('span', { class: 'neg' }, '●'), ' negative. Brightness and glow = how strongly a neuron fires on the selected day.'),
        h('div', null, `Lines are all ${edges.length} trained weights; bright lines carry the most signal that day. Moving dots animate that flow.`),
        h('div', null, `Left → right: ${data.layers[0]} inputs (`, h('span', { class: 'ai' }, 'violet labels'), ' = research features), two hidden layers, output P(up).'),
        h('div', null, 'Bottom row: the rule-based agents from the latest decision. The dashed amber line marks that the network is not connected to the quant agent.'),
        h('div', { class: 'dim' }, 'Positions and the shell are layout only. ', data.note, ` Forward pass verified against scikit-learn (max diff ${data.forward_pass_check}).`),
        h('div', { class: 'dim' }, 'Drag to rotate · scroll to zoom · double-click to reset · Space to play.'))));
  }

  function selectedDetail(n) {
    if (n.stem) return h('div', { class: 'small' }, describe(n), ' ', h('a', { href: '#/agents' }, 'Open trace →'));
    const acts = actsAt(frame);
    const L = data.layers.length;
    const nameOf = (l, i) => (l === 0 ? LABELS[data.features[i]] : l === L - 1 ? 'output' : `h${l}·${i + 1}`);
    const incoming = n.layer === 0 ? [] : data.weights[n.layer - 1].map((row, from) => ({ from, c: acts[n.layer - 1][from] * row[n.idx] }))
      .sort((a, b) => Math.abs(b.c) - Math.abs(a.c)).slice(0, 5);
    const outgoing = n.layer === L - 1 ? [] : data.weights[n.layer][n.idx].map((w, to) => ({ to, w })).sort((a, b) => Math.abs(b.w) - Math.abs(a.w)).slice(0, 5);
    return h('div', { class: 'stack small' }, h('div', null, describe(n)),
      n.layer > 0 ? h('div', null, 'bias ', h('span', { class: 'mono' }, fmt.num(data.biases[n.layer - 1][n.idx], 3))) : null,
      incoming.length ? h('div', null, h('div', { class: 'muted' }, 'Strongest inputs on this day (activation × weight)'),
        incoming.map((x) => h('div', { class: 'mono' }, `${nameOf(n.layer - 1, x.from)}: ${fmt.signed(x.c, 3)}`))) : null,
      outgoing.length ? h('div', null, h('div', { class: 'muted' }, 'Largest outgoing weights'),
        outgoing.map((x) => h('div', { class: 'mono' }, `→ ${nameOf(n.layer + 1, x.to)}: ${fmt.signed(x.w, 3)}`))) : null);
  }

  function teardownScene() {
    cancelAnimationFrame(raf);
    ro?.disconnect();
    for (const d of disposables.splice(0)) d.dispose?.();
    renderer?.dispose();
    renderer?.domElement.remove();
    nodes.length = 0; edges.length = 0; labelsEl.replaceChildren();
  }

  load(true);
  const pollTimer = setInterval(() => load(false), 60000);  // a newly ingested bar changes the activations
  return {
    el,
    destroy() { disposed = true; clearInterval(playTimer); clearInterval(pollTimer); teardownScene(); },
  };
}
