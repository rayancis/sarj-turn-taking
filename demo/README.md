# Interactive demo: Baseline vs P3 (team presentation, work in progress)

A web page that plays a scenario and shows, side by side, what the **real** Baseline and P3 detectors decide on the
same audio. Public site: https://sarj-turn-taking.onrender.com (setup, rights, limits, updates: [DEPLOY.md](DEPLOY.md)).
It is a demonstration aid, not the competition submission, and it changes no detector, kit or evaluation file.

- **Detectors:** Baseline (kit starter, `bd2a3f0`) and P3 Hybrid (Phase 3, `b32cfc2`), each pinned by file hash.
- **Scenarios:** locally, the 24 competition dev calls (private, never public) plus the 50 Phase 3 constructed
  scenarios; the public site serves only the 50 (real human keyword recordings + licensed sounds, with credits).
- Good ones to show: **NV-D51** (cough: Baseline stops, P3 keeps talking), **GI-D51** (genuine interruption: P3
  stops), **BC-D53** (acknowledgement), **GI-H54** (a soft interruption P3 is known to handle late or miss).

## Run it on Windows (PowerShell, from the repository folder)

Uses the environment from PROJECT.md (Python 3.12) plus onnxruntime for P3 (`demo\requirements-public.txt`).

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m pip install -r demo\requirements-public.txt
.\.venv\Scripts\python.exe demo\server.py
```

Then open **http://127.0.0.1:8770** in Chrome (the browser opens automatically; `--no-browser` turns that off).
Stop with Ctrl+C. If port 8770 is busy: `demo\server.py --port 8771`. Local mode lists the 24 competition calls
(private) and the 50 Phase 3 scenarios (their WAVs are committed; `python tools\build_extra.py --verify` checks them).

## Using it

1. Pick a **scenario** (grouped by collection and category; each has a short explanation and expected behaviour).
2. **Play / Pause** (or Space) and **Reset**. Both detectors run on every playback, each in a fresh session.
3. **Hear the agent as controlled by** chooses whose STOP silences the agent you hear (P3 or Baseline); you can switch
   during playback. The two cards and the timeline lanes show both detectors' real decisions all the time.
4. The ground-truth panel shows the labels (never given to the detectors) and the official scorer's verdict for each
   detector, revealed once nothing later can change it, plus a one-line comparison.

## How a decision reaches the page

The browser plays both channels from one audio clock and posts its playhead. For each 20 ms frame the playhead has
reached (plus up to 0.8 s look-ahead on the hosted site), the server makes, for each detector, exactly the calls the
kit's harness and server would: the control messages from `ttbench.harness.build_stream` (same order, same
arguments), then the 1,280-byte frame parsed with `np.frombuffer`, into `MySystem.on_frame`. Decisions pass the kit's
`validate_client_message`, are stamped with their frame time (`20 n` ms, as `tools/replay.py` does), and the page
applies them when playback reaches that time. Labels are read only by the official `ttbench.score.score_scenario`.

The detectors always receive the original recording: muting the agent changes only what you hear (open loop, as in
the official evaluation). Pause stops the clock and the feed together.

## What the numbers mean

- **Decisions**: live output of the two pinned detectors; identical to `tools/replay.py` on every scenario (tested), to
  the recorded real-time runs on the 24 dev calls, and (P3) to the recorded Phase 3 xdev result.
- **Timing**: detector compute per frame and when the page applied each decision. With look-ahead this is **not** a
  real-time network latency benchmark and not an official latency score.
- **Official dev-set results**: Baseline 41.04 vs P3 73.41 Core, from `experiments/evidence/runs.json` (24 original
  dev recordings, real-time harness). Not from the 50 additional scenarios; not hidden-test performance.

## Limits

- One call per session (the official run streams all 24 calls in one session; both detectors reset per call).
- No seeking; use Reset. Agent silence is reconstructed as the scorer does: from STOP to the end of the utterance.
- P3 is experimental: occasional delayed responses and missed quiet interruptions on the additional data.
- Local mode is localhost only (`127.0.0.1`), because the competition audio must not be exposed. The public site
  (`--public`) serves only the collections marked public in `demo/collections.json`.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_demo.py tests\test_demo_public.py
node demo\browser_check.js                                   # optional; needs Node + Playwright, server running
```
