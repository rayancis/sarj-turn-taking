// Optional end-to-end check of the demo page in headless Chromium (real time, about 2 minutes).
// Needs Node.js and Playwright; not part of the pytest suite. Start the server first, then:
//   node demo/browser_check.js [URL]            default http://127.0.0.1:8770/  (local or hosted, e.g. https://….onrender.com/)
//   LATENCY_MS=150 node demo/browser_check.js    emulate a slow network (adds latency to every request)
//   SHOT_DIR=some/folder                         where screenshots go (default: current folder)
// It checks what pytest cannot: audio playback, STOP muting, Baseline vs P3 side by side, pause, reset, switching.
const { chromium } = require("playwright");

const URL = process.argv[2] || "http://127.0.0.1:8770/";
const LATENCY = Number(process.env.LATENCY_MS || 0);
const SHOTS = process.env.SHOT_DIR || ".";
const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok, detail }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Test audio for "test your own audio": the caller channel of GI-D01, as a 48 kHz stereo upload and a 16 kHz
// mono fake microphone (Chromium plays it as the microphone).
const fs = require("fs"), os = require("os"), path = require("path");
function wavFile(samples, rate, channels) {
  const data = Buffer.alloc(samples.length * 2 * channels);
  samples.forEach((v, i) => { for (let c = 0; c < channels; c++) data.writeInt16LE(v, (i * channels + c) * 2); });
  const h = Buffer.alloc(44);
  h.write("RIFF", 0); h.writeUInt32LE(36 + data.length, 4); h.write("WAVEfmt ", 8); h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20); h.writeUInt16LE(channels, 22); h.writeUInt32LE(rate, 24); h.writeUInt32LE(rate * 2 * channels, 28);
  h.writeUInt16LE(2 * channels, 32); h.writeUInt16LE(16, 34); h.write("data", 36); h.writeUInt32LE(data.length, 40);
  return Buffer.concat([h, data]);
}
async function testAudio() {
  const buf = Buffer.from(await (await fetch(new globalThis.URL("api/audio/GI-D01.wav", URL))).arrayBuffer());
  let o = 12, dataAt = -1, size = 0;
  while (o + 8 <= buf.length) { const id = buf.toString("ascii", o, o + 4), n = buf.readUInt32LE(o + 4); if (id === "data") { dataAt = o + 8; size = n; break; } o += 8 + n + (n & 1); }
  const caller = [];
  for (let i = dataAt; i + 3 < dataAt + size; i += 4) caller.push(buf.readInt16LE(i + 2));
  const up = [];
  for (let i = 0; i < caller.length - 1; i++) for (let k = 0; k < 3; k++) up.push(Math.round(caller[i] + (caller[i + 1] - caller[i]) * k / 3));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sarj-own-"));
  const upload = path.join(dir, "caller-48k-stereo.wav"), mic = path.join(dir, "caller-mic.wav");
  fs.writeFileSync(upload, wavFile(up, 48000, 2));
  fs.writeFileSync(mic, wavFile(caller, 16000, 1));
  return { upload, mic };
}

