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
  const local = path.join(__dirname, "..", "data", "dev", "audio", "GI-D01.wav");   // the same original file
  const buf = fs.existsSync(local) ? fs.readFileSync(local)
    : Buffer.from(await (await fetch(new globalThis.URL("api/audio/GI-D01.wav", URL))).arrayBuffer());
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
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", e => errors.push(String(e)));
  page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
  if (LATENCY) {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", { offline:false, latency:LATENCY, downloadThroughput:-1, uploadThroughput:-1 });
  }
  const snap=()=>page.evaluate(()=>window.sarjDemo.snapshot());
  const wait=async(pred, timeout=60000)=>{
    const until=Date.now()+timeout;
    while(Date.now()<until) { const s=await snap(); if(pred(s)) return s; await sleep(100); }
    throw Error("browser state did not arrive: "+JSON.stringify(await snap()));
  };
  const ready=s=>s.sessions?.v3 && !s.started;
  const select=async id=>{ const old=(await snap()).session; await page.selectOption("#scenario",id);
    return wait(s=>s.scenario===id && s.session!==old && ready(s)); };
  const end=async()=>{ await page.click("#play"); return wait(s=>s.ended&&s.done); };
  const r=await page.goto(URL);
  await wait(ready);
  const cfg=await page.evaluate(()=>fetch('/api/config').then(r=>r.json()));
  check("page served",r.ok());
  check("only SARJ v3 is exposed and active",cfg.models.length===1 && cfg.models[0].id==='v3' && cfg.models[0].available &&
    Object.keys((await snap()).sessions).join()==='v3' && (await snap()).listen==='v3');
  check("no legacy detector controls or visible comparisons",!/Baseline|P3/.test(await page.locator('body').innerText()) &&
    await page.locator('#card-baseline,#card-p3,#live-controller').count()===0);
  check("24 original scenarios",cfg.scenarios.length===24 && cfg.scenarios.every(s=>s.collection==='dev'));
  check("favicon does not fail",await page.evaluate(()=>fetch('/favicon.ico').then(r=>r.status))===204);
  check("live remains hidden without validation",await page.locator('#live-panel').isHidden());
  await select('NV-D01');
  await page.click('#play'); await wait(s=>s.playing); await sleep(1200);
  await page.click('#play'); await sleep(300+2*LATENCY);
  const paused=await snap(); await sleep(500); const paused2=await snap();
  check("pause freezes audio and detector feed",!paused.playing && paused2.fedMs===paused.fedMs && Math.abs(paused2.playheadMs-paused.playheadMs)<1);
  const cough=await end();
  check("cough keeps agent speaking",cough.events.v3[0].outcome==='kept' && !cough.decisions.v3.some(d=>d.decision==='STOP'));
  await page.click('#reset'); const reset=await wait(ready);
  check("reset creates fresh sole-v3 session",reset.session!==cough.session && reset.playheadMs===0 && reset.decisions.v3.length===0);
  await select('BC-D02'); const bc=await end();
  check("acknowledgements keep agent speaking",bc.events.v3.every(e=>e.credit===1) && !bc.decisions.v3.some(d=>d.decision==='STOP'));
  await select('GI-D01'); await page.click('#play');
  const stop=await wait(s=>s.decisions.v3.some(d=>d.decision==='STOP'));
  await sleep(200); const muted=await snap();
  check("v3 STOP mutes agent while caller continues",muted.agentMuted && muted.agentGain<0.05 && muted.callerGain===1);
  const genuine=await wait(s=>s.ended&&s.done);
  check("official event scorer confirms genuine interruption",genuine.events.v3[0].outcome==='yield');
  await page.screenshot({path:`${SHOTS}/v3-production.png`,fullPage:true});
  await page.setInputFiles('#upload',own.upload);
  const uploaded=await wait(s=>s.scenario?.startsWith('U')&&ready(s));
  const upEnd=await end();
  check("upload runs only v3 and has no official labels",Object.keys(upEnd.decisions).join()==='v3' && upEnd.events.v3.length===0 &&
    upEnd.decisions.v3.some(d=>d.decision==='STOP'));
  await select('GI-D01'); await page.click('#rec');
  await page.waitForFunction(()=>document.getElementById('rec').textContent==='Stop recording',null,{timeout:15000});
  const rec=await wait(s=>s.scenario?.startsWith('U') && s.scenario!==uploaded.scenario && ready(s));
  const recEnd=await end();
  check("microphone workflow runs only v3",Object.keys(recEnd.decisions).join()==='v3' && recEnd.decisions.v3.some(d=>d.decision==='STOP'));
  const phone=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await phone.goto(URL); await phone.waitForFunction(()=>window.sarjDemo?.snapshot().sessions.v3);
  check("mobile fits screen",await phone.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await phone.screenshot({path:`${SHOTS}/v3-phone.png`,fullPage:true});
  check("no browser errors",errors.length===0,errors.join(' | '));
  await browser.close();
  fs.unlinkSync(own.upload); fs.unlinkSync(own.mic); fs.rmdirSync(path.dirname(own.mic));
  const failures=results.filter(r=>!r.ok).length;
  console.log(`${results.length-failures}/${results.length} browser checks passed`);
  process.exit(failures?1:0);
})().catch(e=>{console.error(e);process.exit(2)});
