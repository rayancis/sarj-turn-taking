"use strict";
// SARJ demo page. The audio clock (one AudioContext for both channels) is the master: its playhead decides which
// 20 ms frames the server feeds to the real detector. Decisions come back from the server; nothing is pre-scripted.

const $ = (id) => document.getElementById(id);
const POLL_MS = 15;

const S = {
  config: null, models: {}, model: null, scenId: null, timeline: null, audio: null, peaks: null,
  ctx: null, nodes: null, sources: null, t0: 0, started: false, playing: false, ended: false,
  gen: 0, loopGen: null, session: null, streamEndMs: 0, fedMs: 0, done: false, decisions: [], protocolCount: 0,
  score: null, meanOnFrameUs: null, lastServerMs: null, agentMuted: false, staticLayer: null, busy: false,
};

// ------------------------------------------------------------------------------------------------ helpers
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sec = (ms) => (ms / 1000).toFixed(2);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opts);
  const isJson = (r.headers.get("Content-Type") || "").includes("json");
  const data = isJson ? await r.json() : await r.arrayBuffer();
  if (!r.ok) throw new Error((isJson && data.error) || `${r.status} ${r.statusText}`);
  return data;
}

function showError(msg) {
  const el = $("error");
  el.hidden = !msg;
  el.textContent = msg || "";
}

// Minimal PCM WAV reader: keeps the exact 16 kHz samples and splits channel 0 (agent) from channel 1 (caller).
function parseWav(buf) {
  const v = new DataView(buf);
  const tag = (o) => String.fromCharCode(v.getUint8(o), v.getUint8(o + 1), v.getUint8(o + 2), v.getUint8(o + 3));
  if (tag(0) !== "RIFF" || tag(8) !== "WAVE") throw new Error("not a WAV file");
  let o = 12, fmt = null, data = null;
  while (o + 8 <= v.byteLength) {
    const id = tag(o), size = v.getUint32(o + 4, true);
    if (id === "fmt ") fmt = { format: v.getUint16(o + 8, true), channels: v.getUint16(o + 10, true), rate: v.getUint32(o + 12, true), bits: v.getUint16(o + 22, true) };
    if (id === "data") { data = { offset: o + 8, size: Math.min(size, v.byteLength - o - 8) }; break; }
    o += 8 + size + (size & 1);
  }
  if (!fmt || !data || fmt.format !== 1 || fmt.bits !== 16 || fmt.channels !== 2) throw new Error("expected 16-bit stereo PCM WAV");
  const n = Math.floor(data.size / 4), agent = new Float32Array(n), caller = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    agent[i] = v.getInt16(data.offset + 4 * i, true) / 32768;
    caller[i] = v.getInt16(data.offset + 4 * i + 2, true) / 32768;
  }
  return { rate: fmt.rate, agent, caller };
}

// ------------------------------------------------------------------------------------------------ audio engine
function ensureContext() {
  if (S.ctx) return;
  const ctx = new AudioContext();
  const mk = () => {
    const gain = ctx.createGain(), an = ctx.createAnalyser();
    an.fftSize = 1024;
    gain.connect(an).connect(ctx.destination);
    return { gain, an };
  };
  S.ctx = ctx;
  S.nodes = { agent: mk(), caller: mk() };
}

function makeBuffer(samples, rate) {
  const b = S.ctx.createBuffer(1, samples.length, rate);
  b.copyToChannel(samples, 0);
  return b;
}

function stopSources() {
  if (!S.sources) return;
  for (const s of Object.values(S.sources)) { try { s.stop(); } catch (_) { /* already stopped */ } s.disconnect(); }
  S.sources = null;
}

