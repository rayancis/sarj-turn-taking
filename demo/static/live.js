"use strict";
// Live AI conversation: Gemini Live speaks; SARJ (P3 by default) decides when the caller has interrupted it.
// The page plays the agent, captures the microphone and streams synchronised 20 ms kit frames
// [agent as actually played, caller] to the server, which runs both detectors and talks to Gemini.
(() => {
  const $ = (id) => document.getElementById(id);
  const L = { ws: null, ctx: null, node: null, stream: null, ready: false, t0: 0, gen: 0, timer: null,
    lastPlayed: false, events: [], stopsShown: 0, maxSeconds: 0 };
  const sec = (ms) => (ms / 1000).toFixed(2);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function setStatus(text, cls) {
    const el = $("live-status");
    el.textContent = text;
    el.className = `live-status ${cls || ""}`;
  }

  function lamp(id, on) { $(id).classList.toggle("on", !!on); }

  function addDecision(m) {
    if (m.decision !== "STOP" && m.decision !== "TAKE_TURN") return;
    const tr = document.createElement("tr");
    const who = m.model === "p3" ? "P3" : "Baseline";
    const role = m.decision === "STOP"
      ? (m.controls ? `<b>controlled the agent</b>${m.utt ? ` (agent turn ${m.utt})` : ""}` : "shadow only: did not control")
      : (m.controls ? "end of the caller's turn → sent to Gemini" : "end of turn (shadow)");
    tr.className = m.decision === "STOP" ? (m.controls ? "ctrl" : "shadow") : "turn";
    tr.innerHTML = `<td class="num">${sec(m.t_ms)} s</td><td><span class="who ${m.model}">${who}</span></td>` +
      `<td><span class="pill ${m.decision === "STOP" ? "STOP" : "other"}">${esc(m.decision)}</span></td><td>${role}</td>`;
    $("live-decisions").prepend(tr);
    if (m.decision === "STOP") {
      const el = $(`live-${m.model}-last`);
      el.textContent = `STOP at ${sec(m.t_ms)} s${m.controls ? " (controlled)" : " (shadow)"}`;
    }
  }

  function addCaption(who, text) {
    const box = $("live-captions");
    let last = box.lastElementChild;
    if (!last || last.dataset.who !== who) {
      last = document.createElement("p");
      last.dataset.who = who;
      last.className = `cap ${who}`;
      last.innerHTML = `<b>${who === "agent" ? "Agent" : "You"}:</b> <span dir="auto"></span>`;
      box.appendChild(last);
    }
    last.querySelector("span").textContent += text;
    box.scrollTop = box.scrollHeight;
  }

  function showSummary(s, reason) {
    if (!s) return;
    const pb = s.playback_stop_after_stop_ms || {};
    $("live-summary").hidden = false;
    $("live-summary").innerHTML =
      `<h3>What happened in this conversation</h3><ul>` +
      `<li>Ended: ${esc(reason)}; ${esc(s.duration_s)} s of audio streamed; controller: <b>${s.controller === "p3" ? "P3" : "Baseline"}</b>.</li>` +
      `<li>STOP decisions: P3 ${s.stops.p3}, Baseline ${s.stops.baseline}. Barge-ins executed (controller STOP while the agent spoke): <b>${s.barge_ins}</b>.</li>` +
      `<li>Agent audio stopped after the controller's STOP: ${pb.n ? `median ${pb.median} ms, max ${pb.max} ms (measured in the played audio)` : "–"}.</li>` +
      `<li>Gemini interruptions: ${s.gemini_interrupted}, of which <b>${s.gemini_interrupted_unrequested}</b> without an activity signal from SARJ` +
      ` (Gemini's own voice detection is disabled). ${s.sarj_controlled_every_barge_in ? "Every interruption was SARJ's decision." : "Check the event log."}</li>` +
      `<li>Caller audio sent to Gemini: ${(s.caller_audio_to_gemini_ms / 1000).toFixed(1)} s (only during your turns and after a STOP); agent audio received: ${(s.agent_audio_ms / 1000).toFixed(1)} s.</li>` +
      `</ul><p class="note">Live conversations have no ground-truth labels: these are counts of real decisions, not accuracy or benchmark scores.</p>`;
  }

  async function start() {
    const cfg = window.SARJ_CONFIG && window.SARJ_CONFIG.live;
    if (!cfg || !cfg.enabled) { setStatus((cfg && cfg.reason) || "The live agent is unavailable.", "bad"); return; }
    if (!$("live-consent").checked) { setStatus("Please confirm that your microphone audio may be sent to Google Gemini.", "bad"); return; }
    $("live-start").disabled = true;
    $("live-summary").hidden = true;
    $("live-decisions").innerHTML = "";
    $("live-captions").innerHTML = "";
    $("live-p3-last").textContent = $("live-baseline-last").textContent = "–";
    setStatus("Asking for the microphone…");
    try {
      try { if (navigator.audioSession) navigator.audioSession.type = "play-and-record"; } catch (_) { /* ignore */ }
      L.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      lamp("live-mic", true);
      L.ctx = new (window.AudioContext || window.webkitAudioContext)({ latencyHint: "interactive" });
      await L.ctx.resume();
      await L.ctx.audioWorklet.addModule("live_worklet.js");
      L.node = new AudioWorkletNode(L.ctx, "sarj-live", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
      L.ctx.createMediaStreamSource(L.stream).connect(L.node);
      L.node.connect(L.ctx.destination);
      L.node.port.onmessage = (e) => onFrame(e.data);
      setStatus("Connecting to the agent…");
      L.ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/live`);
      L.ws.binaryType = "arraybuffer";
      L.ws.onopen = () => L.ws.send(JSON.stringify({ type: "start", controller: $("live-controller").value }));
      L.ws.onmessage = (e) => onMessage(e.data);
      L.ws.onclose = () => { if (L.ws) { setStatus("Disconnected. Press Start to talk again.", "bad"); cleanup(); } };
      L.ws.onerror = () => setStatus("Connection error.", "bad");
      $("live-end").disabled = false;
    } catch (e) {
      setStatus(e && e.name === "NotAllowedError" ? "Microphone permission was refused." : `Could not start: ${e.message}`, "bad");
      cleanup();
    }
  }

  function onFrame(d) {
    lamp("live-agent", d.played);
    lamp("live-user", d.micDb > -45);
    if (L.ready && L.ws && L.ws.readyState === 1 && L.ws.bufferedAmount < 64000) L.ws.send(d.frame);
  }

  function onMessage(data) {
    if (data instanceof ArrayBuffer) {                  // agent audio: 1 byte generation + PCM16 24 kHz
      const gen = new Uint8Array(data, 0, 1)[0];
      const pcm = new Int16Array(data.slice(1));
      L.node.port.postMessage({ audio: pcm, gen }, [pcm.buffer]);
      return;
    }
    const m = JSON.parse(data);
    if (m.type === "ready") {
      L.ready = true; L.t0 = performance.now(); L.maxSeconds = m.max_seconds;
      setStatus(`Connected (${m.model}). Speak in Arabic; interrupt the agent whenever you like.`, "ok");
      $("live-controller-shown").textContent = m.controller === "p3" ? "P3 Hybrid" : "Baseline";
      L.timer = setInterval(() => {
        const left = Math.max(0, L.maxSeconds - (performance.now() - L.t0) / 1000);
        $("live-timer").textContent = `${Math.floor(left / 60)}:${String(Math.floor(left % 60)).padStart(2, "0")} left`;
      }, 500);
    } else if (m.type === "flush") {                   // SARJ said STOP: silence the agent now
      L.node.port.postMessage({ flush: m.gen & 0xFF });
      setStatus(`${m.by === "p3" ? "P3" : "Baseline"} detected an interruption: agent stopped. Go ahead.`, "stop");
    } else if (m.type === "decision") {
      addDecision(m);
    } else if (m.type === "caption") {
      addCaption(m.who, m.text);
    } else if (m.type === "event") {
      L.events.push(m);
      const ev = $("live-events");
      ev.textContent = L.events.slice(-12).map((e) => `${sec(e.t_ms)} s  ${e.kind}${e.why ? ` (${e.why})` : ""}` +
        `${e.requested_by_us !== undefined ? ` requested_by_SARJ=${e.requested_by_us}` : ""}` +
        `${e.after_stop_ms !== undefined ? ` ${e.after_stop_ms} ms after STOP` : ""}`).join("\n");
    } else if (m.type === "notice") {
      setStatus(m.message, "");
    } else if (m.type === "error") {
      setStatus(m.message, "bad");
    } else if (m.type === "ended") {
      showSummary(m.summary, m.reason);
      if (!/error|closed/.test($("live-status").className)) setStatus(`Conversation ended (${m.reason}).`, "");
      cleanup();
    }
  }

  function end() {
    if (L.ws && L.ws.readyState === 1) L.ws.send(JSON.stringify({ type: "end" }));
    setTimeout(cleanup, 1500);
    $("live-end").disabled = true;
  }

  function cleanup() {
    const ws = L.ws;
    L.ws = null; L.ready = false;
    if (ws && ws.readyState <= 1) try { ws.close(); } catch (_) { /* ignore */ }
    if (L.node) { try { L.node.port.postMessage({ stop: true }); L.node.disconnect(); } catch (_) { /* ignore */ } L.node = null; }
    if (L.stream) { L.stream.getTracks().forEach((t) => t.stop()); L.stream = null; }
    if (L.ctx) { L.ctx.close().catch(() => {}); L.ctx = null; }
    if (L.timer) { clearInterval(L.timer); L.timer = null; }
    ["live-mic", "live-agent", "live-user"].forEach((id) => lamp(id, false));
    $("live-start").disabled = false;
    $("live-end").disabled = true;
    $("live-timer").textContent = "";
  }

  function init(cfg) {
    window.SARJ_CONFIG = cfg;
    const live = cfg.live || {};
    $("live-start").onclick = start;
    $("live-end").onclick = end;
    if (!live.enabled) {
      $("live-start").disabled = true;
      setStatus(live.reason || "The live agent is unavailable.", "bad");
    } else {
      setStatus(`Ready. Model: ${live.model}. Up to ${Math.round(live.max_seconds / 60)} minutes per conversation.`, "");
    }
    window.addEventListener("pagehide", () => { if (L.ws) end(); });
  }

  window.sarjLive = { init, snapshot: () => ({ ready: L.ready, events: L.events.slice(), status: $("live-status").textContent,
    decisions: [...$("live-decisions").querySelectorAll("tr")].map((r) => r.textContent),
    captions: $("live-captions").textContent, summary: $("live-summary").textContent }) };
})();
