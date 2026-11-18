# E03: SARJ v3 cold-start false STOP (BG-D02)

| | |
|---|---|
| Branch | `claude/great-galileo-uonrdh`, from Codex handoff `8253301` |
| Commits | `feba8ee` first attempt (rejected), `a8c93bb` fix, this record |
| Evaluated source | `demo/models/sarj_v3` at `a8c93bb`, revision `v3-coldseed-1` |
| Status | **candidate for independent Codex review**; not self-approved; not merged, not deployed |

## Problem

Gemini Live greets first, so v3 can hear the agent before it has heard the line or the caller ("cold
start"). `tests/test_demo_live.py::test_real_v3_worker_over_realtime_wire_for_quiet_speech_and_background[BG-D02]`
streams BG-D02's caller channel from the start of the agent turn: v3 issued STOP after ~1.1 s
(stream 8.59 s) although only street traffic is present. Reproduced on Linux.

## Root cause

The frontend's minimum-statistics noise floor started at about -90 dB per band and may rise only 3 dB/s.
Street noise (about -50 dB) therefore read as SNR 36 dB, above the 18 dB "strong speech" threshold, for
more than 10 s. In the 24 benchmark calls this is hidden because v3 hears 300 ms of the line before the
first agent turn and learns a "caller level" there.

A second finding came out of the first attempt: because of the same slow floor, that early "caller level"
is mostly line noise (GI-D03: -43.8 dB, against -26.9 dB once the floor is correct). v3's thresholds
(caller-relative level, the -6 dB stop-word gate) are tuned in that regime.

## Attempt 1, rejected (`feba8ee`)

The floor starts at the first caller frame of every call. Acoustics-only replay improved warm and cold false
stops, but the corrected caller level moved GI-D03's quiet stop word ("لحظة") to the edge of the -6 dB word
gate. Official runs on this host missed GI-D03 twice (Core 95.09, 94.83). A same-host control with the
original floor caught it (96.50). The change reduced sensitivity to a real interruption, so it was rejected.

## Fix (`a8c93bb`)

Seed the floor from the line only at a cold start: when the agent starts and v3 has not learned the caller's
level (`caller_ref_db is None`). For 1 s after a seed, the floor drops straight to the quietest frame, so a
caller already talking is measured from their first pause. Calls heard before the agent speaks keep exactly
the tuned behaviour. Config: `cold_start_noise_seed` (False = original), `cold_seed_acquire_ms`.

Rejected on the way, with measurements in `tools/v3_cold_start.py`: requiring extra speech while the caller
level is unknown (+300 to +1200 ms) cut cold hits from 5 to 0–2; holding the floor while the agent talks traded
warm hits 5 → 4.

## Results

**Acoustics-only replay, all 24 calls, from the longest agent turn** (`python tools/v3_cold_start.py`; the
Whisper layer needs real time and is not included):

| | warm hits / false / missed | cold hits / false / missed |
|---|---|---|
| original floor | 4 / 3 / 3 | 5 / 11 / 2 |
| `a8c93bb` | 4 / 3 / 3, identical decisions in all 24 calls | 5 / 8 / 2; BG-D02 no STOP |

GI-D03 cold is 80 ms past the acoustic window. In real time with Whisper it still stops on the word
(12.90–12.94 s, onset 12.24 s, several runs).

**Official real-time harness, speed 1.0, all 24 calls, same Linux host** (raw runs local; hashes in
`experiments/evidence/runs.json`):

| Run | Source | Core | R | S | Misses | False stops | Latency median / p90 | Max send lag |
|---|---|---|---|---|---|---|---|---|
| `v3-a8c93bb-rt` | a8c93bb | **96.38** | 0.951 | 0.969 | 0/7 | 1 (BG-D01) | 251.5 / 871.9 ms | 0.03 ms |
| `v3-a8c93bb-rt2` | a8c93bb (repeat) | 97.17 | 0.968 | 0.969 | 0/7 | 1 (BG-D01) | 252.0 / 872.0 ms | 0.03 ms |
| `v3-feba8ee-legacyfloor-rt` | original floor (control) | 96.50 | 0.954 | 0.969 | 0 | 1 | | 21.17 ms (caution) |
| `v3-feba8ee-rt`, `-rt2` | rejected attempt | 95.09 / 94.83 | | 1.000 | 1 (GI-D03) | 0 | | 0.02 ms |

Both `a8c93bb` runs: 29,651/29,651 frames, 0 protocol errors, 0 unattributed or ignored decisions, and the
unmodified scorer recomputes the saved score exactly (`tools/verify_v3_run.py`). The 0.8-point spread between
the two repeats is Whisper timing jitter on the same source. Comparable with Codex's Windows run of `8a63799`
(96.34) and the teammate's record (96.38). The BG-D01 false stop predates this change.

Host: Linux 6.18 x86_64, 4 vCPU Intel Xeon @ 2.30 GHz, Python 3.13.18, numpy 2.5.5, websockets 17.4,
faster-whisper 1.2.1, CTranslate2 4.9.0, Whisper snapshot `d90ca5fe` (all six SHA-256 verified), data
manifest `93a23bcd…9366`. The production image uses Python 3.12.10, not 3.13; harness numbers come from the venv.

**Tests:** 174 passed on Linux (full repository suite, including the BG-D02 live-wire test and
`tests/test_replay.py`, which had failed on Windows timing). New: `tests/test_v3_cold_start.py` (8) and
`tests/test_v3_benchmark_record.py` (2).

## Known cost and limits

- A caller already speaking when the agent starts (words with 0.2 s pauses) is stopped at ~1.4 s instead of
  0.7 s: the floor must hear a first pause. The old 0.7 s came from treating any sound as speech. A voice with
  no pause at all cannot be told from steady noise by level alone.
- Cold start still has 8 acoustic false stops across the 24 calls (side talk, backchannels, TV), against
  11 before. Without the caller's level, v3 cannot tell a soft background voice from the caller. That is the
  remaining Live risk during the greeting.
- The deeper issue is untouched: v3's caller level is learned partly from noise in every call, and the thresholds
  are tuned to that. Recalibrating needs data beyond these 24 synthetic calls.

## Linux / Docker

`demo/Dockerfile` was built on Linux from a scratch copy (Dockerfile unchanged in the repository) with two
sandbox-only differences: the base image fetched by the **same digest** from `mirror.gcr.io` (Docker Hub
returned 429), and the sandbox proxy CA plus apt over HTTPS (the proxy refuses plain HTTP). Result: pinned
packages installed, `pip check` clean, all six Whisper hashes verified in the image, `libgomp1`
12.2.0-14+deb12u1, Python 3.12.10, unprivileged uid 10001, read-only model directory. The server starts in
public mode with v3 `v3-coldseed-1` available and Live hidden.

Container under limits (paced like a browser playhead, look-ahead 800 ms):

| Limit | Memory | GI-D03 (needs the Whisper word) | Other checks |
|---|---|---|---|
| 512 MB, 0.5 CPU | 209 MB idle, ~250 MB in use | 0/3 stopped | BG-D02 kept |
| 512 MB, 1 CPU | | 0/2 | |
| 512 MB, 2 CPU | 261 MB with 4 sessions | 4/7 single; 0/1 with 4 concurrent | GI-D01, OV-D03 stopped; BG-D02 kept |
| 2 CPU, original floor | | 5/8 | same distribution: not caused by this fix |

**Release blocker:** `render.yaml` uses `plan: free`, which Render documents as about 0.1 CPU; that limit was
not measured here. At 0.5 CPU the words layer already arrives too late for GI-D03 every time. Even at 2 CPUs
the demo's batched, look-ahead feeding gives GI-D03 only about a 50–60% chance. The official harness on 4 vCPU
caught it every time.

## Gemini Live

Only the protocol-faithful local fake was exercised (BG-D02 and GI-D01 over the real WebSocket path: both
correct). No genuine Google connection was made: the key is to stay in Render settings only, and no test
service exists. `LIVE_V3_VERIFIED` stays `0`.
