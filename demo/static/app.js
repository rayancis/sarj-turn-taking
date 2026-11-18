"use strict";
// SARJ demo page: Baseline vs P3 on the same audio.
// The audio clock (one AudioContext for both channels) is the master: its playhead decides which 20 ms frames the
// server feeds to BOTH real detectors (each in its own session; plus a small look-ahead on the hosted site, to absorb
// network delay). Decisions come back from the server and are applied when playback reaches the frame time at which
// the detector issued them. The selected detector's STOP silences the agent you hear. Nothing is pre-scripted.

const $ = (id) => document.getElementById(id);
const DETS = ["baseline", "p3"];                   // display order: left, right
const NAMES = { baseline: "Baseline", p3: "P3" };

const freshDet = () => ({ session: null, fedMs: 0, done: false, decisions: [], pending: [], score: null,
  meanOnFrameUs: null, streamEndMs: 0 });

const S = {
  config: null, models: {}, listen: "p3", scenId: null, timeline: null, audio: null,
  ctx: null, nodes: null, sources: null, t0: 0, started: false, playing: false, ended: false,
  gen: 0, loopGen: null, busy: false, staticLayer: null, verdictKey: "", agentMuted: false,
  det: { baseline: freshDet(), p3: freshDet() }, rtts: [], lookahead: 0, pollMs: 15,
};

// ------------------------------------------------------------------------------------------------ helpers
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sec = (ms) => (ms / 1000).toFixed(2);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const scenMeta = () => (S.own && S.own.id === S.scenId ? S.own : S.config.scenarios.find((x) => x.id === S.scenId));
const collection = (key) => (S.config.collections || []).find((c) => c.key === key) || {};

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opts);
  const isJson = (r.headers.get("Content-Type") || "").includes("json");
  const data = isJson ? await r.json() : await r.arrayBuffer();
  if (!r.ok) {
    const err = new Error((isJson && data.error) || `${r.status} ${r.statusText}`);
    err.status = r.status;
    throw err;
  }
  return data;
}

function closeSession(id) {
  // Free the server-side detector of a playback we abandon (best effort; the server also expires idle sessions).
  if (!id) return;
  try { navigator.sendBeacon(`/api/session/${id}/close`, "{}"); } catch (_) { /* ignore */ }
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
  // iOS: play through the silent switch, like a media player (Safari 16.4+; ignored elsewhere).
  try { if (navigator.audioSession) navigator.audioSession.type = "playback"; } catch (_) { /* ignore */ }
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
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
  let b;
  try {
    b = S.ctx.createBuffer(1, samples.length, rate);
  } catch (_) {
    // Some older browsers refuse 16 kHz buffers: resample (linear) to the device rate instead.
    const out = S.ctx.sampleRate, n = Math.floor(samples.length * out / rate), y = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const x = i * rate / out, k = Math.floor(x), f = x - k;
      y[i] = (samples[k] || 0) * (1 - f) + (samples[k + 1] || 0) * f;
    }
    b = S.ctx.createBuffer(1, n, out);
    samples = y;
  }
  b.getChannelData(0).set(samples);
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

// ------------------------------------------------------------------------------------------------ detector sessions
async function newSessions() {
  const gen = ++S.gen;
  stopSources();
  for (const id of DETS) closeSession(S.det[id].session);
  Object.assign(S, { started: false, playing: false, ended: false, agentMuted: false, rtts: [], verdictKey: "",
    det: { baseline: freshDet(), p3: freshDet() } });
  if (S.nodes) S.nodes.agent.gain.gain.value = 1;
  $("log").innerHTML = "";
  renderEvents(); renderTiming(); updatePlayButton();
  const rs = await Promise.all(DETS.map((id) => api("/api/session", { scenario: S.scenId, model: id })));
  if (gen !== S.gen) { rs.forEach((r) => closeSession(r.session_id)); return; }
  DETS.forEach((id, i) => { S.det[id].session = rs[i].session_id; S.det[id].streamEndMs = rs[i].stream_end_ms; });
  addLog(0, "info", `Fresh detectors: ${rs.map((r) => `${esc(r.model.name)} @ ${esc(r.model.revision || "?")}`).join(" and ")}`, "ctl");
  updatePlayButton();
}

async function advanceOne(gen, id, toMs) {
  const d = S.det[id];
  const t0 = performance.now();
  const r = await api(`/api/session/${d.session}/advance`, { to_ms: toMs });
  if (gen !== S.gen) return;                       // stale response from a previous scenario/reset
  S.rtts.push(performance.now() - t0);
  if (S.rtts.length > 200) S.rtts.shift();
  const receivedAt = playhead();
  Object.assign(d, { fedMs: r.fed_ms, done: r.done, score: r.score, meanOnFrameUs: r.mean_on_frame_us });
  // Hold everything until playback reaches its stream time (immediately, when there is no look-ahead).
  if (id === "p3") for (const p of r.protocol) d.pending.push({ at: p.stream_ms, kind: "ctl", item: p });
  for (const x of r.decisions) {
    x.det = id;
    x.earlyMs = x.stream_ms - receivedAt;           // > 0: arrived before playback needed it
    d.pending.push({ at: x.stream_ms, kind: "dec", item: x });
  }
  if (r.errors.length) showError(`${NAMES[id]}: ${r.errors.map((e) => `${sec(e.stream_ms)} s ${e.error}`).join("; ")}`);
}

async function advanceAll(gen, toMs) {
  await Promise.all(DETS.filter((id) => !S.det[id].done).map((id) => advanceOne(gen, id, toMs)));
  if (gen !== S.gen) return;
  reveal(S.ended ? Infinity : playhead());
  renderTiming();
}

