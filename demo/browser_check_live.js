// Browser check of the Live AI Conversation (needs Node + Playwright). Chromium's fake microphone plays a WAV as the
// caller; the server talks to Gemini or to tests/fake_gemini.py (GEMINI_LIVE_URL). Real time, about 25 s per run.
//   node demo/browser_check_live.js URL CALLER_WAV EXPECT     EXPECT = "stop" (an interruption) or "keep" (backchannel)
//   SHOT_DIR=folder  where the screenshot goes
const { chromium } = require("playwright");

const [URL, MIC, EXPECT = "stop"] = process.argv.slice(2);
const SECONDS = Number(process.env.LIVE_SECONDS || 20);
const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required", "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${MIC}`] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto(URL + (URL.includes("?") ? "&" : "?") + "live=1");   // the section is hidden unless LIVE_MODE=on
  await page.waitForFunction(() => window.SARJ_CONFIG, null, { timeout: 30000 });
  const cfg = await page.evaluate(() => window.SARJ_CONFIG.live);
  check("live agent available", !!cfg.enabled, cfg.enabled ? `model ${cfg.model}` : cfg.reason);
  await page.check("#live-consent");
  await page.click("#live-start");
  const ready = await page.waitForFunction(() => window.sarjLive.snapshot().ready, null, { timeout: 30000 }).then(() => true, () => false);
  check("connected to the agent", ready, await page.textContent("#live-status"));
  const t0 = Date.now();
  let agentHeard = false, flushSeen = false;
  while (Date.now() - t0 < SECONDS * 1000) {
    const s = await page.evaluate(() => window.sarjLive.snapshot());
    agentHeard = agentHeard || s.events.some((e) => e.kind === "agent_utterance_start");
    flushSeen = s.events.some((e) => e.kind === "activityStart" && e.why === "barge_in");
    if (EXPECT === "stop" && flushSeen && s.events.some((e) => e.kind === "agent_playback_stopped")) break;
    await sleep(250);
  }
  check("the agent's reply streamed and played (agent utterance started)", agentHeard);
  await page.screenshot({ path: `${process.env.SHOT_DIR || "."}/live-${EXPECT}.png`, fullPage: true });
  await page.click("#live-end");
  await page.waitForFunction(() => !document.getElementById("live-summary").hidden, null, { timeout: 15000 }).catch(() => {});
  const s = await page.evaluate(() => window.sarjLive.snapshot());
  const ev = s.events;
  const stop = ev.find((e) => e.kind === "activityStart" && e.why === "barge_in");
  const stopped = ev.find((e) => e.kind === "agent_playback_stopped");
  const ctrlStops = s.decisions.filter((d) => /STOP/.test(d) && /controlled/.test(d));
  if (EXPECT === "stop") {
    check("P3 STOP controlled the agent", ctrlStops.length > 0, ctrlStops[0]);
    check("Gemini got activityStart right after the STOP", !!stop, stop ? `${stop.t_ms} ms` : "none");
    check("the played agent audio stopped right after the STOP", !!stopped && stopped.after_stop_ms <= 200,
      stopped ? `${stopped.after_stop_ms} ms after STOP (in the played audio)` : "not stopped");
  } else {
    check("no barge-in on a backchannel: the agent kept talking", !stop && ctrlStops.length === 0,
      `${s.decisions.filter((d) => /STOP/.test(d)).join(" | ") || "no STOP at all"}`);
  }
  check("summary: every interruption was SARJ's decision", /without an activity signal from SARJ/.test(s.summary) &&
    /of which 0 without/.test(s.summary), s.summary.replace(/\s+/g, " ").slice(0, 400));
  check("no browser errors", errors.length === 0, errors.slice(0, 2).join(" | "));
  await browser.close();
  const failed = results.filter((x) => !x).length;
  console.log(`\n${results.length - failed}/${results.length} live checks passed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
