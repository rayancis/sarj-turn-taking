# E01b: robustness revision of E01 after independent review

| | |
|---|---|
| Branch | `claude/nice-hawking-okappk` (not merged) |
| Starts from | E01 `8f08311` (review verdict: REVISE) |
| Commits | `5fe0455` reproductions, `ad840f5` step A, `77fa27c` step B1, `e566234` step B2, `fd0a286` matrix tool |
| Evaluated code | `my_system.py` at `e566234` (unchanged in `fd0a286`) |
| Status | **candidate for a second independent review**; not self-approved |

## Review findings: reproduction and root cause

All three were reproduced with generated signals before any change (`tests/test_review_findings.py`, committed as
strict xfail in `5fe0455`, markers removed by the commit that fixes each).

| # | Finding | Reproduced on E01 | Root cause |
|---|---|---|---|
| 1 | Voice from the first frame never stops | sustained and syllabic voice at -25 dB over -65 dB noise: no STOP; same voice after 1 s of noise: STOP at 1180 ms | The floor was a low percentile of *every* frame heard; with no noise-only frames yet, the voice itself became the floor |
| 2 | Sparse bursts accumulate | voiced bursts 40/100 and 60/100 ms (28-38 % duty) stop at 2.5 s | E01's run rule kept a run alive across gaps <= 100 ms with no bound, so any duty cycle could add up to 200 ms |
| 3 | Noise step stops the agent | white and rumble steps -65 -> -30 dB stop 180 ms after the step | The percentile floor needs ~2.7 s to follow a step; meanwhile noise frames pass the level test, and with no RESUME the stop is final |

## Changes (one hypothesis each)

**A, finding 2 (`ad840f5`).** Bounded evidence: STOP when the last 300 ms of agent speech contain >= 200 ms of caller
speech frames. Keeps the baseline's 200 ms evidence and a 300 ms horizon, tolerates syllable dips up to 100 ms,
and requires >= 2/3 occupancy, so low-duty bursts cannot accumulate.

**B1, finding 1 (`77fa27c`).** The floor learns only from aperiodic frames. Periodicity: normalised, window-corrected
autocorrelation of the last 40 ms (300-3400 Hz, spectral magnitude compressed to the power 0.67 as in generalised
autocorrelation, pitch 75-500 Hz); voiced at >= 0.45. That threshold lies above every one of ~27,000 frames of
generated white, rumble and pink noise (max 0.40). The floor is trusted after 5 aperiodic frames; until then a frame
is speech only if it is periodic and louder than -42 dBFS (the baseline rule restricted to voiced frames).

**B2, finding 3 (`e566234`).** A STOP also needs >= 40 ms (2 frames) of periodic frames among the speech frames in
the window. Aperiodic level changes (noise steps, coughs, bangs) can then never stop the agent, however loud.

### Decisions made inside the revision (disclosed)

- **B2 at 60 ms was rejected for fragility, not score.** With 3 voiced frames required, GI-D03 «لحظة» in car noise
  (exactly 3 voiced frames) became a miss under a 7 ms time shift or added -50/-40 dB noise. Validation showed
  natural (TTS) speech is voiced in only ~50 % of loud frames by this measure (synthetic voice: ~100 %). With p≈0.5
  over >= 10 frames, the chance of fewer than k voiced frames is 5.5 % (k=3), 1.1 % (k=2), 0.1 % (k=1); k=2 is the
  largest requirement at ~1 % risk. At 40 ms the dev result is identical across shifts of 3/7/10/13/17 ms and added
  noise. Dev Core is the same for 60 and 40 ms (54.7), so the choice did not raise the score.
- **A "stationary periodic sound = background" rule (option C) was considered and not adopted.** It would help only a
  perfectly steady synthetic engine (dev car noise varies 7-12 dB within 300 ms and already reaches a trusted floor
  through its ~28 % aperiodic frames), and it would re-open finding 1 for a sustained voice. A perfectly steady
  periodic sound present from the first frame is therefore treated as possible speech; see limitations.

## Matched robustness matrix (`tools/robustness.py`, 61 generated cases, identical for every version)

| Version | Cases as intended |
|---|---|
| Baseline `bd2a3f0` | 29 / 61 |
| E01 `8f08311` | 36 / 61 |
| + A | 39 / 61 |
| + B1 | 41 / 61 |
| **+ B2 = E01b** | **48 / 61** |

Fixed by E01b: cold-start voices (steady, syllabic, speech-like; quiet and -40 dB noise), sparse bursts, coughs,
white/rumble steps to -35 and -25 dB, a voice 15 dB over the new level after a step. No case that passed in E01
fails in E01b except the two engine cases below.

Still failing in E01b (13): voice at 0/3/6 dB broadband SNR in white noise (6, also failing in E01); four laughter
models (also in E01); **engine drone from frame 0 and speech over it (2, regressions introduced by B1)**; engine
step -65 -> -35 (also in E01).

Full outputs: `experiments/evidence/robustness-{baseline-bd2a3f0,e01-8f08311,e01b-e566234}.txt`.