function reveal(t) {
  // Apply detector output whose stream time playback has reached, in stream order.
  let changed = false;
  for (const id of DETS) {
    const d = S.det[id];
    while (d.pending.length && d.pending[0].at <= t) {
      const { kind, item } = d.pending.shift();
      if (kind === "ctl") { logControl(item); continue; }
      item.pageDelayMs = S.ended && t === Infinity ? null : Math.max(0, playhead() - item.stream_ms);
      d.decisions.push(item);
      logDecision(item);
      changed = true;
    }
  }
  if (changed) { updateMute(); renderTiming(); renderEvents(); }
}

const fedMs = () => Math.min(...DETS.map((id) => S.det[id].fedMs));
const allDone = () => DETS.every((id) => S.det[id].done);

async function feedLoop(gen) {
  if (S.loopGen === gen) return;                   // one loop per playback: requests stay strictly ordered
  S.loopGen = gen;
  try { await feedTicks(gen); } finally { if (S.loopGen === gen) S.loopGen = null; }
}

async function feedTicks(gen) {
  // Each tick sends the playhead (plus look-ahead); the server feeds every frame whose start time it has reached.
  // While paused the playhead does not move and nothing is fed.
  while (gen === S.gen && S.playing) {
    const t = playhead();
    try {
      if (t >= S.timeline.duration_ms) { await finish(gen); return; }
      if (t + S.lookahead >= fedMs() && !allDone()) await advanceAll(gen, t + S.lookahead);
    } catch (e) {
      if (gen !== S.gen) return;
      if (e.status === 429) { await sleep(500); continue; }      // rate limited: just try again shortly
      const msg = e.status === 404 ? "This playback expired on the server (idle too long). Press Reset."
        : e.status === 503 ? e.message : `Server error: ${e.message}. Press Reset.`;
      showError(msg); await pause();
      return;
    }
    await sleep(S.pollMs);
  }
}

async function finish(gen) {
  reveal(S.timeline.duration_ms);
  S.ended = true;
  await advanceAll(gen, Math.max(...DETS.map((id) => S.det[id].streamEndMs)));   // the harness also streams a silent tail
  if (gen !== S.gen) return;
  reveal(Infinity);
  S.playing = false;
  stopSources();
  if (S.ctx.state === "running") await S.ctx.suspend();
  updatePlayButton();
  renderEvents();
}

// ------------------------------------------------------------------------------------------------ controls
async function play() {
  if (S.busy || !DETS.every((id) => S.det[id].session)) return;
  ensureContext();
  const resumed = S.ctx.resume();                 // first, inside the click: iOS only unlocks audio in a user gesture
  if (S.ended) { await reset(); }
  await resumed;
  if (S.ctx.state !== "running") await S.ctx.resume();
  if (!S.started) {
    // Over a network, fill the look-ahead buffer once before the audio starts, so the detectors never trail it.
    if (S.lookahead && fedMs() < S.lookahead) {
      const gen = S.gen;
      S.busy = true; $("play").textContent = "Starting…"; $("play").disabled = true;
      try { await advanceAll(gen, S.lookahead); } catch (e) { showError(`Server error: ${e.message}. Press Reset.`); }
      finally { S.busy = false; }
      if (gen !== S.gen) { updatePlayButton(); return; }
    }
    startSources(); S.started = true;
  }
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
  await guarded(async () => { await newSessions(); });
}

async function guarded(fn) {
  S.busy = true; updatePlayButton();
  try { showError(""); await fn(); } catch (e) { showError(e.message); } finally { S.busy = false; updatePlayButton(); }
}

function updatePlayButton() {
  const b = $("play"), ready = DETS.every((id) => S.det[id].session);
  b.textContent = S.playing ? "Pause" : (S.ended ? "Replay" : (S.started ? "Resume" : "Play"));
  b.disabled = S.busy || !ready;
  $("reset").disabled = S.busy || !ready;
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
    renderScenarioCard();
    buildStaticLayer();
    await newSessions();
  });
}

function changeListen(id) {
  // Both detectors keep running: this only chooses whose STOP silences the agent you hear.
  S.listen = id;
  renderModelChip();
  S.agentMuted = null;                            // unknown: the next updateMute() always applies the new detector's state
  updateMute();
  renderEvents();
}

// ------------------------------------------------------------------------------------------------ live state
function stopStateAt(id, t) {
  // Agent behaviour implied by a detector's real decisions: after a STOP the agent stays silent until the end of
  // that utterance (or a RESUME), as the official scorer reconstructs it.
  const utts = S.timeline ? S.timeline.protocol.agent_utterances : [];
  const u = utts.find((x) => x.start_ms <= t && t < x.end_ms);
  if (!u) return { utt: null, muted: false, stopAt: null };
  let stopAt = null;
  for (const d of S.det[id].decisions) {
    if (d.utt_id !== u.utt_id || d.stream_ms > t) continue;
    if (d.decision === "STOP" && stopAt === null) stopAt = d.stream_ms;
    else if (d.decision === "RESUME") stopAt = null;
  }
  return { utt: u, muted: stopAt !== null, stopAt };
}

function updateMute() {
  if (!S.ctx || !S.started) return;
  const st = stopStateAt(S.listen, playhead());
  if (st.muted !== S.agentMuted) {
    S.agentMuted = st.muted;
    const g = S.nodes.agent.gain.gain;
    g.cancelScheduledValues(S.ctx.currentTime);
    g.setTargetAtTime(st.muted ? 0 : 1, S.ctx.currentTime, 0.004);
  }
}

function uttStop(id, u) {
  const d = S.det[id].decisions.find((x) => x.utt_id === u.utt_id && x.decision === "STOP");
  return d ? d.stream_ms : null;
}