function startSources() {
  // Both channels start at the same instant of the same clock, from offset 0: they stay sample-locked, and pausing
  // (suspending the context) freezes both at once.
  const ctx = S.ctx, when = ctx.currentTime + 0.05;
  S.sources = {};
  for (const ch of ["agent", "caller"]) {
    const src = ctx.createBufferSource();
    src.buffer = S.audio.buffers[ch];
    src.connect(S.nodes[ch].gain);
    src.start(when, 0);
    S.sources[ch] = src;
  }
  S.t0 = when;
  for (const ch of ["agent", "caller"]) {
    S.nodes[ch].gain.gain.cancelScheduledValues(0);
    S.nodes[ch].gain.gain.setValueAtTime(1, ctx.currentTime);
  }
  S.agentMuted = false;
}

function playhead() {
  if (!S.started || !S.ctx) return 0;
  return Math.max(0, (S.ctx.currentTime - S.t0) * 1000);
}

// ------------------------------------------------------------------------------------------------ detector session
async function newSession() {
  const gen = ++S.gen;
  stopSources();
  Object.assign(S, { started: false, playing: false, ended: false, session: null, fedMs: 0, done: false,
    decisions: [], protocolCount: 0, score: null, meanOnFrameUs: null, lastServerMs: null, agentMuted: false });
  if (S.ctx && S.ctx.state === "running") await S.ctx.suspend();
  $("log").innerHTML = "";
  renderEvents(); renderTiming(); updatePlayButton();
  const r = await api("/api/session", { scenario: S.scenId, model: S.model });
  if (gen !== S.gen) return;
  S.session = r.session_id;
  S.streamEndMs = r.stream_end_ms;
  addLog(0, "info", `Fresh detector: ${esc(r.model.name)} @ ${esc(r.model.revision || "?")}`, "ctl");
  updatePlayButton();
}

async function advance(gen, toMs) {
  const r = await api(`/api/session/${S.session}/advance`, { to_ms: toMs });
  if (gen !== S.gen) return;                       // stale response from a previous scenario/model/reset
  const receivedAt = playhead();
  S.fedMs = r.fed_ms; S.done = r.done; S.score = r.score;
  S.meanOnFrameUs = r.mean_on_frame_us; S.lastServerMs = r.server_ms;
  for (const p of r.protocol) logControl(p);
  for (const d of r.decisions) {
    d.receivedAt = receivedAt;
    d.pageDelayMs = S.ended ? null : receivedAt - d.stream_ms;
    S.decisions.push(d);
    logDecision(d);
  }
  if (r.decisions.length) updateMute();
  if (r.errors.length) showError(`Detector/protocol error: ${r.errors.map((e) => `${sec(e.stream_ms)} s ${e.error}`).join("; ")}`);
  renderEvents(); renderTiming();
}

async function feedLoop(gen) {
  if (S.loopGen === gen) return;                   // one loop per session: requests stay strictly ordered
  S.loopGen = gen;
  try { await feedTicks(gen); } finally { if (S.loopGen === gen) S.loopGen = null; }
}

async function feedTicks(gen) {
  // Each tick sends the playhead; the server feeds every frame whose start time it has reached. One request at a
  // time, so frames are delivered in order; while paused the playhead does not move and nothing is fed.
  while (gen === S.gen && S.playing) {
    const t = playhead();
    try {
      if (t >= S.timeline.duration_ms) { await finish(gen); return; }
      if (t >= S.fedMs && !S.done) await advance(gen, t);
    } catch (e) {
      if (gen === S.gen) { showError(`Server error: ${e.message}. Press Reset.`); await pause(); }
      return;
    }
    await sleep(POLL_MS);
  }
}

async function finish(gen) {
  S.ended = true;
  await advance(gen, S.streamEndMs);              // the harness also streams a short silent tail after the call
  if (gen !== S.gen) return;
  S.playing = false;
  stopSources();
  if (S.ctx.state === "running") await S.ctx.suspend();
  updatePlayButton();
}

