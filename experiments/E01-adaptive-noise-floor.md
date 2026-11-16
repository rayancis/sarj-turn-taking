# E01: adaptive noise floor and relative speech evidence for STOP

| | |
|---|---|
| Branch / code commit | `claude/nice-hawking-okappk` / `8f08311` (replay tool: `012d330`) |
| Compared with | original baseline `my_system.py` at `bd2a3f0` (identical to kit `0915ace`) |
| Data | dev 0.1.0-synthetic, manifest sha256 `93a23bcd…5029366`, 24 calls, 7 STOP events |
| Environment | Linux cloud container, 4 vCPU, Python 3.13.18, numpy 2.5.5, websockets 17.4 |
| Status | **candidate, awaiting independent Codex review**; not merged |

## Hypothesis

Phase-1 diagnosis: 10 of the baseline's 19 wrong STOPs came from steady caller-channel noise (car, street, TV
bed) whose level is above the fixed -42 dBFS speech threshold (median floor -38.8 to -34.2 dB in five calls).
The agent stopped about 200 ms into each utterance, which also made the two missed interruptions (GI-D02,
GI-D03): the agent was already silent when the caller spoke. Judging caller sound relative to a tracked noise
floor should remove these stops without slowing the reaction to a real caller.

## Implementation (`my_system.py`)

1. Band level: 300-3400 Hz energy of each 20 ms caller frame (Hann window, FFT).
2. Noise floor: 10th percentile of the band levels of the previous 3 s (current frame excluded); cleared at
   `scenario_start`; no decision until 100 ms of history.
3. Speech frame with hysteresis: start at >= 10 dB above the floor, continue at >= 6 dB; every speech frame
   must also exceed the baseline's absolute -42 dBFS (full band).