function detStatus(id, t) {
  // One detector's live state, in words, for its card.
  const utts = S.timeline ? S.timeline.protocol.agent_utterances : [];
  const stops = S.det[id].decisions.filter((d) => d.decision === "STOP");
  if (!S.timeline) return ["idle", "Loading…"];
  if (S.ended) return stops.length ? ["stopped", `Stopped the agent ${stops.length}× (${stops.map((d) => `${sec(d.stream_ms)} s`).join(", ")})`]
    : ["kept", "Never stopped the agent"];
  if (!S.started) return ["idle", "Ready"];
  const st = stopStateAt(id, t);
  if (st.utt && st.muted) return ["stopped", `STOP at ${sec(st.stopAt)} s: agent silent for the rest of ${st.utt.utt_id}`];
  if (st.utt) return ["speaking", `Agent speaking (${st.utt.utt_id}): no STOP`];
  const prev = utts.filter((u) => u.end_ms <= t).pop();
  if (!prev) return ["idle", "Agent not speaking yet"];
  return uttStop(id, prev) !== null ? ["stopped", `${prev.utt_id}: stopped at ${sec(uttStop(id, prev))} s`]
    : ["kept", `${prev.utt_id}: spoke to the end, no STOP`];
}

function renderStatus(t) {
  for (const id of DETS) {
    const [cls, msg] = detStatus(id, t);
    $(`card-${id}`).className = `det-card ${cls}${id === S.listen ? " listening" : ""}`;
    $(`st-${id}`).textContent = (S.started && !S.playing && !S.ended ? "Paused · " : "") + msg;
  }
  const utts = S.timeline ? S.timeline.protocol.agent_utterances : [];
  const st = stopStateAt(S.listen, t);
  const cur = st.utt || utts.find((u) => u.start_ms > t);
  $("utt-text").textContent = cur ? `Agent text ${cur.utt_id}${st.utt ? "" : " (next)"}: ${cur.text}` : "";
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
  if (S.playing) { reveal(playhead()); updateMute(); }
  const key = visibleVerdictKey();
  if (key !== S.verdictKey) { S.verdictKey = key; renderEvents(); }
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
  if (p.type === "agent_utterance_start") addLog(p.stream_ms, "→ detectors", `agent_utterance_start ${esc(p.utt_id)} (${sec(p.duration_ms)} s, with text)`, "ctl");
  else if (p.type === "agent_utterance_end") addLog(p.stream_ms, "→ detectors", `agent_utterance_end ${esc(p.utt_id)}`, "ctl");
  else if (p.type === "scenario_start") addLog(p.stream_ms, "→ detectors", "scenario_start", "ctl");
  else if (p.type === "scenario_end") addLog(p.stream_ms, "→ detectors", "scenario_end", "ctl");
}

function logDecision(d) {
  const pill = `<span class="pill ${d.decision === "STOP" ? "STOP" : "other"}">${esc(d.decision)}</span> <span class="who ${d.det}">${NAMES[d.det]}</span>`;
  const where = d.utt_id ? `during ${esc(d.utt_id)}` : "agent not speaking";
  const extra = d.decision === "TAKE_TURN" ? " · end-of-turn track (separate)" : "";
  const delay = d.pageDelayMs === null || d.pageDelayMs === undefined ? "" : ` · shown ${Math.round(d.pageDelayMs)} ms after its frame`;
  addLog(d.stream_ms, pill, `${where} · frame ${d.frame} · on_frame ${d.on_frame_us} µs${delay}${extra}`, "dec");
}

function modelLabel(id) {
  const m = S.models[id];
  return m ? `${m.name} @ ${m.revision || "?"}` : "?";
}

function renderModelChip() {
  $("model-chip").textContent = `Hearing: ${modelLabel(S.listen)}`;
  $("m-agent-label").textContent = `Agent output (what the caller hears, with ${NAMES[S.listen]})`;
}

function renderTiming() {
  const rows = [];
  for (const id of DETS) {
    const d = S.det[id];
    const delays = d.decisions.map((x) => x.pageDelayMs).filter((x) => x !== null && x !== undefined).sort((a, b) => a - b);
    rows.push([`${NAMES[id]} compute`, d.meanOnFrameUs === null ? "–" : `${d.meanOnFrameUs} µs mean per 20 ms frame`]);
    if (delays.length) rows.push([`${NAMES[id]} decisions shown`, `median ${Math.round(delays[Math.floor(delays.length / 2)])} ms after their frame time`]);
  }
  rows.push(["Frames fed", `${Math.round(fedMs() / 20)} (${sec(fedMs())} s of stream)`]);
  if (S.lookahead) {
    const r = [...S.rtts].sort((a, b) => a - b);
    rows.push(["Page ↔ server round trip", r.length ? `median ${Math.round(r[Math.floor(r.length / 2)])} ms (this connection)` : "–"]);
    rows.push(["Look-ahead", `detectors may run up to ${S.lookahead} ms ahead of what you hear`]);
  }
  $("timing").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
}

function renderScenarioCard() {
  const s = scenMeta(), col = collection(s.collection);
  const own = s.collection === "custom";
  $("scen-title").textContent = own ? s.title : `${s.id} · ${s.title}`;
  $("scen-collection").textContent = own ? "your audio · not stored" : col.public ? `${col.title}` : `${col.title || s.collection} · private, team use only`;
  $("scen-collection").className = `tag ${own || col.public ? "gt" : "private"}`;
  $("scen-explanation").textContent = s.explanation;
  const cr = S.timeline.credits || [];
  $("scen-credits").textContent = cr.length ? `Audio: ${cr.map((c) => `${c.attribution} (${c.license})`).join(" · ")}` : "";
  const gt = S.timeline.ground_truth;
  $("scenario-info").innerHTML = own ? "Your own audio: no ground-truth labels, so there is no official verdict. Compare the two detectors' decisions."
    : `<b>${esc(s.id)}</b> · ${esc(s.category_name)} · ${esc(gt.subtype)} · ${esc(s.acoustic.join(" + "))} · labels: ${esc(gt.label_status)}`;
  if (!own) {                                       // default start of an uploaded caller file: 1 s into the agent's longest turn
    const u = [...S.timeline.protocol.agent_utterances].sort((a, b) => (b.end_ms - b.start_ms) - (a.end_ms - a.start_ms))[0];
    if (u) $("offset").value = (Math.round((u.start_ms + 1000) / 500) * 0.5).toFixed(1);
  }
}

