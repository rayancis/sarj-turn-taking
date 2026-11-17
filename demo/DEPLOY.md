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