// ------------------------------------------------------------------------------------------------ controls
async function play() {
  if (S.busy || !S.session) return;
  ensureContext();
  if (S.ended) { await reset(); }
  await S.ctx.resume();
  if (!S.started) { startSources(); S.started = true; }
  S.playing = true;
  updatePlayButton();
  feedLoop(S.gen);
}

async function pause() {
  S.playing = false;
  if (S.ctx && S.ctx.state === "running") await S.ctx.suspend();
  updatePlayButton();
}

async function reset() {
  await guarded(async () => { await newSession(); });
}

async function guarded(fn) {
  S.busy = true; updatePlayButton();
  try { showError(""); await fn(); } catch (e) { showError(e.message); } finally { S.busy = false; updatePlayButton(); }
}

function updatePlayButton() {
  const b = $("play");
  b.textContent = S.playing ? "Pause" : (S.ended ? "Replay" : (S.started ? "Resume" : "Play"));
  b.disabled = S.busy || !S.session;
  $("reset").disabled = S.busy || !S.session;
}

async function loadScenario(id) {
  await guarded(async () => {
    S.scenId = id;
    ensureContext();
    const gen = ++S.gen;
    stopSources();
    S.started = S.playing = S.ended = false;
    const [tl, wav] = await Promise.all([api(`/api/scenario/${id}`), api(`/api/audio/${id}.wav`)]);
    if (gen !== S.gen) return;
    const a = parseWav(wav);
    S.timeline = tl;
    S.audio = { rate: a.rate, agent: a.agent, caller: a.caller,
      buffers: { agent: makeBuffer(a.agent, a.rate), caller: makeBuffer(a.caller, a.rate) } };
    $("duration").textContent = sec(tl.duration_ms);
    renderScenarioInfo();
    buildStaticLayer();
    await newSession();
  });
}

async function changeModel(id) {
  S.model = id;
  renderModelChip();
  renderBench();
  await reset();
}

// ------------------------------------------------------------------------------------------------ live state
function stopStateAt(t) {
  // Agent behaviour implied by the real decisions: after a STOP the agent stays silent until the end of that
  // utterance (or a RESUME), as the official scorer reconstructs it.
  const utts = S.timeline ? S.timeline.protocol.agent_utterances : [];
  const u = utts.find((x) => x.start_ms <= t && t < x.end_ms);
  if (!u) return { utt: null, muted: false, stopAt: null };
  let stopAt = null;
  for (const d of S.decisions) {
    if (d.utt_id !== u.utt_id || d.stream_ms > t) continue;
    if (d.decision === "STOP" && stopAt === null) stopAt = d.stream_ms;
    else if (d.decision === "RESUME") stopAt = null;
  }
  return { utt: u, muted: stopAt !== null, stopAt };
}

function updateMute() {
  if (!S.ctx || !S.started) return;
  const st = stopStateAt(playhead());
  if (st.muted !== S.agentMuted) {
    S.agentMuted = st.muted;
    const g = S.nodes.agent.gain.gain;
    g.cancelScheduledValues(S.ctx.currentTime);
    g.setTargetAtTime(st.muted ? 0 : 1, S.ctx.currentTime, 0.004);
  }
}

function uttStop(u) {
  const d = S.decisions.find((x) => x.utt_id === u.utt_id && x.decision === "STOP");
  return d ? d.stream_ms : null;
}