// ------------------------------------------------------------------------------------------------ test your own audio
// The visitor's audio becomes the caller channel; the agent channel and agent timing come from the selected scenario.
// Audio is converted here to 16 kHz mono 16-bit (what the detectors receive) and sent once; the server keeps it in
// memory only and drops it within custom.ttl_s.
const OWN = { recording: null };

function ownStatus(msg, bad) {
  const el = $("own-status");
  el.textContent = msg || "";
  el.className = `own-status${bad ? " bad" : ""}`;
}

function setOwnBusy(busy) {
  $("rec").disabled = busy && !OWN.recording;
  $("upload").disabled = busy;
  $("upload-label").classList.toggle("disabled", busy);
}

async function to16kMono(channels, rate) {
  const n = channels[0].length, mono = new Float32Array(n);
  for (const ch of channels) for (let i = 0; i < n; i++) mono[i] += ch[i] / channels.length;
  if (rate === 16000) return mono;
  const len = Math.max(1, Math.round(n * 16000 / rate));
  try {                                             // the browser's own resampler (band-limited)
    const off = new OfflineAudioContext(1, len, 16000);
    const b = off.createBuffer(1, n, rate);
    b.getChannelData(0).set(mono);
    const src = off.createBufferSource();
    src.buffer = b;
    src.connect(off.destination);
    src.start();
    return (await off.startRendering()).getChannelData(0).slice();
  } catch (_) {                                     // fallback: average over the source span, then linear interpolation
    const out = new Float32Array(len), step = rate / 16000, half = Math.max(0, Math.floor(step / 2));
    for (let i = 0; i < len; i++) {
      const c = i * step;
      let acc = 0, cnt = 0;
      for (let k = Math.floor(c) - half; k <= Math.floor(c) + half; k++) if (k >= 0 && k < n) { acc += mono[k]; cnt++; }
      out[i] = cnt ? acc / cnt : 0;
    }
    return out;
  }
}

async function submitOwnAudio(samples16k, offsetMs, how) {
  const max = (S.config.custom && S.config.custom.max_s) || 60;
  let x = samples16k;
  if (x.length > max * 16000) { x = x.subarray(0, max * 16000); how += `, first ${max} s`; }
  if (x.length < 3200) throw new Error("The audio is too short (less than 0.2 s).");
  const pcm = new Int16Array(x.length);
  for (let i = 0; i < x.length; i++) pcm[i] = Math.max(-32768, Math.min(32767, Math.round(x[i] * 32767)));
  const agent = S.own && S.own.id === S.scenId ? S.own.agent_from : S.scenId;
  ownStatus("Sending your audio to the detectors…");
  const r = await fetch(`/api/custom?agent=${encodeURIComponent(agent)}&offset_ms=${Math.round(offsetMs)}`,
    { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: pcm.buffer });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
  const sec1 = (ms) => (ms / 1000).toFixed(1);
  S.own = { id: data.scenario_id, collection: "custom", agent_from: data.agent_from, acoustic: [], category_name: "Your own audio",
    title: `Your audio with the agent of ${data.agent_from}`, expected: [],
    explanation: `${how}: ${sec1(data.caller_ms)} s of your audio as the caller, starting at ${sec1(data.offset_ms)} s; the agent is the ` +
      `original agent of ${data.agent_from}. No labels: compare what Baseline and P3 decide. Not stored; deleted within ` +
      `${Math.round(((S.config.custom && S.config.custom.ttl_s) || 900) / 60)} minutes.` };
  let opt = $("scenario").querySelector("option[data-own]");
  if (!opt) {
    const g = document.createElement("optgroup");
    g.label = "Your audio";
    opt = document.createElement("option");
    opt.dataset.own = "1";
    g.appendChild(opt);
    $("scenario").prepend(g);
  }
  opt.value = S.own.id;
  opt.textContent = `Your audio · agent of ${data.agent_from}`;
  $("scenario").value = S.own.id;
  await loadScenario(S.own.id);
  ownStatus("Ready: press Play to hear your audio with the agent and see both detectors' decisions.");
}

async function uploadOwn(file) {
  if (!file) return;
  setOwnBusy(true);
  try {
    ensureContext();
    ownStatus(`Reading ${file.name}…`);
    const decoded = await S.ctx.decodeAudioData(await file.arrayBuffer());
    const chans = [...Array(decoded.numberOfChannels).keys()].map((c) => decoded.getChannelData(c));
    const x = await to16kMono(chans, decoded.sampleRate);
    await submitOwnAudio(x, Number($("offset").value || 0) * 1000, `Uploaded file`);
  } catch (e) {
    ownStatus(`Could not use this file: ${e.message}`, true);
  } finally {
    $("upload").value = "";
    setOwnBusy(false);
  }
}

