# Turn-taking benchmark report: B1-any-sound

> **Labels: script_derived.** Development labels are derived from the scripts of synthetic recordings and are not yet validated by native listeners.

## Headline

| Core score | Responsiveness R | Restraint S | F (harmonic) | Ambiguity A | End-of-turn (separate track) |
|---|---|---|---|---|---|
| **41.0** / 100 | 0.81 | 0.22 | 0.34 | 1.00 | 28.5 |

- Stop latency after the intent was evident: median 0 ms, p90 522 ms (5 yields)
- Missed interruptions: 2 of 7 · false stops: 25 (3.48 per minute of agent speech) · hiccups (stop + quick RESUME): 0
- Network round trip: median 0.66 ms, max 1.67 ms
- Protocol errors: 0 · decisions outside agent speech (ignored): 0

## By category

| Category | R | S | genuine stops caught | backchannels/noise kept | scenarios with no stray stop |
|---|---|---|---|---|---|
| AM Ambiguous | 0.89 | 1.00 | 1/1 | 0/0 | 3/3 |
| BC Backchannel | – | 0.20 | 0/0 | 1/5 | 3/3 |
| BG Background | – | 0.00 | 0/0 | 0/4 | 1/3 |
| DV Dialect variation | 1.00 | 0.00 | 1/1 | 0/2 | 2/3 |
| GI Genuine interruption | 0.33 | 0.33 | 1/3 | 0/0 | 1/3 |
| HT Hesitation | – | 0.00 | 0/0 | 0/1 | 3/3 |
| NV Caller non-speech / echo | – | 0.22 | 0/0 | 1/3 | 2/3 |
| OV Overlap | 1.00 | 0.00 | 2/2 | 0/1 | 3/3 |

## Slices

| Slice | scenarios | R | S | F |
|---|---|---|---|---|
| najdi | 12 | 0.80 | 0.57 | 0.67 |
| other_dialects | 12 | 0.50 | 0.07 | 0.12 |
| clean_audio | 13 | 1.00 | 0.57 | 0.73 |
| noisy_or_phone | 11 | 0.44 | 0.29 | 0.35 |
| scripted | 24 | 0.81 | 0.22 | 0.34 |

Dialect gap (Najdi F − other F): 0.54 · noise gap (clean F − noisy F): 0.38

## Every event

| Scenario | Event | Category | Expected | Outcome | Credit | What the agent did |
|---|---|---|---|---|---|---|
| GI-D01 | GI-D01.e1 | GI | STOP | yield | 1.00 | stopped at 16841 ms (0 ms after evident) |
| GI-D02 | GI-D02.e1 | GI | STOP | miss | 0.00 | did not stop in time (or resumed over the caller) |
| GI-D03 | GI-D03.e1 | GI | STOP | miss | 0.00 | did not stop in time (or resumed over the caller) |
| BC-D01 | BC-D01.e1 | BC | KEEP | kept | 1.00 | kept speaking |
| BC-D02 | BC-D02.e1 | BC | KEEP | false_stop | 0.00 | false stop at 13661 ms |
| BC-D02 | BC-D02.e2 | BC | KEEP | false_stop | 0.00 | agent was already silent (stopped at 13661 ms) |
| BC-D02 | BC-D02.e3 | BC | KEEP | false_stop | 0.00 | agent was already silent (stopped at 13661 ms) |
| BC-D03 | BC-D03.e1 | BC | KEEP | false_stop | 0.00 | false stop at 15802 ms |
| HT-D01 | HT-D01.e1 | HT | TAKE_TURN | eot | 0.99 | TAKE_TURN 612 ms after turn end |
| HT-D02 | HT-D02.e1 | HT | TAKE_TURN | eot | 0.98 | TAKE_TURN 642 ms after turn end |
| HT-D03 | HT-D03.e1 | HT | KEEP | false_stop | 0.00 | false stop at 14002 ms |
| BG-D01 | BG-D01.e1 | BG | KEEP | false_stop | 0.00 | agent was already silent (stopped at 6422 ms) |
| BG-D02 | BG-D02.e1 | BG | KEEP | false_stop | 0.00 | agent was already silent (stopped at 7662 ms) |
| BG-D02 | BG-D02.e2 | BG | KEEP | false_stop | 0.00 | agent was already silent (stopped at 7662 ms) |
| BG-D03 | BG-D03.e1 | BG | KEEP | false_stop | 0.00 | false stop at 11761 ms |
| NV-D01 | NV-D01.e1 | NV | KEEP | false_stop | 0.00 | false stop at 11622 ms |
| NV-D02 | NV-D02.e1 | NV | KEEP | kept | 1.00 | kept speaking |
| NV-D03 | NV-D03.e1 | NV | KEEP | false_stop | 0.00 | false stop at 6042 ms |
| AM-D01 | AM-D01.e1 | AM | EITHER | kept | 1.00 | kept speaking |
| AM-D02 | AM-D02.e1 | AM | STOP | yield | 0.89 | stopped at 13382 ms (522 ms after evident) |
| AM-D03 | AM-D03.e1 | AM | EITHER | yield | 1.00 | stopped at 14942 ms |
| OV-D01 | OV-D01.e1 | OV | STOP | yield | 1.00 | stopped at 10522 ms (0 ms after evident) |
| OV-D02 | OV-D02.e1 | OV | KEEP | false_stop | 0.00 | false stop at 10982 ms |
| OV-D03 | OV-D03.e1 | OV | STOP | yield | 1.00 | stopped at 12441 ms (0 ms after evident) |
| DV-D01 | DV-D01.e1 | DV | KEEP | false_stop | 0.00 | false stop at 9462 ms |
| DV-D02 | DV-D02.e1 | DV | KEEP | false_stop | 0.00 | agent was already silent (stopped at 7542 ms) |
| DV-D03 | DV-D03.e1 | DV | STOP | yield | 1.00 | stopped at 11381 ms (0 ms after evident) |

## Stops when nothing was happening

- GI-D02: at 601, 8622 ms (scenario time)
- GI-D03: at 482, 6681 ms (scenario time)
- BG-D01: at 481, 6422 ms (scenario time)
- BG-D02: at 481, 7662 ms (scenario time)
- NV-D03: at 1762 ms (scenario time)
- DV-D02: at 482, 7542 ms (scenario time)

Scoring rules: SCORING.md. Times are scenario time in ms, measured when your decision reached the harness.
