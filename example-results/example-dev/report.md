# Turn-taking benchmark report: T00-example

> **Labels: script_derived.** Development labels are derived from the scripts of synthetic recordings and are not yet validated by native listeners.

## Headline

| Core score | Responsiveness R | Restraint S | F (harmonic) | Ambiguity A | End-of-turn (separate track) |
|---|---|---|---|---|---|
| **58.6** / 100 | 0.83 | 0.40 | 0.54 | 1.00 | 28.3 |

- Stop latency after the intent was evident: median 166 ms, p90 1394 ms (6 yields)
- Missed interruptions: 1 of 7 · false stops: 16 (2.23 per minute of agent speech) · hiccups (stop + quick RESUME): 0
- Network round trip: median 0.75 ms, max 1.46 ms
- Protocol errors: 0 · decisions outside agent speech (ignored): 0

## By category

| Category | R | S | genuine stops caught | backchannels/noise kept | scenarios with no stray stop |
|---|---|---|---|---|---|
| AM Ambiguous | 0.84 | 1.00 | 1/1 | 0/0 | 3/3 |
| BC Backchannel | – | 0.20 | 0/0 | 1/5 | 3/3 |
| BG Background | – | 0.33 | 0/0 | 2/4 | 2/3 |
| DV Dialect variation | 1.00 | 0.33 | 1/1 | 1/2 | 2/3 |
| GI Genuine interruption | 0.48 | 0.67 | 2/3 | 0/0 | 2/3 |
| HT Hesitation | – | 0.00 | 0/0 | 0/1 | 3/3 |
| NV Caller non-speech / echo | – | 0.67 | 0/0 | 2/3 | 3/3 |
| OV Overlap | 1.00 | 0.00 | 2/2 | 0/1 | 3/3 |

## Slices

| Slice | scenarios | R | S | F |
|---|---|---|---|---|
| najdi | 12 | 0.85 | 0.64 | 0.73 |
| other_dialects | 12 | 0.50 | 0.24 | 0.32 |
| clean_audio | 13 | 0.82 | 0.64 | 0.72 |
| noisy_or_phone | 11 | 0.67 | 0.44 | 0.53 |
| scripted | 24 | 0.83 | 0.40 | 0.54 |

Dialect gap (Najdi F − other F): 0.41 · noise gap (clean F − noisy F): 0.19

## Every event

| Scenario | Event | Category | Expected | Outcome | Credit | What the agent did |
|---|---|---|---|---|---|---|
| GI-D01 | GI-D01.e1 | GI | STOP | yield | 0.45 | stopped at 18444 ms (1394 ms after evident) |
| GI-D02 | GI-D02.e1 | GI | STOP | miss | 0.00 | did not stop in time (or resumed over the caller) |
| GI-D03 | GI-D03.e1 | GI | STOP | yield | 0.98 | stopped at 13022 ms (332 ms after evident) |
| BC-D01 | BC-D01.e1 | BC | KEEP | kept | 1.00 | kept speaking |
| BC-D02 | BC-D02.e1 | BC | KEEP | false_stop | 0.00 | false stop at 13982 ms |
| BC-D02 | BC-D02.e2 | BC | KEEP | false_stop | 0.00 | false stop at 18223 ms |
| BC-D02 | BC-D02.e3 | BC | KEEP | false_stop | 0.00 | false stop at 22382 ms |
| BC-D03 | BC-D03.e1 | BC | KEEP | false_stop | 0.00 | false stop at 15902 ms |
| HT-D01 | HT-D01.e1 | HT | TAKE_TURN | eot | 0.95 | TAKE_TURN 712 ms after turn end |
| HT-D02 | HT-D02.e1 | HT | TAKE_TURN | eot | 0.94 | TAKE_TURN 742 ms after turn end |
| HT-D03 | HT-D03.e1 | HT | KEEP | false_stop | 0.00 | false stop at 14101 ms |
| BG-D01 | BG-D01.e1 | BG | KEEP | false_stop | 0.00 | agent was already silent (stopped at 6302 ms) |
| BG-D02 | BG-D02.e1 | BG | KEEP | kept | 1.00 | kept speaking |
| BG-D02 | BG-D02.e2 | BG | KEEP | kept | 1.00 | kept speaking |
| BG-D03 | BG-D03.e1 | BG | KEEP | false_stop | 0.00 | false stop at 11881 ms |
| NV-D01 | NV-D01.e1 | NV | KEEP | kept | 1.00 | kept speaking |
| NV-D02 | NV-D02.e1 | NV | KEEP | kept | 1.00 | kept speaking |
| NV-D03 | NV-D03.e1 | NV | KEEP | false_stop | 0.00 | false stop at 8664 ms |
| AM-D01 | AM-D01.e1 | AM | EITHER | kept | 1.00 | kept speaking |
| AM-D02 | AM-D02.e1 | AM | STOP | yield | 0.84 | stopped at 13482 ms (622 ms after evident) |
| AM-D03 | AM-D03.e1 | AM | EITHER | yield | 1.00 | stopped at 15042 ms |
| OV-D01 | OV-D01.e1 | OV | STOP | yield | 1.00 | stopped at 10622 ms (0 ms after evident) |
| OV-D02 | OV-D02.e1 | OV | KEEP | false_stop | 0.00 | false stop at 11402 ms |
| OV-D03 | OV-D03.e1 | OV | STOP | yield | 1.00 | stopped at 12542 ms (0 ms after evident) |
| DV-D01 | DV-D01.e1 | DV | KEEP | kept | 1.00 | kept speaking |
| DV-D02 | DV-D02.e1 | DV | KEEP | false_stop | 0.00 | agent was already silent (stopped at 13361 ms) |
| DV-D03 | DV-D03.e1 | DV | STOP | yield | 1.00 | stopped at 11482 ms (0 ms after evident) |

## Stops when nothing was happening

- GI-D02: at 4343, 10903 ms (scenario time)
- BG-D01: at 562, 2083, 6302 ms (scenario time)
- DV-D02: at 13361 ms (scenario time)

Scoring rules: SCORING.md. Times are scenario time in ms, measured when your decision reached the harness.
