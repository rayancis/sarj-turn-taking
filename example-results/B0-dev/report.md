# Turn-taking benchmark report: B0-never-stop

> **Labels: script_derived.** Development labels are derived from the scripts of synthetic recordings and are not yet validated by native listeners.

## Headline

| Core score | Responsiveness R | Restraint S | F (harmonic) | Ambiguity A | End-of-turn (separate track) |
|---|---|---|---|---|---|
| **8.0** / 100 | 0.00 | 1.00 | 0.00 | 0.80 | not attempted |

- Stop latency after the intent was evident: median – ms, p90 – ms (0 yields)
- Missed interruptions: 7 of 7 · false stops: 0 (0.00 per minute of agent speech) · hiccups (stop + quick RESUME): 0
- Network round trip: median 0.61 ms, max 1.59 ms
- Protocol errors: 0 · decisions outside agent speech (ignored): 0

## By category

| Category | R | S | genuine stops caught | backchannels/noise kept | scenarios with no stray stop |
|---|---|---|---|---|---|
| AM Ambiguous | 0.00 | 1.00 | 0/1 | 0/0 | 3/3 |
| BC Backchannel | – | 1.00 | 0/0 | 5/5 | 3/3 |
| BG Background | – | 1.00 | 0/0 | 4/4 | 3/3 |
| DV Dialect variation | 0.00 | 1.00 | 0/1 | 2/2 | 3/3 |
| GI Genuine interruption | 0.00 | 1.00 | 0/3 | 0/0 | 3/3 |
| HT Hesitation | – | 1.00 | 0/0 | 1/1 | 3/3 |
| NV Caller non-speech / echo | – | 1.00 | 0/0 | 3/3 | 3/3 |
| OV Overlap | 0.00 | 1.00 | 0/2 | 1/1 | 3/3 |

## Slices

| Slice | scenarios | R | S | F |
|---|---|---|---|---|
| najdi | 12 | 0.00 | 1.00 | 0.00 |
| other_dialects | 12 | 0.00 | 1.00 | 0.00 |
| clean_audio | 13 | 0.00 | 1.00 | 0.00 |
| noisy_or_phone | 11 | 0.00 | 1.00 | 0.00 |
| scripted | 24 | 0.00 | 1.00 | 0.00 |

Dialect gap (Najdi F − other F): 0.00 · noise gap (clean F − noisy F): 0.00

## Every event

| Scenario | Event | Category | Expected | Outcome | Credit | What the agent did |
|---|---|---|---|---|---|---|
| GI-D01 | GI-D01.e1 | GI | STOP | miss | 0.00 | did not stop in time (or resumed over the caller) |
| GI-D02 | GI-D02.e1 | GI | STOP | miss | 0.00 | did not stop in time (or resumed over the caller) |
| GI-D03 | GI-D03.e1 | GI | STOP | miss | 0.00 | did not stop in time (or resumed over the caller) |
| BC-D01 | BC-D01.e1 | BC | KEEP | kept | 1.00 | kept speaking |
| BC-D02 | BC-D02.e1 | BC | KEEP | kept | 1.00 | kept speaking |
| BC-D02 | BC-D02.e2 | BC | KEEP | kept | 1.00 | kept speaking |
| BC-D02 | BC-D02.e3 | BC | KEEP | kept | 1.00 | kept speaking |
| BC-D03 | BC-D03.e1 | BC | KEEP | kept | 1.00 | kept speaking |
| HT-D01 | HT-D01.e1 | HT | TAKE_TURN | eot | 0.00 | no TAKE_TURN after the caller finished |
| HT-D02 | HT-D02.e1 | HT | TAKE_TURN | eot | 0.00 | no TAKE_TURN after the caller finished |
| HT-D03 | HT-D03.e1 | HT | KEEP | kept | 1.00 | kept speaking |
| BG-D01 | BG-D01.e1 | BG | KEEP | kept | 1.00 | kept speaking |
| BG-D02 | BG-D02.e1 | BG | KEEP | kept | 1.00 | kept speaking |
| BG-D02 | BG-D02.e2 | BG | KEEP | kept | 1.00 | kept speaking |
| BG-D03 | BG-D03.e1 | BG | KEEP | kept | 1.00 | kept speaking |
| NV-D01 | NV-D01.e1 | NV | KEEP | kept | 1.00 | kept speaking |
| NV-D02 | NV-D02.e1 | NV | KEEP | kept | 1.00 | kept speaking |
| NV-D03 | NV-D03.e1 | NV | KEEP | kept | 1.00 | kept speaking |
| AM-D01 | AM-D01.e1 | AM | EITHER | kept | 1.00 | kept speaking |
| AM-D02 | AM-D02.e1 | AM | STOP | miss | 0.00 | did not stop in time (or resumed over the caller) |
| AM-D03 | AM-D03.e1 | AM | EITHER | kept | 0.60 | kept speaking |
| OV-D01 | OV-D01.e1 | OV | STOP | miss | 0.00 | did not stop in time (or resumed over the caller) |
| OV-D02 | OV-D02.e1 | OV | KEEP | kept | 1.00 | kept speaking |
| OV-D03 | OV-D03.e1 | OV | STOP | miss | 0.00 | did not stop in time (or resumed over the caller) |
| DV-D01 | DV-D01.e1 | DV | KEEP | kept | 1.00 | kept speaking |
| DV-D02 | DV-D02.e1 | DV | KEEP | kept | 1.00 | kept speaking |
| DV-D03 | DV-D03.e1 | DV | STOP | miss | 0.00 | did not stop in time (or resumed over the caller) |

Scoring rules: SCORING.md. Times are scenario time in ms, measured when your decision reached the harness.