(async () => {
  const own = await testAudio();
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required", "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${own.mic}`] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
  if (LATENCY) {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: LATENCY, downloadThroughput: -1, uploadThroughput: -1 });
  }
  const consoleErrors = [];
  page.on("console", (m) => {                       // (the deliberate 404 probe for GI-D51 below is not an error)
    if (m.type() === "error" && !(m.location().url || "").includes("GI-D51")) consoleErrors.push(m.text());
  });
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
  const snap = () => page.evaluate(() => window.sarjDemo.snapshot());
  const waitFor = async (pred, timeoutMs, stepMs = 50) => {
    const end = Date.now() + timeoutMs;
    for (;;) { const s = await snap(); if (pred(s)) return s; if (Date.now() > end) return null; await sleep(stepMs); }
  };
  const ready = (s) => s.sessions && s.sessions.p3 && s.sessions.baseline && !s.started;
  const select = async (value) => {
    const old = (await snap()).session;
    await page.selectOption("#scenario", value);
    return waitFor((s) => s.session !== old && ready(s), 20000);
  };
  const playToEnd = async () => { await page.click("#play"); return waitFor((x) => x.ended && x.done, 60000, 200); };

  const t0 = Date.now();
  const resp = await page.goto(URL);
  check("page served", resp && resp.ok(), `${resp && resp.status()} ${resp && resp.headers()["content-type"]}, ${Date.now() - t0} ms`);
  let s = await waitFor(ready, 90000, 200);           // a sleeping free host may need ~1 min to wake
  check("page loads with a Baseline and a P3 session", !!s, s ? `${s.scenario}` : "no sessions");
  const cfg = await page.evaluate(() => fetch("/api/config").then((r) => r.json()));
  const options = await page.$$eval("#model option", (os) => os.map((o) => o.textContent));
  check("exactly two detectors: P3 and Baseline", options.length === 2 && /^P3/.test(options[0]) && /^Baseline/.test(options[1]) &&
    cfg.models.map((m) => m.id).join() === "p3,baseline", options.join(" | "));
  const body = await page.content();
  check("no E01b anywhere on the page", !/E01b|e566234/.test(body + JSON.stringify(cfg)));
  console.log(`mode ${cfg.mode}, look-ahead ${cfg.lookahead_ms} ms, ${cfg.scenarios.length} scenarios in ${cfg.collections.map((c) => c.key).join(", ")}`);
  const entries = await page.$$eval("#scenario option", (os) => os.map((o) => o.value));
  check("selector lists exactly the 24 original competition recordings", entries.length === 24 &&
    entries.every((id) => /^[A-Z]{2}-D0[1-3]$/.test(id)) && cfg.scenarios.every((x) => x.collection === "dev"),
    `${entries.length} entries: ${entries.slice(0, 4).join(", ")} …`);
  const research = await page.evaluate(() => fetch("/api/audio/GI-D51.wav").then((r) => r.status));
  check("Phase 3 research scenarios are not served", research === 404);

  // 1. A cough (KEEP): Baseline stops falsely, P3 keeps talking. Pause/resume on the way.
  await select("NV-D01");
  await page.click("#play");
  const leads = [];
  for (let i = 0; i < 15; i++) { await sleep(150); const x = await snap(); leads.push((x.fedMs - 20) - x.playheadMs); }
  check("both detectors are fed in step with the audio clock", leads.every((l) => l <= cfg.lookahead_ms + 20 && l > -40),
    `last fed frame minus playhead: ${Math.min(...leads).toFixed(0)} .. ${Math.max(...leads).toFixed(0)} ms`);
  check("agent and caller play from one clock", (await snap()).sources.join() === "agent,caller");
  await page.click("#play");
  await sleep(300 + 2 * LATENCY);
  const p1 = await snap();
  await sleep(1200);
  const p2 = await snap();
  check("pause freezes playhead and both feeds", p1.ctxState === "suspended" && Math.abs(p2.playheadMs - p1.playheadMs) < 1 && p2.fedMs === p1.fedMs,
    `playhead ${p1.playheadMs.toFixed(1)} -> ${p2.playheadMs.toFixed(1)} ms`);
  await page.click("#play");
  // switch whose decisions you hear to Baseline before its STOP arrives
  await page.selectOption("#model", "baseline");
  const bstop = await waitFor((x) => x.decisions.baseline.some((d) => d.decision === "STOP"), 30000);
  await sleep(150);
  const m = await snap();
  const bd = bstop && bstop.decisions.baseline.find((d) => d.decision === "STOP");
  check("Baseline's real STOP silences the agent you hear; caller continues", !!bd && m.agentMuted && m.agentGain < 0.05 && m.callerGain === 1,
    bd ? `STOP at ${bd.stream_ms} ms, shown ${bd.pageDelayMs.toFixed(1)} ms after its frame; agent gain ${m.agentGain.toFixed(3)}` : "no STOP");
  await page.selectOption("#model", "p3");
  await sleep(150);
  const m2 = await snap();
  check("switching to P3 (no STOP) brings the agent back, same playback", !m2.agentMuted && m2.agentGain > 0.9 && m2.playing);
  await page.screenshot({ path: `${SHOTS}/demo-compare-live.png`, fullPage: true });
  const end = await waitFor((x) => x.ended && x.done, 40000, 200);
  check("cough: Baseline false stop, P3 kept talking (official scorer)", !!end && end.events.baseline[0].outcome === "false_stop" &&
    end.events.p3[0].outcome === "kept" && /Baseline stopped falsely; P3 correctly kept talking/.test(end.headline),
    end ? `${end.events.baseline[0].outcome} / ${end.events.p3[0].outcome}; "${end.headline}"` : "not finished");
  await page.screenshot({ path: `${SHOTS}/demo-compare-done.png`, fullPage: true });

  // 2. Reset clears state; replay gives the same decisions.
  const key = (x) => JSON.stringify(Object.fromEntries(Object.entries(x.decisions).map(([k, v]) => [k, v.map((d) => [d.stream_ms, d.decision])])));
  const first = key(end);
  await page.click("#reset");
  const r = await waitFor((x) => ready(x) && x.decisions.p3.length === 0 && x.decisions.baseline.length === 0, 15000);
  check("reset clears decisions and returns to 0", !!r && r.playheadMs === 0 && r.session !== end.session);
  const again = await playToEnd();
  check("repeated playback gives identical decisions", !!again && key(again) === first, first);

  // 3. Genuine interruption: P3 detects it; scenario switch mid-playback clears state.
  await page.click("#reset");
  await waitFor(ready, 15000);
  await page.click("#play");
  await sleep(800);
  const before = await snap();
  await select("GI-D01");
  const after = await snap();
  check("switching scenario stops playback and starts fresh detectors", !after.playing && after.decisions.p3.length === 0 &&
    after.fedMs === 0 && after.session !== before.session && after.scenario === "GI-D01");
  await page.click("#play");
  const gstop = await waitFor((x) => x.decisions.p3.some((d) => d.decision === "STOP"), 30000);
  const gd = gstop && gstop.decisions.p3.find((d) => d.decision === "STOP");
  await sleep(150);
  const gm = await snap();
  check("P3's real STOP on a genuine interruption silences the agent", !!gd && gm.agentMuted && gm.agentGain < 0.05 && gm.callerGain === 1,
    gd ? `P3 STOP at ${gd.stream_ms} ms, shown ${gd.pageDelayMs.toFixed(1)} ms after its frame` : "no STOP");
  if (gd && LATENCY) check(`STOP applied on time despite ${LATENCY} ms latency`, gd.pageDelayMs < 40, `${gd.pageDelayMs.toFixed(1)} ms`);
  await page.screenshot({ path: `${SHOTS}/demo-interruption.png`, fullPage: true });
  const g = await waitFor((x) => x.ended && x.done, 40000, 200);
  check("genuine interruption detected by P3 (official scorer)", !!g && g.events.p3[0].outcome === "yield" && /P3 detected it/.test(g.headline),
    g ? `"${g.headline}"` : "not finished");

  // 4. Test your own audio: upload (48 kHz stereo, resampled in the page) and record (fake microphone).
  await page.fill("#offset", "0");
  await page.setInputFiles("#upload", own.upload);
  const up = await waitFor((x) => x.scenario && x.scenario.startsWith("U") && ready(x), 30000, 200);
  check("upload creates a private own-audio scenario", !!up && /^U[0-9a-f]{32}$/.test(up.scenario),
    up ? `${up.scenario.slice(0, 9)}…, status: ${await page.textContent("#own-status")}` : await page.textContent("#own-status"));
  const upEnd = up && await playToEnd();
  const upStop = upEnd && upEnd.decisions.p3.find((d) => d.decision === "STOP");
  check("own upload: both detectors decide on it; P3 stops for the interruption (≈16.88 s as on GI-D01)",
    !!upStop && Math.abs(upStop.stream_ms - 16880) <= 200 && upEnd.decisions.baseline.some((d) => d.decision === "STOP"),
    upEnd ? `P3 ${JSON.stringify(upEnd.decisions.p3.map((d) => d.stream_ms))}, Baseline ${JSON.stringify(upEnd.decisions.baseline.map((d) => d.stream_ms))}` : "no result");
  check("own audio: no labels, no verdict", !!upEnd && upEnd.events.p3.length === 0 && /No labels/.test(await page.textContent("#events")));
  await page.screenshot({ path: `${SHOTS}/demo-own-upload.png`, fullPage: true });

  await select("GI-D01");
  await page.click("#rec");
  const recBusy = await page.waitForFunction(() => document.getElementById("rec").textContent === "Stop recording", null,
    { timeout: 15000 }).then(() => true, () => false);
  check("recording starts (microphone + agent playback)", recBusy, await page.textContent("#own-status"));
  const rec = await waitFor((x) => x.scenario && x.scenario.startsWith("U") && x.scenario !== (up && up.scenario) && ready(x), 60000, 300);
  check("recording creates an own-audio scenario", !!rec, rec ? await page.textContent("#own-status") : await page.textContent("#own-status"));
  const recEnd = rec && await playToEnd();
  const recStop = recEnd && recEnd.decisions.p3.find((d) => d.decision === "STOP");
  check("own recording: P3 stops during the agent's turn", !!recStop && recStop.utt_id === "A1",
    recEnd ? `P3 ${JSON.stringify(recEnd.decisions.p3.map((d) => [d.stream_ms, d.utt_id]))}, Baseline ${JSON.stringify(recEnd.decisions.baseline.map((d) => d.stream_ms))}` : "no result");

  // 5. Phone-sized layout.
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
  await phone.goto(URL);
  await phone.waitForFunction(() => window.sarjDemo && window.sarjDemo.snapshot().sessions.p3, null, { timeout: 30000 });
  const overflow = await phone.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check("phone layout fits the screen width", overflow <= 0, `horizontal overflow ${overflow} px`);
  await phone.screenshot({ path: `${SHOTS}/demo-phone.png`, fullPage: true });

  check("no browser console errors", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
  await browser.close();
  const failed = results.filter((x) => !x.ok).length;
  console.log(`\n${results.length - failed}/${results.length} browser checks passed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