## Official real-time runs: speed 1.0, all 24 calls, same container, nothing else running

| | Baseline `bd2a3f0` | E01 `8f08311` | **E01b `e566234`** |
|---|---|---|---|
| **Core** | 41.04 | 44.05 | **54.72** |
| Responsiveness R | 0.806 | 1.000 | **1.000** |
| Restraint S | 0.219 | 0.233 | **0.331** |
| F | 0.345 | 0.378 | 0.497 |
| Missed interruptions | 2 / 7 | 0 / 7 | **0 / 7** |
| False-stop count (scorer) | 25 (3.48/min) | 18 (2.50/min) | **16 (2.23/min)** |
| STOP decisions sent | 25 | 23 | 20 |
| Stop latency median / p90 | 0 / 521 ms | 0 / 0 ms | 0 / 0 ms |
| Protocol errors / ignored | 0 / 0 | 0 / 0 | 0 / 0 |
| Max harness send lag | 1.87 ms | 0.01 ms | 2.04 ms |
| RTT median / max | 0.61 / 2.20 ms | 0.75 / 1.65 ms | 0.98 / 3.57 ms |
| Decision arrival after frame send | 0.9-2.6 ms | 1.0-2.3 ms | 1.1-3.4 ms |
| `on_frame` mean / p99 | ~6 / - us | 77 / 147 us | 139 / 282 us (0.7 % of a frame) |
| End-of-turn (separate) | 28.52 | 28.52 | 28.52 |

The E01b real-time run produced the same 37 decisions, in the same order, as the offline replay of the same code.
Raw logs, scores, reports, hashes and run configuration: `experiments/evidence/` (`runs.json` indexes them).

### Per-event changes, E01 -> E01b

| Event | E01 | E01b | Mechanism |
|---|---|---|---|
| NV-D01 cough (KEEP) | false stop | **kept** | B2: no voicing in a cough |
| NV-D02 laughter (KEEP) | false stop | **kept** | A: bursts no longer accumulate |
| GI-D02 stray stop at 5.10 s (street swell) | stop | **gone** | B2 |
| BG-D01 stray stops (TV voice) | 1 (0.64 s) | **2 (0.50 s, 6.42 s)** | B1: a voice present from the first frame is treated as speech; TV voice frames no longer raise the floor |

No STOP event changed: all 7 interruptions are caught with full credit in both. Restraint rises in GI (0.67 -> 1.00)
and NV (0 -> 0.44). The dev synthetic laughter is kept, but four generated laughter models still stop, so laughter
is **not** solved in general.

### Dev perturbations (offline, official scorer)

| Perturbation | E01 Core / misses | E01b Core / misses |
|---|---|---|
| none | 44.1 / 0 | 54.7 / 0 |
| shift +3, +7, +10, +13, +17 ms | 44.1-48.8 / 0 | 54.7 (all) / 0 |
| + white noise -50 / -40 dBFS on caller | 48.8 / 53.3, 0 | 54.7 / 57.5, 0 |
| gain -6 / +6 dB | 48.8 / 42.1, 0 | 57.5 / 49.8, 0 |
| gain -12 dB | 67.0 / **1 miss** | 67.0 / **1 miss** |

Dev scores depend strongly on input gain for every version (baseline: 34.0 at +6 dB, 66.3 at -12 dB). The absolute
-42 dBFS gate acts as a microphone-gain assumption: at -12 dB quiet backchannels fall below it (S rises) and one
interruption is missed. This is the largest open generalisation risk and is not addressed here.

## Decision: KEEP E01b as the new candidate (pending independent review and the user's decision)

- Fixes all three confirmed findings with tests; matrix 36 -> 48/61; real-time Core 44.05 -> 54.72 with R kept at
  1.00, misses 0, protocol errors 0, latency unchanged (decisions 1-3 ms after the frame).
- Each step is a separate commit with its own hypothesis and measurements, and can be reverted on its own.
- Main trade-off to review: B1 makes the detector treat any loud periodic sound heard before a noise estimate exists
  as speech (synthetic engine from frame 0, dev TV voice). That is the price of fixing finding 1; the alternative
  (option C) re-opens it.

## Limitations

- **Low SNR (finding 4) is not fixed.** Speech at 0-6 dB broadband SNR in white noise is still missed; the relative
  level test (>= 10 dB over the floor) is the limit, and the periodicity measure itself weakens below ~6 dB.
- **Gain dependence:** see the -42 dBFS gate above.
- **Periodic backgrounds:** engines, music, TV and echo are periodic; B2 does not reject them, and B1 makes the
  untrusted start more sensitive to them. A periodic noise step still stops the agent.
- **Laughter and backchannels** (BC, HT filler, OV, DV) remain the dominant Restraint errors; they need cues beyond
  level and periodicity.
- The voicing measure is less sensitive on natural speech (~50 % of frames) than on synthetic voices; its
  behaviour on human recordings and narrowband codecs is unverified.
- 24 synthetic dev calls, 7 STOP events, script-derived labels: the +10.7 Core over E01 is mainly 2 KEEP events and
  2 stray stops; it is evidence of direction, not of size.