function renderStatus(t) {
  const el = $("status"), txt = $("utt-text");
  let cls = "idle", msg;
  const st = stopStateAt(t);
  const utts = S.timeline ? S.timeline.protocol.agent_utterances : [];
  const stops = S.decisions.filter((d) => d.decision === "STOP").length;
  if (!S.timeline) msg = "Loading…";
  else if (S.ended) {
    msg = stops ? `Finished: ${stops} STOP decision${stops === 1 ? "" : "s"} from ${modelLabel()}.`
      : `Finished: no STOP from ${modelLabel()}; the agent spoke every utterance to the end.`;
    cls = stops ? "stopped" : "kept";
  } else if (!S.started) msg = "Ready. Press Play (or Space).";
  else if (st.utt && st.muted) {
    cls = "stopped";
    msg = `STOP at ${sec(st.stopAt)} s: agent silenced for the rest of ${st.utt.utt_id}; caller audio continues.`;
  } else if (st.utt) {
    cls = "speaking";
    msg = `Agent speaking (${st.utt.utt_id}): no STOP issued so far.`;
  } else {
    const prev = utts.filter((u) => u.end_ms <= t).pop();
    if (!prev) msg = "Agent not speaking yet.";
    else if (uttStop(prev) !== null) { cls = "stopped"; msg = `${prev.utt_id}: agent yielded (STOP at ${sec(uttStop(prev))} s).`; }
    else { cls = "kept"; msg = `${prev.utt_id}: agent finished the utterance; no STOP was issued.`; }
  }
  if (S.started && !S.playing && !S.ended) msg = `Paused · ${msg}`;
  el.className = `status ${cls}`;
  el.textContent = msg;
  const cur = st.utt || utts.find((u) => u.start_ms > t);
  txt.textContent = cur ? `${cur.utt_id}${st.utt ? "" : " (next)"}: ${cur.text}` : "";
}

function levelPct(an) {
  const buf = new Float32Array(an.fftSize);
  an.getFloatTimeDomainData(buf);
  let s = 0;
  for (const x of buf) s += x * x;
  const db = 10 * Math.log10(s / buf.length + 1e-12);
  return Math.max(0, Math.min(100, ((db + 60) / 60) * 100));
}

function frame() {
  const t = Math.min(playhead(), S.timeline ? S.timeline.duration_ms : 0);
  if (S.playing) updateMute();
  $("clock").textContent = sec(t);
  renderStatus(t);
  if (S.nodes) {
    const live = S.playing;
    $("m-agent").style.width = `${live ? levelPct(S.nodes.agent.an) : 0}%`;
    $("m-caller").style.width = `${live ? levelPct(S.nodes.caller.an) : 0}%`;
  }
  drawTimeline(t);
  requestAnimationFrame(frame);
}

// ------------------------------------------------------------------------------------------------ log & panels
function addLog(ms, kind, html, cls) {
  const tr = document.createElement("tr");
  tr.className = cls;
  tr.innerHTML = `<td class="num">${sec(ms)} s</td><td>${kind}</td><td>${html}</td>`;
  $("log").appendChild(tr);
  const wrap = $("log-wrap");
  wrap.scrollTop = wrap.scrollHeight;
}

function logControl(p) {
  S.protocolCount++;
  if (p.type === "agent_utterance_start") addLog(p.stream_ms, "→ detector", `agent_utterance_start ${esc(p.utt_id)} (${sec(p.duration_ms)} s, with text)`, "ctl");
  else if (p.type === "agent_utterance_end") addLog(p.stream_ms, "→ detector", `agent_utterance_end ${esc(p.utt_id)}`, "ctl");
  else if (p.type === "scenario_start") addLog(p.stream_ms, "→ detector", `scenario_start (context: ${esc(p.context.use_case)})`, "ctl");
  else if (p.type === "scenario_end") addLog(p.stream_ms, "→ detector", "scenario_end", "ctl");
}

function logDecision(d) {
  const pill = `<span class="pill ${d.decision === "STOP" ? "STOP" : "other"}">${esc(d.decision)}</span>`;
  const where = d.utt_id ? `during ${esc(d.utt_id)}` : "agent not speaking";
  const extra = d.decision === "TAKE_TURN" ? " · end-of-turn track (separate)" : "";
  const delay = d.pageDelayMs === null ? "" : ` · shown ${Math.round(d.pageDelayMs)} ms after frame time`;
  addLog(d.stream_ms, pill, `${where} · frame ${d.frame} · on_frame ${d.on_frame_us} µs${delay}${extra}`, "dec");
}