async function toggleRecording() {
  if (OWN.recording) { OWN.recording.stop(); return; }
  setOwnBusy(true);
  ensureContext();
  const resumed = S.ctx.resume();                   // inside the click (iOS)
  let stream = null;
  try {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error("this browser cannot record audio here");
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: false, autoGainControl: false } });
    await resumed;
    if (S.playing) await pause();
    stopSources();
    if (S.ctx.state !== "running") await S.ctx.resume();
    if (!OWN.moduleLoaded) { await S.ctx.audioWorklet.addModule("recorder.js"); OWN.moduleLoaded = true; }
    const base = S.own && S.own.id === S.scenId ? null : S.audio;  // the agent we play while recording
    if (!base) throw new Error("select one of the 24 scenarios first: its agent is played while you record");
    const ctx = S.ctx, mic = ctx.createMediaStreamSource(stream), node = new AudioWorkletNode(ctx, "pcm-capture");
    const sink = ctx.createGain();
    sink.gain.value = 0;
    mic.connect(node).connect(sink).connect(ctx.destination);
    const agentSrc = ctx.createBufferSource();
    agentSrc.buffer = base.buffers.agent;
    agentSrc.connect(ctx.destination);
    const parts = [];
    const finished = new Promise((resolve) => {
      node.port.onmessage = (e) => { if (e.data.chunk) parts.push(e.data.chunk); if (e.data.done) resolve(); };
    });
    const startAt = ctx.currentTime + 0.2;
    node.port.postMessage({ startAt });
    agentSrc.start(startAt);
    const max = (S.config.custom && S.config.custom.max_s) || 60;
    const track = stream.getAudioTracks()[0];
    // what you hear lags the agent by the output latency, and the microphone adds its own: remove both
    const lag = Math.min(0.5, Math.max(0, (ctx.outputLatency || ctx.baseLatency || 0) + ((track.getSettings && track.getSettings().latency) || 0)));
    let stopped = false;
    const stop = () => { if (stopped) return; stopped = true; try { agentSrc.stop(); } catch (_) { /* ended */ } node.port.postMessage({ stop: true }); };
    agentSrc.onended = stop;
    const timer = setTimeout(stop, Math.min(max, base.buffers.agent.duration + 1) * 1000 + 300);
    OWN.recording = { stop };
    $("rec").textContent = "Stop recording";
    $("rec").classList.add("recording");
    ownStatus(`Recording… speak as the caller (the agent is playing). Stops by itself at the end of the agent's audio, or press Stop.`);
    await finished;
    clearTimeout(timer);
    mic.disconnect(); node.disconnect(); sink.disconnect();
    stream.getTracks().forEach((t) => t.stop());
    stream = null;
    OWN.recording = null;
    $("rec").textContent = "Record while the agent speaks";
    $("rec").classList.remove("recording");
    const total = parts.reduce((a, p) => a + p.length, 0), all = new Float32Array(total);
    let o = 0;
    for (const p of parts) { all.set(p, o); o += p.length; }
    const drop = Math.round(lag * ctx.sampleRate);
    const x = await to16kMono([all.subarray(Math.min(drop, all.length))], ctx.sampleRate);
    await submitOwnAudio(x, 0, "Recorded with the agent");
  } catch (e) {
    ownStatus(e && e.name === "NotAllowedError" ? "Microphone permission was refused." : `Recording failed: ${e.message}`, true);
  } finally {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    OWN.recording = null;
    $("rec").textContent = "Record while the agent speaks";
    $("rec").classList.remove("recording");
    setOwnBusy(false);
  }
}

function visibleVerdictKey() {
  // Changes whenever another verdict may be shown (playback passed a close time) or the call ended.
  const ph = playhead();
  return `${S.ended}|${S.listen}|` + DETS.map((id) => (S.det[id].score ? S.det[id].score.events.map((r) => (r.closed && (S.ended || ph >= r.close_ms) ? 1 : 0)).join("") : "")).join("/");
}

function shown(r) {
  return r && r.closed && (S.ended || playhead() >= r.close_ms);
}

function resultCell(ev, r) {
  // Shown only once playback has passed the point after which no later decision can change it.
  if (!shown(r)) return `<span class="pending">pending${r && r.close_ms ? ` · by ${sec(r.close_ms)} s` : ""}</span>`;
  const c = r.credit ?? 0;
  const cls = c >= 1 ? "ok" : c > 0 ? "part" : "bad";
  const mark = c >= 1 ? "✓" : c > 0 ? "◐" : "✗";
  let text;
  if (ev.kind !== "interruption") text = `end-of-turn track: ${r.detail}`;
  else if (r.outcome === "yield") text = `stopped${r.latency_ms !== null && r.latency_ms !== undefined ? `, ${Math.round(r.latency_ms)} ms after intent` : ""}`;
  else if (r.outcome === "miss") text = "missed: did not stop in time";
  else if (r.outcome === "kept") text = "kept speaking";
  else if (r.outcome === "false_stop") text = `false stop (${r.detail.replace(/^false stop at /, "at ")})`;
  else text = r.detail || r.outcome;
  return `<span class="${cls}">${mark} ${esc(text)}</span>`;
}

function headline() {
  // The one-sentence comparison, from the official scorer's per-event outcomes (only events already decided).
  if (!S.timeline) return null;
  const evs = S.timeline.ground_truth.events;
  const out = [];
  evs.forEach((ev, i) => {
    const b = S.det.baseline.score && S.det.baseline.score.events[i], p = S.det.p3.score && S.det.p3.score.events[i];
    if (!shown(b) || !shown(p) || ev.kind !== "interruption") return;
    const ok = (r) => (r.credit ?? 0) >= 1;
    if (ev.expected === "KEEP") {
      if (!ok(b) && ok(p)) out.push(["good", "Baseline stopped falsely; P3 correctly kept talking."]);
      else if (ok(b) && ok(p)) out.push(["good", "Both correctly kept talking."]);
      else if (!ok(b) && !ok(p)) out.push(["bad", "Both stopped falsely: the agent should have kept talking."]);
      else out.push(["bad", "P3 stopped falsely; Baseline kept talking."]);
    } else if (ev.expected === "STOP") {
      const pt = p.outcome === "yield" ? `P3 detected it ${Math.round(p.latency_ms || 0)} ms after the intent was evident` : "P3 missed it";
      const bt = b.outcome === "yield" ? `Baseline ${Math.round(b.latency_ms || 0)} ms` : "Baseline missed it";
      out.push([p.outcome === "yield" ? "good" : "bad", `Genuine interruption: ${pt} (${bt}).`]);
    } else {
      out.push(["neutral", `Ambiguous event: Baseline credit ${b.credit}, P3 credit ${p.credit}.`]);
    }
  });
  return out.length ? out : null;
}

