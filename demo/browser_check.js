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

(async () => {
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
  if (LATENCY) {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: LATENCY, downloadThroughput: -1, uploadThroughput: -1 });
  }
  const consoleErrors = [];
  page.on("console", (m) => {                       // (the deliberate 404 probe for GI-D01 below is not an error)
    if (m.type() === "error" && !(m.location().url || "").includes("GI-D01")) consoleErrors.push(m.text());
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
  if (cfg.mode === "public") check("public site: no competition recordings", cfg.scenarios.every((x) => x.collection !== "dev") &&
    (await page.evaluate(() => fetch("/api/audio/GI-D01.wav").then((r) => r.status))) === 404);

  // 1. A cough (KEEP): Baseline stops falsely, P3 keeps talking. Pause/resume on the way.
  await select("NV-D51");
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
  await select("GI-D51");
  const after = await snap();
  check("switching scenario stops playback and starts fresh detectors", !after.playing && after.decisions.p3.length === 0 &&
    after.fedMs === 0 && after.session !== before.session && after.scenario === "GI-D51");
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

  // 4. Phone-sized layout.
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