function modelLabel() {
  const m = S.models[S.model];
  return m ? `${m.name} @ ${m.revision || "?"}${m.dirty ? " (uncommitted)" : ""}` : "?";
}

function renderModelChip() {
  $("model-chip").textContent = `Model: ${modelLabel()}`;
}

function renderTiming() {
  const delays = S.decisions.map((d) => d.pageDelayMs).filter((x) => x !== null && x !== undefined).sort((a, b) => a - b);
  const med = delays.length ? delays[Math.floor(delays.length / 2)] : null;
  const rows = [
    ["Active detector", esc(modelLabel())],
    ["Frames fed", `${Math.round(S.fedMs / 20)} (${sec(S.fedMs)} s of stream)`],
    ["Detector compute", S.meanOnFrameUs === null ? "–" : `${S.meanOnFrameUs} µs mean per 20 ms frame`],
    ["Page update after decision frame", med === null ? "–" : `median ${Math.round(med)} ms, max ${Math.round(delays[delays.length - 1])} ms (${delays.length})`],
  ];
  $("timing").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
}

function renderScenarioInfo() {
  const s = S.config.scenarios.find((x) => x.id === S.scenId);
  const gt = S.timeline.ground_truth;
  $("scenario-info").innerHTML = `<b>${esc(s.id)}</b> · ${esc(s.category)} ${esc(s.category_name)} · ${esc(gt.subtype)} · ` +
    `difficulty ${esc(s.difficulty)} · ${esc(s.dialect)} · ${esc(s.acoustic.join(" + "))} · labels: ${esc(gt.label_status)}`;
}

function resultCell(ev, r) {
  if (!r || !r.closed) return `<span class="pending">pending · decided by ${sec(r ? r.close_ms : 0)} s</span>`;
  const c = r.credit ?? 0;
  const cls = c >= 1 ? "ok" : c > 0 ? "part" : "bad";
  const mark = c >= 1 ? "✓" : c > 0 ? "◐" : "✗";
  let text;
  if (ev.kind !== "interruption") text = `End-of-turn track (separate): ${r.detail}`;
  else if (r.outcome === "yield") text = `Stopped${r.latency_ms !== null && r.latency_ms !== undefined ? `, ${Math.round(r.latency_ms)} ms after intent was evident` : ""}`;
  else if (r.outcome === "miss") text = "Missed: did not stop in time";
  else if (r.outcome === "kept") text = "Kept speaking";
  else if (r.outcome === "false_stop") text = `False stop (${r.detail})`;
  else text = r.detail || r.outcome;
  return `<span class="${cls}">${mark} ${esc(text)}</span> <span class="pending">credit ${c}</span>`;
}

function renderEvents() {
  if (!S.timeline) return;
  const gt = S.timeline.ground_truth;
  const segs = Object.fromEntries(gt.user_segments.map((s) => [s.seg_id, s]));
  const scored = S.score ? S.score.events : [];
  $("events").innerHTML = gt.events.map((ev, i) => {
    const words = (ev.segment_ids || []).map((id) => segs[id] && (segs[id].text || `[${segs[id].kind}]`)).filter(Boolean).join(" … ");
    const exp = ev.expected === "EITHER" ? `EITHER<br><small>prefers ${esc(ev.preferred || "-")}</small>` : esc(ev.expected);
    const when = `${sec(ev.onset_ms)}–${sec(ev.offset_ms ?? ev.turn_end_ms ?? ev.onset_ms)} s`;
    const r = scored[i] || { closed: false, close_ms: 0 };
    return `<tr><td>${esc(ev.event_id.split(".").pop())}<br><small>${esc(ev.kind)}</small></td>` +
      `<td><span class="pill ${esc(ev.expected)}">${exp}</span></td>` +
      `<td><span dir="rtl" lang="ar">${esc(words || "–")}</span><br><small>${when}</small></td>` +
      `<td>${resultCell(ev, r)}</td></tr>`;
  }).join("");
  const p = S.score && S.score.protected;
  $("protected").innerHTML = p
    ? `Stops outside labelled events (they lower Restraint): ${p.spurious_stops.length ? p.spurious_stops.map((x) => `${sec(x)} s`).join(", ") : "none"}. ` +
      `<span class="pending">Call restraint credit ${p.credit}.</span>`
    : "";
}