function renderEvents() {
  if (!S.timeline) return;
  const gt = S.timeline.ground_truth;
  const segs = Object.fromEntries(gt.user_segments.map((s) => [s.seg_id, s]));
  $("events").innerHTML = gt.events.map((ev, i) => {
    const words = (ev.segment_ids || []).map((id) => segs[id] && (segs[id].text || `[${segs[id].delivery || segs[id].kind}]`)).filter(Boolean).join(" … ");
    const exp = ev.expected === "EITHER" ? `EITHER<br><small>prefers ${esc(ev.preferred || "-")}</small>` : esc(ev.expected);
    const when = `${sec(ev.onset_ms)}–${sec(ev.offset_ms ?? ev.turn_end_ms ?? ev.onset_ms)} s`;
    const cell = (id) => resultCell(ev, S.det[id].score ? S.det[id].score.events[i] : null);
    return `<tr><td>${esc(ev.event_id.split(".").pop())}<br><small>${esc(ev.kind)}</small></td>` +
      `<td><span class="pill ${esc(ev.expected)}">${exp}</span></td>` +
      `<td><span dir="rtl" lang="ar">${esc(words || "–")}</span><br><small>${when}</small></td>` +
      `<td>${cell("baseline")}</td><td>${cell("p3")}</td></tr>`;
  }).join("") || `<tr><td colspan="5" class="pending">No labels for your own audio: compare the two detectors' decisions in the timeline and the log.</td></tr>`;
  const parts = DETS.map((id) => {
    const p = S.ended && gt.events.length && S.det[id].score && S.det[id].score.protected;
    return p ? `${NAMES[id]}: ${p.spurious_stops.length ? p.spurious_stops.map((x) => `${sec(x)} s`).join(", ") : "none"}` : null;
  }).filter(Boolean);
  $("protected").innerHTML = parts.length ? `Stops outside labelled events (they lower Restraint): ${esc(parts.join(" · "))}.` : "";
  const h = headline(), el = $("headline");
  el.hidden = !h;
  if (h) {
    el.className = `headline ${h.some((x) => x[0] === "bad") ? "bad" : "good"}`;
    el.innerHTML = h.map(([cls, txt]) => `<div class="${cls}">${cls === "good" ? "✓" : cls === "bad" ? "✗" : "•"} ${esc(txt)}</div>`).join("");
  }
}

function renderBench() {
  const b = S.config.benchmark;
  $("bench-panel").hidden = !b;
  if (!b) return;
  const row = (m) => b.rows.find((r) => r.model === m) || {};
  const B = row("baseline"), P = row("p3");
  const f = (x, d) => (x === null || x === undefined ? "–" : Number(x).toFixed(d));
  const lines = [["Official dev Core", f(B.core, 2), f(P.core, 2)], ["Responsiveness", f(B.R, 3), f(P.R, 3)],
    ["Restraint", f(B.S, 3), f(P.S, 3)], ["False-stop scoring events", B.false_stops, P.false_stops],
    ["Missed genuine interruptions", B.misses, P.misses]];
  $("bench").innerHTML = lines.map(([k, x, y]) => `<tr><td>${k}</td><td class="num">${esc(x)}</td><td class="num"><b>${esc(y)}</b></td></tr>`).join("");
  $("bench-note").textContent = `Source: ${b.source}; ${b.scope}. These numbers come from the 24 original competition development ` +
    "recordings, not from the additional Phase 3 research scenarios. The Phase 3 holdout result is not an official competition score. " +
    "P3 is experimental, with known limitations: occasional delayed responses and missed quiet interruptions on the additional data.";
}

function renderCredits() {
  const a = S.config.attribution || [];
  $("credits").innerHTML = a.map((x) => `<p><b>${esc(x.work)}</b>, ${esc(x.by)}${x.cite ? `. ${esc(x.cite)}` : ""}. ` +
    `<a href="${esc(x.url)}" rel="noopener noreferrer">${esc(x.url)}</a>. Licence: ${esc(x.license)}` +
    `${x.license_url ? ` (<a href="${esc(x.license_url)}" rel="noopener noreferrer">licence text</a>)` : ""}. Changes: ${esc(x.changes)}</p>`).join("") +
    "<p>Each scenario lists the individual sound clips it uses, with their authors and licences, under its description.</p>";
}

// ------------------------------------------------------------------------------------------------ timeline canvas
const LANES = { left: 70, right: 10, axis: 18, agent: [22, 70], caller: [96, 70], dets: { baseline: [172, 22], p3: [198, 22] }, labels: [226, 44] };
const H = 278;

function canvasSize() {
  const cv = $("timeline");
  const w = Math.max(320, cv.clientWidth), dpr = window.devicePixelRatio || 1;
  return { cv, w, h: H, dpr };
}

function xOf(ms, w) {
  return LANES.left + (ms / S.timeline.duration_ms) * (w - LANES.left - LANES.right);
}

