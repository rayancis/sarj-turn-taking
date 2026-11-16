# Interactive demo (team presentation, work in progress)

A local web page that plays a development call and shows what the **real detector** decides, as it happens.
It is a demonstration aid, not the competition submission, and it changes nothing in the kit, the data or
`my_system.py`.

## Run it on Windows (PowerShell, from the repository folder)

Uses the existing environment from PROJECT.md (Python 3.12, `requirements-lock.txt`); nothing new to install.

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe demo\server.py
```

Then open **http://127.0.0.1:8770** in Chrome (the default browser opens automatically; `--no-browser` turns
that off). Stop with Ctrl+C. If port 8770 is busy: `demo\server.py --port 8771`.

No virtual environment yet? Create it once, as in PROJECT.md:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
```

## Using it

1. Pick a **scenario** (grouped by category; each shows what its labels expect) and a **detector**.
2. **Play / Pause** (or Space) and **Reset**. Changing scenario or detector starts a fresh detector.
3. When the detector sends STOP, the agent's audio is muted for the rest of that utterance; the caller keeps playing.
   The status line says when the agent spoke an utterance to the end because no STOP came.
4. The right panel shows the labels (ground truth, never given to the detector) and the official scorer's verdict
   for each event, revealed once nothing later in the stream can change it.

Good pairs to show: **NV-D01** (a cough: E01b keeps talking, Baseline stops at 11.62 s), **GI-D02** (street noise:
E01b stops for the caller at 14.32 s, Baseline stops at 0.60 s on the noise and misses the interruption),
**BG-D01** (TV voice: both still stop; a known E01b limitation).

## Detector versions

| Menu entry | Source | Follows later work? |
|---|---|---|
| E01b / current | `my_system.py` in the working tree | yes: edit or check out a new version and restart the server |
| Baseline | `git show bd2a3f0:my_system.py` (needs `git` on PATH) | no, pinned |

Add any other version without code changes, for example:
`demo\server.py --model e01=git:8f08311:my_system.py --model draft=file:experiments\draft_system.py`.
Each file is imported unchanged and must define `MySystem`, as `my_system.py` does.

## How a decision reaches the page

The browser plays both channels from one audio clock and posts its playhead every ~15 ms. For each 20 ms frame
the playhead has reached, the server makes exactly the calls the kit's harness and server would: the control
messages from `ttbench.harness.build_stream` (same order, same arguments), then the 1,280-byte frame parsed with
`np.frombuffer`, into `MySystem.on_frame`. Returned decisions pass the kit's `validate_client_message`, are stamped
with their frame time (`20 n` ms, as `tools/replay.py` does), and go back to the page. Labels are read only by the
official `ttbench.score.score_scenario`, after the decisions exist.

The detector always receives the original recording: muting the agent changes only what you hear (open loop, as in
the official evaluation). Pause stops the clock and the feed together; the detector's timeline is frame-based, so a
pause cannot shift it.

## What the numbers mean

- **Detector decisions**: live output of the selected version. Identical, decision for decision, to
  `tools/replay.py` on all 24 dev calls (tested), and to the recorded real-time harness runs in `experiments/evidence/`.
- **Timing**: detector compute per frame and how long after its frame time the page showed a decision. Local
  replay timing only: it is **not** network latency and not an official latency score.
- **Development-set benchmark**: Core / R / S read from `experiments/evidence/runs.json` (earlier real-time runs,
  24 synthetic calls). Not live scores and not hidden-test performance.

## Limits (intentional for this MVP)

- One call per session. The official run streams all 24 calls in one session; for E01b and Baseline the per-call
  decisions are the same as in those full runs (checked), but a future system that carries state across calls could differ.
- No seeking (it would require re-feeding the detector from the start); use Reset.
- Agent-mute reconstruction: silent from STOP to the end of the utterance, or until RESUME, as the scorer assumes.
- The page reacts within one poll (~15-40 ms) of the frame; your audio device adds its own output delay.
- Localhost only (`127.0.0.1`), because the dev audio must not be exposed. Do not share the page over a network.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_demo.py    # demo tests (part of the full suite)
node demo\browser_check.js                                       # optional; needs Node + Playwright, server running
```