function renderBench() {
  const b = S.config.benchmark;
  const active = S.models[S.model];
  if (!b.available) { $("bench").innerHTML = `<tr><td colspan="7">No recorded evidence found.</td></tr>`; return; }
  const f = (x, d = 2) => (x === null || x === undefined ? "–" : Number(x).toFixed(d));
  $("bench").innerHTML = b.rows.map((r) => `<tr class="${active && r.commit === active.revision ? "active" : ""}">` +
    `<td>${esc(r.name)}</td><td><code>${esc(r.commit)}</code></td><td class="num">${f(r.core)}</td><td class="num">${f(r.R, 3)}</td>` +
    `<td class="num">${f(r.S, 3)}</td><td class="num">${esc(r.misses)}</td><td class="num">${esc(r.false_stops)}</td></tr>`).join("");
  $("bench-note").textContent = `Development-set results from the official scorer, recorded earlier by the real-time harness ` +
    `(speed ${b.speed}, ${b.calls}; ${b.data ? b.data.version : ""}). Source: ${b.source}. They are not live scores and say nothing ` +
    `about hidden-test performance; 24 synthetic calls with script-derived labels. The highlighted row matches the active detector's commit.`;
}

// ------------------------------------------------------------------------------------------------ timeline canvas
const LANES = { left: 70, right: 10, axis: 18, agent: [24, 78], caller: [108, 78], labels: [194, 44] };

function canvasSize() {
  const cv = $("timeline");
  const w = Math.max(320, cv.clientWidth), h = 250, dpr = window.devicePixelRatio || 1;
  return { cv, w, h, dpr };
}

function xOf(ms, w) {
  return LANES.left + (ms / S.timeline.duration_ms) * (w - LANES.left - LANES.right);
}