4. Speech run: a dip of <= 100 ms does not end it; only speech frames add evidence.
5. STOP at 200 ms of speech in a run while the agent talks (the baseline's evidence amount); one STOP per
   utterance; no RESUME. TAKE_TURN path unchanged.

Parameter values were set from general reasoning before the dev run (telephone band; percentile floor over a
few seconds; ~10 dB onset margin, far above the +/-1 dB frame-to-frame spread of steady noise; ~100 ms
syllable dips) and were not changed after seeing dev results.

### Revision inside the experiment (disclosed)

The first version (v1) required 200 ms of *consecutive* speech frames, as the baseline did. The synthetic
regression test "voice onset at 12 dB SNR" failed on v1: between syllables the level fell to 4-5 dB above the
floor for 60-80 ms, so runs reached only 160-180 ms. The 100 ms dip rule (step 4) was added for that reason,
i.e. from a generated signal, not from a dev label. v1 is exactly `MAX_GAP_MS = 0` (verified: identical
decisions), so it remains reproducible as an ablation.

## Tests

- `tests/test_my_system.py` (17 tests, generated signals only): steady white noise at -34 dB and rumble at
  -30 dB (both above the old fixed level) never stop; noise from the first frame with the agent starting at
  100 ms; voice onset at 12 and 20 dB SNR stops within 300 ms; quiet-room onset; < 200 ms sound; voice below
  -42 dBFS ignored; syllable dips bridged; bursts with 150 ms gaps not accumulated; digital zeros; no STOP
  while the agent is silent; one STOP per utterance and a new utterance can stop again; `scenario_start`
  clears the floor (with a guard test showing the old floor would fire without the reset) and a leftover stop.
- `tests/test_replay.py`: offline replay and a real WebSocket run of `my_system.py` (subprocess, 4x speed)
  give the same decision sequence.
- Full suite: **45 passed** (26 original kit tests + 19 new).

## Results: official real-time runs, speed 1.0, all 24 calls, same container

| | Baseline `bd2a3f0` | E01 `8f08311` | Change |
|---|---|---|---|
| **Core** | 41.04 | **44.05** | +3.01 |
| Responsiveness R | 0.806 | **1.000** | +0.194 |
| Restraint S | 0.219 | **0.233** | +0.014 |
| F | 0.345 | 0.378 | +0.033 |
| Ambiguity A | 1.00 | 1.00 | 0 |
| Missed interruptions | 2 / 7 | **0 / 7** | -2 |
| False-stop count (scorer) | 25 (3.48/min) | **18 (2.50/min)** | -7 |
| STOP decisions sent | 25 | 23 | -2 |
| Stop latency median / p90 | 0 / 521 ms (5 yields) | 0 / 0 ms (7 yields) | |
| Protocol errors / ignored decisions | 0 / 0 | 0 / 0 | |
| Max harness send lag | 1.87 ms | 17.72 ms (see limitations) | |
| RTT median / max | 0.61 / 2.20 ms | 0.96 / 2.24 ms | |
| Decision arrival after frame send | 0.9-2.6 ms | 1.1-4.7 ms | |
| `on_frame` cost | ~6 us | mean 77 us, p99 147 us | 0.4 % of a frame |
| End-of-turn (separate track) | 28.52 | 28.52 | 0 |

The real-time E01 run produced the same 40 decisions, in the same order, as the offline replay of the same
commit (`tools/replay.py`).

### Per-event changes

| Event | Baseline | E01 | Reason |
|---|---|---|---|
| GI-D02.e1 STOP | miss | yield, full credit | noise stops at 0.6 s / 8.6 s removed |
| GI-D03.e1 STOP | miss | yield, full credit | noise stops at 0.5 s / 6.7 s removed |
| AM-D02.e1 STOP | 0.89 (521 ms) | 1.00 (stop at 12.0 s, before evident) | dip rule joins «طيب؟» |
| **NV-D02.e1 KEEP** (laughter) | kept | **false stop at 12.56 s** | **regression**: dip rule joins laugh bursts |

Unchanged outcome but different cause: BG-D01 (TV speech), BG-D02 e1/e2 (horn at 11.46 s), DV-D02 (caller's
«هيه… هيه» at 13.64 s) still fail, but now on the actual sound instead of "agent already silent" after a noise
stop. Stray stops: removed in GI-D03, BG-D02, DV-D02; GI-D02 2 -> 1 (street swell at 5.10 s); BG-D01 2 -> 1
(TV at 0.64 s); NV-D03 echo stop at 1.76 s unchanged.

Category Restraint is a product (KEEP mean x stray-stop item), so removing a noise stop only raises S where the
category's KEEP events then pass. In BG and DV the KEEP events are transients, TV and backchannels, so S stays
0 there; this is why the Phase-1 "remove noise stops" ceiling (Core 62.6) was optimistic.

## Offline ablation and sensitivity (diagnostic, nothing selected from it)

| Variant (one change from E01) | Core | R | S | Misses |
|---|---|---|---|---|
| E01 as committed | 44.1 | 1.000 | 0.233 | 0 |
| v1, `MAX_GAP_MS = 0` | 47.1 | 0.973 | 0.261 | 0 |
| `MAX_GAP_MS = 60 / 140` | 47.3 / 44.1 | 1.000 | 0.261 / 0.233 | 0 |
| `SPEECH_ON_DB = 8 / 12` | 43.5 / 48.8 | 0.917 / 1.000 | 0.233 / 0.275 | 1 / 0 |
| `SPEECH_OFF_DB = 4 / 8` | 44.1 / 48.8 | 1.000 | 0.233 / 0.275 | 0 |
| `FLOOR_WINDOW_MS = 2000 / 5000` | 48.8 / 44.1 | 1.000 | 0.275 / 0.233 | 0 |
| `FLOOR_PERCENTILE = 5 / 20` | 43.5 / 48.8 | 0.917 / 1.000 | 0.233 / 0.275 | 1 / 0 |

Every neighbour beats the baseline (43.5-48.8 vs 41.0), so the direction of the effect is robust. The size is
not: +/-3 Core comes from single events flipping (mostly NV-D02 and BC/AM single stops). Higher-scoring
neighbours were **not** adopted; choosing them would be fitting 24 synthetic calls.

## Decision: KEEP (pending Codex review and the user's decision)

- Meets the preliminary targets: Core and Restraint up, R >= 0.80 (1.00), misses 2 -> 0, zero protocol
  errors, no added STOP latency (decisions arrive 1-5 ms after the frame).
- The core mechanism (relative floor) is a clear, explainable win. The dip rule is a deliberate trade:
  robustness to low-SNR speech (synthetic test) against one more false stop on bursty non-speech (dev
  laughter). It is the main open question for review; v1 stays available as `MAX_GAP_MS = 0`.

## Limitations

- Dev is 24 synthetic calls with script-derived labels and 7 STOP events; +3 Core is within the size of one or
  two events. Generalisation to human audio is not established.
- Non-stationary noise is not solved: TV speech, horns, street swells still cause stops. A sudden rise in
  steady noise is treated as speech until the floor adapts (about 2.7 s with a 3 s window, 10th percentile).
- If a caller talks continuously from the first frames of a call, the cold-start floor is speech level.
- Echo (NV-D03), backchannels, fillers, coughs/laughter and side speech are untouched by design.
- Max harness send lag in the E01 run was 17.7 ms (under the kit's 20 ms limit, vs 1.9 ms in the baseline
  run). The harness is a separate process; a short analysis script ran concurrently for part of the run, the
  likely cause. One repeat run without concurrent work would settle it.
- Measured on a Linux cloud container, not the team's Windows laptop or the venue network.