function buildStaticLayer() {
  if (!S.timeline || !S.audio) return;
  const { cv, w, h, dpr } = canvasSize();
  cv.style.height = `${h}px`;
  cv.width = w * dpr; cv.height = h * dpr;
  const off = document.createElement("canvas");
  off.width = w * dpr; off.height = h * dpr;
  const g = off.getContext("2d");
  g.scale(dpr, dpr);
  const dur = S.timeline.duration_ms;
  g.font = "11px system-ui, sans-serif"; g.textBaseline = "top";
  const span = (w - LANES.left - LANES.right) / (dur / 1000);           // px per second
  const step = span > 60 ? 2000 : span > 25 ? 5000 : 10000;             // keep axis labels apart on phones
  for (let ms = 0; ms <= dur; ms += step) {
    const x = xOf(ms, w);
    g.fillStyle = css("--grid"); g.fillRect(x, LANES.axis, 1, h - LANES.axis);
    if (x < w - 22) { g.fillStyle = css("--muted"); g.fillText(`${ms / 1000}s`, x + 2, 2); }
  }
  for (const u of S.timeline.protocol.agent_utterances) {
    g.fillStyle = css("--utt");
    g.fillRect(xOf(u.start_ms, w), LANES.agent[0], xOf(u.end_ms, w) - xOf(u.start_ms, w), LANES.agent[1]);
  }
  drawWave(g, S.audio.agent, LANES.agent, css("--agent"), w);
  drawWave(g, S.audio.caller, LANES.caller, css("--caller"), w);
  for (const id of DETS) {
    const [y, hh] = LANES.dets[id];
    g.fillStyle = css("--bg"); g.fillRect(LANES.left, y, w - LANES.left - LANES.right, hh);
  }
  const gt = S.timeline.ground_truth, [ly, lh] = LANES.labels;
  g.fillStyle = css("--muted-fill");
  for (const s of gt.user_segments) g.fillRect(xOf(s.start_ms, w), ly + lh - 8, Math.max(1, xOf(s.end_ms, w) - xOf(s.start_ms, w)), 6);
  for (const ev of gt.events) {
    const key = ev.expected === "STOP" ? "stop" : ev.expected === "KEEP" ? "keep" : "either";
    const a = xOf(ev.onset_ms, w), b = xOf(ev.offset_ms ?? ev.turn_end_ms ?? ev.onset_ms + 200, w);
    g.fillStyle = css(`--${key}-soft`); g.fillRect(a, ly, Math.max(3, b - a), lh - 12);
    g.strokeStyle = css(`--${key}`); g.lineWidth = 1; g.strokeRect(a + .5, ly + .5, Math.max(3, b - a) - 1, lh - 13);
    if (ev.evident_ms !== undefined) { g.fillStyle = css(`--${key}`); g.fillRect(xOf(ev.evident_ms, w), ly, 2, lh - 12); }
    g.fillStyle = css(`--${key}`); g.font = "700 10px system-ui, sans-serif";
    g.fillText(ev.kind === "interruption" ? ev.expected : "EOT", Math.min(b + 3, w - 34), ly + 3);
  }
  g.fillStyle = css("--text"); g.font = "600 11px system-ui, sans-serif";
  g.fillText("Agent", 6, LANES.agent[0] + 4); g.fillText("Caller", 6, LANES.caller[0] + 4);
  g.fillText("Baseline", 6, LANES.dets.baseline[0] + 5); g.fillText("P3", 6, LANES.dets.p3[0] + 5);
  g.fillText("Labels", 6, ly + 4);
  g.fillStyle = css("--muted"); g.font = "10px system-ui, sans-serif";
  g.fillText("channel 0", 6, LANES.agent[0] + 18); g.fillText("channel 1", 6, LANES.caller[0] + 18);
  g.fillText("ground truth", 6, ly + 18);
  S.staticLayer = off;
}

function drawWave(g, data, [y, hh], color, w) {
  const x0 = LANES.left, cols = Math.floor(w - LANES.left - LANES.right), per = data.length / cols, mid = y + hh / 2;
  let peak = 1e-4;
  for (let i = 0; i < data.length; i += 7) peak = Math.max(peak, Math.abs(data[i]));
  const amp = (hh / 2 - 2) / Math.min(1, peak * 1.1);    // scaled to this channel's peak, so quiet lines stay visible
  g.fillStyle = color;
  for (let c = 0; c < cols; c++) {
    let lo = 0, hi = 0;
    const a = Math.floor(c * per), b = Math.min(data.length, Math.floor((c + 1) * per));
    for (let i = a; i < b; i++) { const v = data[i]; if (v < lo) lo = v; else if (v > hi) hi = v; }
    const top = mid - Math.min(hh / 2 - 1, hi * amp), bot = mid - Math.max(-(hh / 2 - 1), lo * amp);
    g.fillRect(x0 + c, top, 1, Math.max(1, bot - top));
  }
}

