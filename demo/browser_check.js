// Optional end-to-end check of the demo page in headless Chromium (real time, about 90 s).
// Needs Node.js and Playwright; not part of the pytest suite. Start the server first, then:
//   node demo/browser_check.js [URL]            default http://127.0.0.1:8770/  (local or hosted, e.g. https://….onrender.com/)
//   LATENCY_MS=150 node demo/browser_check.js    emulate a slow network (adds latency to every request)
//   SHOT_DIR=some/folder                         where screenshots go (default: current folder)
// It checks what pytest cannot: audio playback, STOP muting, pause, reset and scenario switching in a browser.
const { chromium } = require("playwright");

const URL = process.argv[2] || "http://127.0.0.1:8770/";
const LATENCY = Number(process.env.LATENCY_MS || 0);
const SHOTS = process.env.SHOT_DIR || ".";
const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok, detail }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  if (LATENCY) {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: LATENCY, downloadThroughput: -1, uploadThroughput: -1 });
  }
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
    return waitFor((s) => s.session && s.session !== old && !s.started, 15000);
  };

  const t0 = Date.now();
  const resp = await page.goto(URL);
  check("page served", resp && resp.ok(), `${resp && resp.status()} ${resp && resp.headers()["content-type"]}, ${Date.now() - t0} ms`);
  let s = await waitFor((x) => x.session, 90000, 200);           // a sleeping free host may need ~1 min to wake
  check("page loads and opens a detector session", !!s, s ? `${s.scenario} / ${s.model}` : "no session");
  const cfg = await page.evaluate(() => fetch("/api/config").then((r) => r.json()));
  const pub = cfg.mode === "public";
  const STOP_CALL = pub ? "GI-P01" : "GI-D02", KEEP_CALL = pub ? "NV-P01" : "NV-D01";
  console.log(`mode ${cfg.mode}, look-ahead ${cfg.lookahead_ms} ms, scenarios ${cfg.scenarios.map((x) => x.id).join(",")}`);
  if (pub) check("public site serves no evaluation artifacts or dev calls", cfg.benchmark === null && cfg.scenarios.every((x) => x.id.includes("-P")));

  // 1. Real interruption with the current detector.
  await select("scenario", STOP_CALL);
  await page.click("#play");
  const leads = [];
  for (let i = 0; i < 20; i++) { await sleep(150); const x = await snap(); leads.push((x.fedMs - 20) - x.playheadMs); }
  // (fedMs - 20) = start of the last frame the detector has received; it may lead playback by the look-ahead only.
  check("detector feed follows the audio clock", leads.every((l) => l <= cfg.lookahead_ms + 20 && l > -(80 + 2 * LATENCY)),
    `last fed frame minus playhead: ${Math.min(...leads).toFixed(0)} .. ${Math.max(...leads).toFixed(0)} ms (look-ahead ${cfg.lookahead_ms})`);
  check("agent and caller play from one clock", (await snap()).sources.join() === "agent,caller");

  await page.click("#play");                                     // pause
  await sleep(300 + 2 * LATENCY);
  const p1 = await snap();
  await sleep(1500);
  const p2 = await snap();
  check("pause freezes playhead and detector feed", p1.ctxState === "suspended" && Math.abs(p2.playheadMs - p1.playheadMs) < 1 && p2.fedMs === p1.fedMs,
    `playhead ${p1.playheadMs.toFixed(1)} -> ${p2.playheadMs.toFixed(1)} ms, fed ${p1.fedMs} -> ${p2.fedMs}`);
  await page.click("#play");
  const p3 = await waitFor((x) => x.playheadMs > p2.playheadMs + 300, 3000);
  check("resume continues from the paused position", !!p3 && p3.fedMs >= p2.fedMs);

  const stop = await waitFor((x) => x.decisions.some((d) => d.decision === "STOP"), 30000);
  const d = stop && stop.decisions.find((x) => x.decision === "STOP");
  check(`real STOP decision displayed (${STOP_CALL})`, !!d, d ? `STOP at ${d.stream_ms} ms during ${d.utt_id}, applied ${d.pageDelayMs.toFixed(1)} ms after its frame time` : "none");
  if (d && LATENCY) check(`STOP applied on time despite ${LATENCY} ms network latency`, d.pageDelayMs < 40, `${d.pageDelayMs.toFixed(1)} ms; round trips ${stop.rtts.map(Math.round).join(",")} ms`);
  await sleep(150);
  const m = await snap();
  check("STOP mutes agent audio, caller continues", m.agentMuted && m.agentGain < 0.05 && m.callerGain === 1 && m.playing,
    `agent gain ${m.agentGain.toFixed(3)}, caller gain ${m.callerGain}`);
  await page.screenshot({ path: `${SHOTS}/demo-stop.png` });
  const end = await waitFor((x) => x.ended && x.done, 30000, 200);
  const ev = end && end.events[0];
  check("call finishes and is scored by the official scorer", !!ev && ev.closed && ev.outcome === "yield", ev ? `${ev.outcome}, credit ${ev.credit}` : "not finished");

  // 2. Reset clears state.
  await page.click("#reset");
  const r = await waitFor((x) => x.session && !x.started && x.decisions.length === 0, 10000);
  check("reset clears decisions and returns to 0", !!r && r.playheadMs === 0 && r.session !== end.session);

  // 3. Scenario switch mid-playback clears previous state.
  await page.click("#play");
  await sleep(1000);
  const before = await snap();
  await select("scenario", KEEP_CALL);
  const after = await snap();
  check("switching scenario stops playback and starts a fresh detector", !after.playing && after.decisions.length === 0 && after.fedMs === 0 &&
    after.session !== before.session && after.scenario === KEEP_CALL);

  // 4. KEEP call with the current detector: no STOP, and no verdict shown before playback reaches it.
  await page.click("#play");
  let wasMuted = false, earlyVerdict = false;
  for (;;) {
    const x = await snap();
    wasMuted = wasMuted || x.agentMuted;
    const e0 = x.events[0];
    if (e0 && !x.ended && x.playheadMs < e0.close_ms && x.shownVerdicts.some((t) => /✓|✗|◐/.test(t))) earlyVerdict = true;
    if (x.ended && x.done) { s = x; break; }
    await sleep(100);
  }
  const k = s.events[0];
  check(`KEEP call: agent keeps speaking (${KEEP_CALL}, current detector)`, !wasMuted && s.decisions.filter((x) => x.decision === "STOP").length === 0 && k.outcome === "kept",
    `${k.outcome}, status: ${s.status}`);
  check("no verdict is shown before playback reaches its decision point", !earlyVerdict);
  await page.screenshot({ path: `${SHOTS}/demo-keep.png` });

  // 5. Switch detector: the baseline makes a real false stop on the same call.
  await select("model", "baseline");
  await page.click("#play");
  const b = await waitFor((x) => x.ended && x.done, 40000, 200);
  const bs = b && b.decisions.filter((x) => x.decision === "STOP");
  check(`baseline selectable; its own decisions shown (${KEEP_CALL})`, !!b && bs.length > 0 && b.events[0].outcome === "false_stop",
    b ? `STOP at ${bs.map((x) => x.stream_ms).join(", ")} ms -> ${b.events[0].outcome}` : "not finished");

  // 6. Phone-sized layout: no horizontal page scroll.
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  await phone.goto(URL);
  await phone.waitForFunction(() => window.sarjDemo && window.sarjDemo.snapshot().session, null, { timeout: 30000 });
  const overflow = await phone.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check("phone layout fits the screen width", overflow <= 0, `horizontal overflow ${overflow} px`);
  await phone.screenshot({ path: `${SHOTS}/demo-phone.png`, fullPage: false });

  check("no browser console errors", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
  await browser.close();
  const failed = results.filter((x) => !x.ok).length;
  console.log(`\n${results.length - failed}/${results.length} browser checks passed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