function buildStaticLayer() {
  if (!S.timeline || !S.audio) return;
  const { cv, w, h, dpr } = canvasSize();
  cv.width = w * dpr; cv.height = h * dpr;
  const off = document.createElement("canvas");
  off.width = w * dpr; off.height = h * dpr;
  const g = off.getContext("2d");
  g.scale(dpr, dpr);
  const dur = S.timeline.duration_ms;
  // time grid
  g.font = "11px system-ui, sans-serif"; g.textBaseline = "top";
  const step = dur > 40000 ? 5000 : 2000;
  for (let ms = 0; ms <= dur; ms += step) {
    const x = xOf(ms, w);
    g.fillStyle = css("--grid"); g.fillRect(x, LANES.axis, 1, h - LANES.axis);
    g.fillStyle = css("--muted"); g.fillText(`${ms / 1000}s`, x + 2, 2);
  }
  // agent utterances (protocol input)
  for (const u of S.timeline.protocol.agent_utterances) {
    g.fillStyle = css("--utt");
    g.fillRect(xOf(u.start_ms, w), LANES.agent[0], xOf(u.end_ms, w) - xOf(u.start_ms, w), LANES.agent[1]);
    g.fillStyle = css("--muted"); g.fillText(u.utt_id, xOf(u.start_ms, w) + 3, LANES.agent[0] + 2);
  }
  drawWave(g, S.audio.agent, LANES.agent, css("--agent"), w);
  drawWave(g, S.audio.caller, LANES.caller, css("--caller"), w);
  // ground truth lane
  const gt = S.timeline.ground_truth, [ly, lh] = LANES.labels;
  g.fillStyle = css("--muted-fill");
  for (const s of gt.user_segments) g.fillRect(xOf(s.start_ms, w), ly + lh - 8, Math.max(1, xOf(s.end_ms, w) - xOf(s.start_ms, w)), 6);
  for (const ev of gt.events) {
    const key = ev.expected === "STOP" ? "stop" : ev.expected === "KEEP" ? "keep" : "either";
    const a = xOf(ev.onset_ms, w), b = xOf(ev.offset_ms ?? ev.turn_end_ms ?? ev.onset_ms + 200, w);
    g.fillStyle = css(`--${key}-soft`); g.fillRect(a, ly, Math.max(3, b - a), lh - 12);
    g.strokeStyle = css(`--${key}`); g.lineWidth = 1; g.strokeRect(a + .5, ly + .5, Math.max(3, b - a) - 1, lh - 13);
    if (ev.evident_ms !== undefined) { g.fillStyle = css(`--${key}`); g.fillRect(xOf(ev.evident_ms, w), ly, 2, lh - 12); }
    g.fillStyle = css(`--${key}`); g.fillText(ev.kind === "interruption" ? ev.expected : "EOT", a + 3, ly + 3);
  }
  // lane titles
  g.fillStyle = css("--text"); g.font = "600 11px system-ui, sans-serif";
  g.fillText("Agent", 6, LANES.agent[0] + 4); g.fillText("Caller", 6, LANES.caller[0] + 4); g.fillText("Labels", 6, ly + 4);
  g.fillStyle = css("--muted"); g.font = "10px system-ui, sans-serif";
  g.fillText("channel 0", 6, LANES.agent[0] + 18); g.fillText("channel 1", 6, LANES.caller[0] + 18); g.fillText("not given", 6, ly + 18);
  g.fillText("to detector", 6, ly + 29);
  S.staticLayer = off;
}

function drawWave(g, data, [y, hh], color, w) {
  const x0 = LANES.left, cols = Math.floor(w - LANES.left - LANES.right), per = data.length / cols, mid = y + hh / 2;
  g.fillStyle = color;
  for (let c = 0; c < cols; c++) {
    let lo = 0, hi = 0;
    const a = Math.floor(c * per), b = Math.min(data.length, Math.floor((c + 1) * per));
    for (let i = a; i < b; i++) { const v = data[i]; if (v < lo) lo = v; else if (v > hi) hi = v; }
    const amp = (hh / 2 - 2) * 2.5;                       // speech peaks near -8 dBFS; scale for visibility
    const top = mid - Math.min(hh / 2 - 1, hi * amp), bot = mid - Math.max(-(hh / 2 - 1), lo * amp);
    g.fillRect(x0 + c, top, 1, Math.max(1, bot - top));
  }
}

function drawTimeline(t) {
  const { cv, w, h, dpr } = canvasSize();
  const g = cv.getContext("2d");
  if (cv.width !== Math.round(w * dpr)) { buildStaticLayer(); }
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.clearRect(0, 0, cv.width, cv.height);
  if (!S.staticLayer) return;
  g.drawImage(S.staticLayer, 0, 0);
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const top = LANES.agent[0], bottom = LANES.caller[0] + LANES.caller[1];
  // agent muted spans (from each real STOP to the end of its utterance, or a RESUME)
  for (const u of S.timeline.protocol.agent_utterances) {
    const ds = S.decisions.filter((d) => d.utt_id === u.utt_id);
    let from = null;
    for (const d of ds) {
      if (d.decision === "STOP" && from === null) from = d.stream_ms;
      else if (d.decision === "RESUME" && from !== null) { shade(g, from, d.stream_ms, w); from = null; }
    }
    if (from !== null) shade(g, from, u.end_ms, w);
  }
  // decisions
  g.font = "700 11px system-ui, sans-serif"; g.textBaseline = "top";
  for (const d of S.decisions) {
    const x = xOf(d.stream_ms, w);
    if (d.decision === "STOP") {
      g.fillStyle = css("--stop"); g.fillRect(x - 1, top, 3, bottom - top);
      g.fillText(`STOP ${sec(d.stream_ms)}s`, Math.min(x + 4, w - 90), LANES.caller[0] + LANES.caller[1] - 14);
    } else {
      g.fillStyle = css("--muted"); g.fillRect(x, LANES.caller[0], 1, LANES.caller[1]);
    }
  }
  // playhead
  if (S.started) {
    g.fillStyle = css("--playhead");
    g.fillRect(xOf(t, w) - 1, LANES.axis, 2, h - LANES.axis);
  }
}

