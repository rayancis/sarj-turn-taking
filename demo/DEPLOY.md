# Public demo site: Baseline vs P3

Live site: **https://sarj-turn-taking.onrender.com** (Render, free web service, Docker). It deploys branch
`feat/demo-ui` of the private repository; visitors reach only the HTTP routes below. Rollback point for the
previous site (E01b + 6 synthetic scenarios): branch `demo-rollback/e91e36e-before-p3` (commit `e91e36e`).

## The 24 original recordings: public with the supervisor's permission

The demo shows exactly the 24 original competition recordings (`data/dev`, the kit's own files, unchanged), with
Baseline and P3. The 50 Phase 3 research scenarios stay in the repository (`data/extra`) and are not in the demo
or the image.

| Question | Answer |
|---|---|
| Are the voices AI-generated? | Yes: Gemini TTS voices and procedural noise (DATA.md). That alone does not license republication; the kit itself grants none. |
| Internal team use? | Yes: supplied to participants; stored in this private repository with the owner's approval (PROJECT.md). |
| Public web hosting? | **Permitted since 2026-11-17** by written message from the SARJ competition supervisor: «نعم، مسموح نشر او عرض التسجيلات على رابط عام لانه غير حقيقية ولغرض الاختبار فقط» ("Yes, publishing or displaying the recordings on a public link is permitted, because they are not real and are for testing only"). Recorded in `demo/collections.json` (`dev.authorization`), without names or contact details; the screenshot is kept by the repository owner. Scope: this non-commercial test/presentation demo, not other reuse. |

Public mode serves the competition recordings only while that record exists, serves only the kit's original files
(SHA-256 as in `data/dev/manifest.json`; a changed file stops the server), and never starts with no playable scenario.
Previous live version (50 Phase 3 scenarios): branch `demo-rollback/4fe3f8c-phase3-scenarios`.

## Test your own audio

Visitors can **record** (the agent of the selected scenario plays while they speak, so they can interrupt it) or
**upload** an audio file (the caller side; they choose when it starts). Their audio becomes the caller channel; the
agent channel and the agent's timing come from the selected original scenario. Both detectors then run on it
exactly as on the 24 scenarios (tested: uploading a scenario's own caller channel reproduces its decisions exactly).
There are no labels, so no verdict is shown.

- The page converts the audio to 16 kHz mono 16-bit (what the detectors receive) and sends it once (`POST /api/custom`).
- The server keeps it **in memory only** (never on disk, never logged), under an unguessable id that is not listed
  anywhere, and drops it after 15 minutes; at most 20 at once; 20 new ones per minute in total; at most 60 s each.
- The page asks for microphone permission only when Record is pressed (`Permissions-Policy: microphone=(self)`).
  Headphones are recommended so the agent's voice does not leak into the microphone. Recording needs HTTPS (the
  Render site) or localhost, and a current Chrome, Edge, Firefox or Safari (AudioWorklet).

## Live AI Conversation (Gemini Live, barge-in decided by SARJ)

A real-time Arabic voice conversation with a Gemini Live agent (`gemini-3.8-live` by default: the current default
Live model, free of charge on the free tier per Google's pricing page, Nov 2026). **SARJ, not Gemini, decides when
the caller has interrupted the agent.**

How control is split (`demo/live.py`):
- Gemini runs with automatic activity detection **disabled** (`realtimeInputConfig.automaticActivityDetection.disabled`,
  `activityHandling: START_OF_ACTIVITY_INTERRUPTS`): it cannot detect speech or interrupt itself.
- The page plays Gemini's audio (24 kHz) and, in the same AudioWorklet clock, streams 20 ms kit frames
  `[agent audio actually played, microphone]` (16 kHz PCM16) to the server, where P3 (controller by default) and
  Baseline (shadow) run on identical frames.
- While the agent speaks, the microphone goes only to SARJ. On the controller's STOP the server tells the page to
  empty its playback queue, sends `activityStart` to Gemini (cancelling any generation still running) and the
  caller's audio from a 1.5 s pre-roll. No STOP (a backchannel, a cough): Gemini never hears the caller.
- While the agent is silent, a level gate opens the caller's turn (`activityStart` + 0.4 s pre-roll) and the
  controller's end-of-turn output (TAKE_TURN, the kit's 700 ms rule) closes it (`activityEnd`; fallback 1.5 s of
  quiet, max 20 s).
- Evidence shown after each conversation and in the event log: STOP decisions of both detectors, barge-ins
  executed, how long after the STOP the played agent audio went silent, and Gemini `interrupted` messages without a
  preceding SARJ signal (must be 0).

Tested (Nov 2026) against a protocol-faithful local stand-in (`tests/fake_gemini.py`), **not yet against the real
Gemini service** (no key in the test environment): unit equivalence with the offline replay (6 interruption
scenarios: same STOP frame), backchannels/coughs (nothing reaches Gemini), Baseline shadow and controller switch,
wire-level barge-in and backchannel, limits, origin check, quota pause; browser runs with a fake microphone
(`demo/browser_check_live.js`): STOP at 8.96-9.00 s, played agent audio silent 20 ms later.

Limits (env vars, defaults): `LIVE_MAX_CONCURRENT=2`, `LIVE_MAX_SECONDS=180`, `LIVE_PER_HOUR=30`, `LIVE_PER_DAY=100`
(in memory: a restart resets them). If Gemini reports exhausted quota the feature pauses for 15 minutes. Without
`GEMINI_API_KEY` the section explains that it is not configured and nothing connects. Only same-origin pages can
open `/api/live`. The key is read from the environment and sent to Google in the `x-goog-api-key` header; it never
reaches the browser, a URL or the repository.

Privacy: in this mode the caller's microphone audio (on their turns and after a STOP) and the conversation go,
through this server, to Google's Gemini API. On the free tier Google may use the content to improve its products.
The page says so and requires the visitor's confirmation before starting. Nothing is stored here.

Known limits: if the caller uses loudspeakers, the agent's voice reaches the microphone (browser echo
cancellation may not cover Web Audio playback); headphones are recommended. When Gemini has already finished
generating (it generates faster than real time), its context holds the whole reply even though the caller heard
only part of it; the system instruction tells it to assume the rest was not heard.