function silentSpans(id) {
  const spans = [];
  for (const u of S.timeline.protocol.agent_utterances) {
    let from = null;
    for (const d of S.det[id].decisions.filter((x) => x.utt_id === u.utt_id)) {
      if (d.decision === "STOP" && from === null) from = d.stream_ms;
      else if (d.decision === "RESUME" && from !== null) { spans.push([from, d.stream_ms]); from = null; }
    }
    if (from !== null) spans.push([from, u.end_ms]);
  }
  return spans;
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
  // the agent you hear: silent spans of the selected detector over the agent waveform
  g.fillStyle = css("--muted-fill");
  for (const [a, b] of silentSpans(S.listen)) g.fillRect(xOf(a, w), LANES.agent[0], Math.max(2, xOf(b, w) - xOf(a, w)), LANES.agent[1]);
  g.font = "700 10px system-ui, sans-serif"; g.textBaseline = "middle";
  for (const id of DETS) {
    const [y, hh] = LANES.dets[id];
    g.fillStyle = css("--stop-soft");
    for (const [a, b] of silentSpans(id)) g.fillRect(xOf(a, w), y, Math.max(2, xOf(b, w) - xOf(a, w)), hh);
    for (const d of S.det[id].decisions) {
      const x = xOf(d.stream_ms, w);
      if (d.decision === "STOP") {
        g.fillStyle = css("--stop"); g.fillRect(x - 1, y, 3, hh);
        const label = `STOP ${sec(d.stream_ms)}s`;
        const tw = g.measureText(label).width;
        g.fillText(label, x + 5 + tw > w - 4 ? x - 5 - tw : x + 5, y + hh / 2);
        if (id === S.listen) { g.globalAlpha = 0.55; g.fillRect(x - 1, LANES.agent[0], 2, LANES.caller[0] + LANES.caller[1] - LANES.agent[0]); g.globalAlpha = 1; }
      } else {
        g.fillStyle = css("--muted"); g.fillRect(x, y + 4, 1, hh - 8);
      }
    }
  }
  if (S.started) {
    g.fillStyle = css("--playhead");
    g.fillRect(xOf(t, w) - 1, LANES.axis, 2, h - LANES.axis);
  }
}

// ------------------------------------------------------------------------------------------------ start-up
async function init() {
  try {
    S.config = await api("/api/config");
  } catch (e) { showError(`Cannot reach the demo server: ${e.message}`); return; }
  for (const m of S.config.models) S.models[m.id] = m;
  if (window.sarjLive) window.sarjLive.init(S.config);
  S.lookahead = S.config.lookahead_ms || 0;
  S.pollMs = S.config.poll_ms || 15;
  const msel = $("model");
  const order = ["p3", "baseline"];
  msel.innerHTML = order.filter((id) => S.models[id]).map((id) => { const m = S.models[id];
    return `<option value="${esc(id)}" ${m.available ? "" : "disabled"}>${esc(m.name)} · ${esc(m.revision || "?")}${m.available ? "" : " (unavailable)"}</option>`; }).join("");
  if (!DETS.every((id) => S.models[id] && S.models[id].available)) {
    showError("Both detectors must be available; see the server console."); return;
  }
  msel.value = S.listen;
  const pub = S.config.mode === "public";
  const dev = collection("dev");
  $("mode-chip").textContent = pub ? "public demo" : "local · team use (private recordings)";
  const note = $("dataset-note");
  note.hidden = false;
  note.textContent = "The 24 original SARJ competition development recordings, as supplied with the kit (separate agent and " +
    "caller channels; AI-generated voices, script-derived labels). " +
    (pub ? (dev.rights || "") : "Private: team use only; the public site does not serve them until hosting is authorised.");
  const groups = new Map();
  for (const s of S.config.scenarios) {
    const col = collection(s.collection);
    const key = `${col.title || s.collection}${col.public ? "" : " (private)"} · ${s.category_name || s.category}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(s);
  }
  $("scenario").innerHTML = [...groups.entries()].map(([label, list]) => `<optgroup label="${esc(label)}">` +
    list.map((s) => `<option value="${esc(s.id)}">${esc(s.id)} · ${esc(s.title)} · expects ${esc(s.expected.join(", "))}</option>`).join("") +
    "</optgroup>").join("");
  renderModelChip(); renderBench(); renderCredits();
  $("play").onclick = () => (S.playing ? pause() : play());
  $("reset").onclick = () => reset();
  $("scenario").onchange = (e) => loadScenario(e.target.value);
  $("rec").onclick = () => toggleRecording();
  $("upload").onchange = (e) => uploadOwn(e.target.files[0]);
  msel.onchange = (e) => changeListen(e.target.value);
  window.addEventListener("pagehide", () => DETS.forEach((id) => closeSession(S.det[id].session)));
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !["SELECT", "BUTTON"].includes(document.activeElement.tagName)) { e.preventDefault(); $("play").click(); }
  });
  window.addEventListener("resize", () => buildStaticLayer());
  const first = S.config.scenarios.find((s) => s.id === "NV-D01") || S.config.scenarios[0];
  $("scenario").value = first.id;
  await loadScenario(first.id);
  requestAnimationFrame(frame);
}

// Read-only snapshot for automated browser checks.
window.sarjDemo = {
  snapshot: () => ({
    scenario: S.scenId, listen: S.listen, started: S.started, playing: S.playing, ended: S.ended,
    playheadMs: playhead(), fedMs: fedMs(), done: allDone(), agentMuted: S.agentMuted,
    agentGain: S.nodes ? S.nodes.agent.gain.gain.value : null, callerGain: S.nodes ? S.nodes.caller.gain.gain.value : null,
    ctxState: S.ctx ? S.ctx.state : null, sources: S.sources ? Object.keys(S.sources) : [],
    sessions: Object.fromEntries(DETS.map((id) => [id, S.det[id].session])),
    session: DETS.map((id) => S.det[id].session || "").join("+"),
    decisions: Object.fromEntries(DETS.map((id) => [id, S.det[id].decisions.map((d) => ({ stream_ms: d.stream_ms, decision: d.decision, utt_id: d.utt_id, pageDelayMs: d.pageDelayMs }))])),
    events: Object.fromEntries(DETS.map((id) => [id, S.det[id].score ? S.det[id].score.events : []])),
    pending: DETS.reduce((n, id) => n + S.det[id].pending.length, 0), lookahead: S.lookahead, rtts: S.rtts.slice(-20),
    shownVerdicts: [...document.querySelectorAll("#events td:nth-child(n+4)")].map((td) => td.textContent),
    headline: $("headline").hidden ? "" : $("headline").textContent,
    cards: Object.fromEntries(DETS.map((id) => [id, $(`st-${id}`).textContent])),
  }),
};

init();