function shade(g, a, b, w) {
  g.fillStyle = css("--muted-fill");
  g.fillRect(xOf(a, w), LANES.agent[0], Math.max(2, xOf(b, w) - xOf(a, w)), LANES.agent[1]);
}

// ------------------------------------------------------------------------------------------------ start-up
async function init() {
  try {
    S.config = await api("/api/config");
  } catch (e) { showError(`Cannot reach the demo server: ${e.message}`); return; }
  for (const m of S.config.models) S.models[m.id] = m;
  const msel = $("model");
  msel.innerHTML = S.config.models.map((m) => `<option value="${esc(m.id)}" ${m.available ? "" : "disabled"}>` +
    `${esc(m.name)} · ${esc(m.revision || "?")}${m.dirty ? " (uncommitted)" : ""}${m.available ? "" : ` (unavailable: ${esc(m.error)})`}</option>`).join("");
  S.model = (S.config.models.find((m) => m.available) || {}).id;
  msel.value = S.model;
  const groups = {};
  for (const s of S.config.scenarios) (groups[s.category] ||= []).push(s);
  $("scenario").innerHTML = Object.entries(groups).map(([cat, list]) => `<optgroup label="${esc(cat)} · ${esc(list[0].category_name)}">` +
    list.map((s) => `<option value="${esc(s.id)}">${esc(s.id)} · expects ${esc(s.expected.join(", "))} · ${esc(s.acoustic.join("+"))} · ${sec(s.duration_ms)} s</option>`).join("") +
    "</optgroup>").join("");
  renderModelChip(); renderBench();
  $("play").onclick = () => (S.playing ? pause() : play());
  $("reset").onclick = () => reset();
  $("scenario").onchange = (e) => loadScenario(e.target.value);
  msel.onchange = (e) => changeModel(e.target.value);
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !["SELECT", "BUTTON"].includes(document.activeElement.tagName)) { e.preventDefault(); $("play").click(); }
  });
  window.addEventListener("resize", () => buildStaticLayer());
  if (!S.model) { showError("No detector version could be loaded; see the server console."); return; }
  const first = S.config.scenarios.find((s) => s.category === "GI") || S.config.scenarios[0];
  $("scenario").value = first.id;
  await loadScenario(first.id);
  requestAnimationFrame(frame);
}

// Read-only snapshot for automated browser checks.
window.sarjDemo = {
  snapshot: () => ({
    scenario: S.scenId, model: S.model, session: S.session, started: S.started, playing: S.playing, ended: S.ended,
    playheadMs: playhead(), fedMs: S.fedMs, done: S.done, agentMuted: S.agentMuted,
    agentGain: S.nodes ? S.nodes.agent.gain.gain.value : null, callerGain: S.nodes ? S.nodes.caller.gain.gain.value : null,
    ctxState: S.ctx ? S.ctx.state : null, t0: S.t0,
    sources: S.sources ? Object.keys(S.sources) : [],
    decisions: S.decisions.map((d) => ({ stream_ms: d.stream_ms, decision: d.decision, utt_id: d.utt_id, pageDelayMs: d.pageDelayMs })),
    events: S.score ? S.score.events : [], protectedResult: S.score ? S.score.protected || null : null,
    status: $("status").textContent,
  }),
};

init();