### Setup (once)

1. **Google AI Studio** (https://aistudio.google.com): sign in, **Get API key → Create API key** (a free-tier
   project; do not enable billing). Copy the key.
2. **Test it on your laptop first** (PowerShell, in the repository, branch `feat/gemini-live-agent`):
   ```powershell
   $env:PYTHONUTF8 = '1'; $env:GEMINI_API_KEY = Read-Host 'Gemini API key'
   .\.venv\Scripts\python.exe demo\server.py
   ```
   Open http://127.0.0.1:8770, use headphones, tick the consent box, **Start conversation**, speak Arabic, and
   interrupt the agent while it talks. The summary at the end must show "of which 0 without an activity signal".
3. **Render** (only after step 2 works and the branch is merged into `feat/demo-ui`): the service → **Environment** →
   **Add Environment Variable** → key `GEMINI_API_KEY`, value: the key → **Save Changes** (Render redeploys).
   Optional: `GEMINI_LIVE_MODEL` (e.g. `gemini-3.1-flash-live-preview`), and the `LIVE_*` limits above.

## What visitors get

A normal HTTPS link that opens in Chrome or Safari, on desktop or phone, with no install, account or sign-in.
They pick one of the 24 original competition scenarios, press Play, and both detectors run on the same audio, side by side:

| Detector | Code | Pinned SHA-256 of the file |
|---|---|---|
| **Baseline** | kit starter detector, `demo/models/baseline_bd2a3f0.py` = `git show bd2a3f0:my_system.py` | `85a8fa0c…` |
| **P3 Hybrid** | Phase 3 `my_system.py` + `neural_vad.py` + `models/silero_vad.onnx`, unchanged from `b32cfc2` | `8066ae36…` |

If a file does not match its pin the server disables that detector (and refuses to start in public mode), so a label
can never run other code. "Hear the agent as controlled by" chooses whose STOP silences the agent you hear; both
detectors always run.

## Scenario collections and rights (`demo/collections.json`)

| Collection | Count | In the demo? | Rights |
|---|---|---|---|
| Competition dev set (`data/dev`) | 24 | **yes**, locally and on the public site | Public display permitted by the competition supervisor (2026-11-17), recorded above. |
| Phase 3 xdev (`data/extra/xdev`) | 25 | no (research only) | ASC voice clips CC BY 4.0; ESC-50 clips individually CC0 / CC BY. Kept with their rights review. |
| Phase 3 holdout (`data/extra/holdout`) | 25 | no (research only) | Same sources and licences. Frozen; never used to tune the detector. |

The Phase 3 files (WAVs rebuilt bit-exactly by `tools/build_extra.py`, manifests, labels, design, sources and
results) stay in the repository unchanged; they are simply not part of the demo or its image.

## Public routes (everything else is 404)

`/`, `/app.js`, `/style.css`; `/api/config` (scenarios, the two detectors, recorded dev-set numbers; no file paths
or hashes); `/api/scenario/<id>`, `/api/audio/<id>.wav` (the 24 original scenarios only); `POST /api/session`,
`/api/session/<id>/advance`, `/close`; `/healthz`, `/robots.txt` (no indexing).

## Limits (public mode)

16 detector sessions at once (8 simultaneous playbacks: each runs Baseline and P3; env `SARJ_DEMO_MAX_SESSIONS`),
80 new sessions per minute in total, 20 playhead posts per second per session, detectors at most 1.5x real time,
2 KB request bodies, 64 connections, idle playbacks dropped after 10 minutes. Memory about 150 MB with the ONNX
model loaded; P3 costs about 0.2-0.3 ms of CPU per 20 ms frame, Baseline about 0.02 ms.

The page runs the detectors up to 0.8 s ahead of what you hear and applies each decision at the frame time where
the detector made it. This visualisation is **not** a real-time network latency benchmark.

## Updating the site

Every push to `feat/demo-ui` redeploys automatically (Render: Auto-Deploy on commit; 3-5 minutes).

- **Run the checks first**: `python -m pytest -q` (all suites), then optionally
  `python demo/server.py --public` and `node demo/browser_check.js http://127.0.0.1:10000/`.
- **A new P3 version** (after its own review): bring its files onto the demo branch, then update `P3_SHA256` (and the
  revision) in `demo/server.py` and the `p3` row in `demo/benchmark.json` from its recorded run in
  `experiments/evidence/runs.json`. The tests check the pin against that run.
- **Scenario audio**: `python tools/build_extra.py` then `python tools/build_extra.py --verify`; a changed WAV is
  refused publicly until its new SHA-256 is reviewed and added to `demo/collections.json`.
- **Roll back**: Render dashboard > the service > Events > Rollback, or push the rollback branch:
  `git push origin demo-rollback/e91e36e-before-p3:feat/demo-ui --force-with-lease` (ask the repository owner first).

## First-time setup (done)

The service was created on Render from this repository: Docker, branch `feat/demo-ui`, Dockerfile `./demo/Dockerfile`,
context `.`, health check `/healthz`, free instance. The free plan sleeps after about 15 minutes without visitors and
takes about a minute to wake; open the link a minute before a presentation.
