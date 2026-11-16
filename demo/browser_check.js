// Optional end-to-end check of the demo page in headless Chromium (real time, about 90 s).
// Needs Node.js and Playwright; not part of the pytest suite. Start the server first:
//   python demo/server.py --no-browser
//   node demo/browser_check.js [http://127.0.0.1:8770]
// It checks what pytest cannot: audio playback, STOP muting, pause, reset and scenario switching in a browser.
const { chromium } = require("playwright");

const URL = process.argv[2] || "http://127.0.0.1:8770/";
const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok, detail }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  const consoleErrors = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
  const snap = () => page.evaluate(() => window.sarjDemo.snapshot());
  const waitFor = async (pred, timeoutMs, stepMs = 50) => {
    const end = Date.now() + timeoutMs;
    for (;;) { const s = await snap(); if (pred(s)) return s; if (Date.now() > end) return null; await sleep(stepMs); }
  };
  const select = async (id, value) => {
    const old = (await snap()).session;
    await page.selectOption(`#${id}`, value);
    return waitFor((s) => s.session && s.session !== old && !s.started, 10000);
  };

  await page.goto(URL);
  let s = await waitFor((x) => x.session, 15000);
  check("page loads and opens a detector session", !!s, s ? `${s.scenario} / ${s.model}` : "no session");

  // 1. Real interruption: GI-D02 with the current detector (street noise, interruption at 14.1 s).
  await select("scenario", "GI-D02");
  await page.click("#play");
  const lags = [];
  // fedMs is the start of the next frame to feed, so (fedMs - 20) is the start of the last frame the detector got.
  for (let i = 0; i < 20; i++) { await sleep(150); const x = await snap(); lags.push(x.playheadMs - (x.fedMs - 20)); }
  check("detector feed follows the audio clock", lags.every((l) => l >= 0 && l < 80),
    `playhead minus start of last fed frame: ${Math.min(...lags).toFixed(1)} .. ${Math.max(...lags).toFixed(1)} ms`);
  check("agent and caller play from one clock", (await snap()).sources.join() === "agent,caller");

  // Pause / resume.
  await page.click("#play");
  await sleep(300);
  const p1 = await snap();
  await sleep(1500);
  const p2 = await snap();
  check("pause freezes playhead and detector feed", p1.ctxState === "suspended" && Math.abs(p2.playheadMs - p1.playheadMs) < 1 && p2.fedMs === p1.fedMs,
    `playhead ${p1.playheadMs.toFixed(1)} -> ${p2.playheadMs.toFixed(1)} ms, fed ${p1.fedMs} -> ${p2.fedMs}`);
  await page.click("#play");
  const p3 = await waitFor((x) => x.playheadMs > p2.playheadMs + 300, 3000);
  check("resume continues from the paused position", !!p3 && p3.fedMs >= p2.fedMs);

  const stop = await waitFor((x) => x.decisions.some((d) => d.decision === "STOP"), 25000);
  const d = stop && stop.decisions.find((x) => x.decision === "STOP");
  check("real STOP decision displayed (GI-D02)", !!d, d ? `STOP at ${d.stream_ms} ms during ${d.utt_id}, shown ${d.pageDelayMs.toFixed(1)} ms after its frame time` : "none");
  await sleep(150);
  const m = await snap();
  check("STOP mutes agent audio, caller continues", m.agentMuted && m.agentGain < 0.05 && m.callerGain === 1 && m.playing,
    `agent gain ${m.agentGain.toFixed(3)}, caller gain ${m.callerGain}`);
  await page.screenshot({ path: process.env.SHOT_DIR ? `${process.env.SHOT_DIR}/demo-stop.png` : "demo-stop.png" });
  const end = await waitFor((x) => x.ended && x.done, 20000, 200);
  const ev = end && end.events[0];
  check("scenario finishes and is scored by the official scorer", !!ev && ev.closed && ev.outcome === "yield", ev ? `${ev.outcome}, credit ${ev.credit}` : "not finished");

  // 2. Reset clears state.
  await page.click("#reset");
  const r = await waitFor((x) => x.session && !x.started && x.decisions.length === 0, 5000);
  check("reset clears decisions and returns to 0", !!r && r.playheadMs === 0 && r.session !== end.session);

  // 3. Scenario switch mid-playback clears previous state.
  await page.click("#play");
  await sleep(1000);
  const before = await snap();
  await select("scenario", "NV-D01");
  const after = await snap();
  check("switching scenario stops playback and starts a fresh detector", !after.playing && after.decisions.length === 0 && after.fedMs === 0 &&
    after.session !== before.session && after.scenario === "NV-D01");

  // 4. KEEP scenario: NV-D01 (cough at 11.4 s) with the current detector.
  await page.click("#play");
  let wasMuted = false;
  for (;;) {
    const x = await snap();
    wasMuted = wasMuted || x.agentMuted;
    if (x.ended && x.done) { s = x; break; }
    await sleep(100);
  }
  const k = s.events[0];
  check("KEEP scenario: agent keeps speaking (NV-D01, current detector)", !wasMuted && s.decisions.filter((x) => x.decision === "STOP").length === 0 && k.outcome === "kept",
    `${k.outcome}, status: ${s.status}`);
  await page.screenshot({ path: process.env.SHOT_DIR ? `${process.env.SHOT_DIR}/demo-keep.png` : "demo-keep.png" });

  // 5. Switch detector: the baseline makes a real false stop on the same cough.
  await select("model", "baseline");
  await page.click("#play");
  const b = await waitFor((x) => x.ended && x.done, 30000, 200);
  const bs = b && b.decisions.filter((x) => x.decision === "STOP");
  check("baseline selectable; its own decisions shown (NV-D01)", !!b && bs.length > 0 && b.events[0].outcome === "false_stop",
    b ? `STOP at ${bs.map((x) => x.stream_ms).join(", ")} ms -> ${b.events[0].outcome}` : "not finished");

  check("no browser console errors", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
  await browser.close();
  const failed = results.filter((x) => !x.ok).length;
  console.log(`\n${results.length - failed}/${results.length} browser checks passed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
