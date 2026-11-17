# Public demo site: Baseline vs P3

Live site: **https://sarj-turn-taking.onrender.com** (Render, free web service, Docker). It deploys branch
`feat/demo-ui` of the private repository; visitors reach only the HTTP routes below. Rollback point for the
previous site (E01b + 6 synthetic scenarios): branch `demo-rollback/e91e36e-before-p3` (commit `e91e36e`).

## Status (2026-11-17): the 24 originals are prepared, not yet public

The demo now shows only the 24 original competition recordings (`data/dev`), with Baseline and P3. This version is on
branch `feat/demo-p3-integration` and runs locally (`python demo/server.py`). The live site keeps the previous
version (50 Phase 3 scenarios, branch `feat/demo-ui`) until public hosting of the 24 is authorised: public mode
refuses to start without that permission, and never starts with no playable scenario, so deploying early cannot
expose the recordings or blank the site (Render keeps the running version when a new one fails to start).

**What the kit allows.** Three different things:

| Question | Answer from the kit |
|---|---|
| Are the voices AI-generated? | Yes: Gemini TTS voices, procedural noise (DATA.md). That does not make them free to republish: the scripts, labels, mix and dataset belong to the organisers, and the kit grants no licence. |
| Internal team use? | Yes. Supplied to participants; the owner approved storing them in this private repository (PROJECT.md); the judging rubric expects "a clear demo … with Arabic examples" in the teams' presentations (CHALLENGE.md). |
| Public web hosting? | **Not granted.** No clause in CHALLENGE.md, DATA.md, README.md or SUBMISSION.md licenses redistribution. "Public development set" means shared with all participants. §10.8 licenses *our* work to Sarj, not their data to us. |

**Authorization needed.** Written permission (email is enough) from Sarj AI, the challenge organisers, stating that
the team may host the 24 development recordings, with their labels and transcripts, on a public, non-commercial
website for its project presentation; with any attribution wording, time limit or conditions (e.g. no indexing).

**Switch-over, once permission is received** (on `feat/demo-p3-integration`):
1. `demo/collections.json`, collection `dev`: set `"public": true`; put who granted it, the date, the scope and a
   reference (e.g. the email subject) in `"authorization"`; update `"rights"` (it is shown on the public page).
2. `demo/Dockerfile`: add `COPY data/dev/manifest.json data/dev/` and `COPY data/dev/labels/ data/dev/labels/` and
   `COPY data/dev/audio/ data/dev/audio/`; adjust `test_image_contains_only_whitelisted_paths` accordingly.
3. `python -m pytest -q`, then `python demo/server.py --public` and `node demo/browser_check.js http://127.0.0.1:10000/`.
4. Fast-forward `feat/demo-ui` to the integration branch and push; Render redeploys in about a minute.

## What visitors get

A normal HTTPS link that opens in Chrome or Safari, on desktop or phone, with no install, account or sign-in.
They pick one of 50 scenarios, press Play, and both detectors run on the same audio, side by side:

| Detector | Code | Pinned SHA-256 of the file |
|---|---|---|
| **Baseline** | kit starter detector, `demo/models/baseline_bd2a3f0.py` = `git show bd2a3f0:my_system.py` | `85a8fa0c…` |
| **P3 Hybrid** | Phase 3 `my_system.py` + `neural_vad.py` + `models/silero_vad.onnx`, unchanged from `b32cfc2` | `8066ae36…` |

If a file does not match its pin the server disables that detector (and refuses to start in public mode), so a label
can never run other code. "Hear the agent as controlled by" chooses whose STOP silences the agent you hear; both
detectors always run.

## Scenario collections and rights (`demo/collections.json`)

| Collection | Count | Public? | Why |
|---|---|---|---|
| Competition dev set (`data/dev`) | 24 | **no**, local mode only | The kit grants no public redistribution licence; the owner's approval covers the private repository and team use. |
| Phase 3 xdev (`data/extra/xdev`) | 25 | yes, with attribution | ASC voice clips CC BY 4.0; ESC-50 sound clips individually CC0 or CC BY (cross-checked against the ESC-50 LICENSE file); generated room tone/echo/filters. |
| Phase 3 holdout (`data/extra/holdout`) | 25 | yes, with attribution | Same sources and licences. Listening only: never used to tune the detector. |

The Phase 3 scenarios are constructed: recordings of real people reading isolated Arabic keywords, mixed with
licensed sounds. They are not spontaneous conversations. Their WAVs were rebuilt with `tools/build_extra.py` and are
bit-identical to the Phase 3 manifests (`--verify`); they are committed so the site can serve them.

Public mode serves a collection only if `collections.json` marks it public, it is not the kit data, and every WAV's
SHA-256 is in the register's `public_wav_sha256` list. Credits are shown on the page (each scenario lists its clips).
ESC-50 as a compilation is CC BY-NC: **keep the site non-commercial** (no ads, no paid access).

## Public routes (everything else is 404)

`/`, `/app.js`, `/style.css`; `/api/config` (scenarios, the two detectors, recorded dev-set numbers; no file paths
or hashes); `/api/scenario/<id>`, `/api/audio/<id>.wav` (the 50 public scenarios only); `POST /api/session`,
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
