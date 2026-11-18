# Turn-taking benchmark report: my-team

> **Labels: script_derived.** Development labels are derived from the scripts of synthetic recordings and are not yet validated by native listeners.

## In plain words

- ✗ HT-D01: the caller finished and you never took the turn.
- ✗ HT-D02: the caller finished and you never took the turn.
- ✗ BG-D01: background sound. You stopped, but the caller did not want the floor.
- ~ GI-D02: a real interruption. You stopped 0.9 s after it was clear (full credit needs 0.3 s).
- ~ AM-D02: an ambiguous word. You stopped 0.5 s after it was clear (full credit needs 0.3 s).
- ✓ GI-D01: a real interruption. You stopped 0.3 s after it was clear.
- ✓ GI-D03: a real interruption. You stopped 0.3 s after it was clear.
- ✓ BC-D01: a backchannel ("mm-hm", "ايه"). You kept talking, correctly.
- ✓ BC-D02: a backchannel ("mm-hm", "ايه"). You kept talking, correctly.
- ✓ BC-D02: a backchannel ("mm-hm", "ايه"). You kept talking, correctly.
- ✓ BC-D02: a backchannel ("mm-hm", "ايه"). You kept talking, correctly.
- ✓ BC-D03: a backchannel ("mm-hm", "ايه"). You kept talking, correctly.
- ✓ HT-D03: a hesitation. You kept talking, correctly.
- ✓ BG-D02: background sound. You kept talking, correctly.
- ✓ BG-D02: background sound. You kept talking, correctly.
- ✓ BG-D03: background sound. You kept talking, correctly.
- ✓ NV-D01: a cough, laugh or echo. You kept talking, correctly.
- ✓ NV-D02: a cough, laugh or echo. You kept talking, correctly.
- ✓ NV-D03: a cough, laugh or echo. You kept talking, correctly.
- ✓ AM-D01: an ambiguous word; either choice is acceptable here, you kept talking.
- ✓ AM-D03: an ambiguous word; either choice is acceptable here, you stopped.
- ✓ OV-D01: both starting at once. You stopped 0.0 s after it was clear.
- ✓ OV-D02: both starting at once. You kept talking, correctly.
- ✓ OV-D03: both starting at once. You stopped 0.0 s after it was clear.
- ✓ DV-D01: a dialect case. You kept talking, correctly.
- ✓ DV-D02: a dialect case. You kept talking, correctly.
- ✓ DV-D03: a dialect case. You stopped 0.3 s after it was clear.

## Headline

| Core score | Responsiveness R | Restraint S | F (harmonic) | Ambiguity A | End-of-turn (separate track) |
|---|---|---|---|---|---|
| **96.4** / 100 | 0.95 | 0.97 | 0.96 | 1.00 | not attempted |

- Stop latency after the intent was evident: median 252 ms, p90 871 ms (7 yields)
- Missed interruptions: 0 of 7 · false stops: 1 (0.14 per minute of agent speech) · hiccups (stop + quick RESUME): 0
- Network round trip: median 1.07 ms, max 4.61 ms
- Protocol errors: 0 · decisions outside agent speech (ignored): 0

## By category

| Category | R | S | genuine stops caught | backchannels/noise kept | scenarios with no stray stop |
|---|---|---|---|---|---|
| AM Ambiguous | 0.90 | 1.00 | 1/1 | 0/0 | 3/3 |
| BC Backchannel | – | 1.00 | 0/0 | 5/5 | 3/3 |
| BG Background | – | 0.75 | 0/0 | 3/4 | 3/3 |
| DV Dialect variation | 1.00 | 1.00 | 1/1 | 2/2 | 3/3 |
| GI Genuine interruption | 0.90 | 1.00 | 3/3 | 0/0 | 3/3 |
| HT Hesitation | – | 1.00 | 0/0 | 1/1 | 3/3 |
| NV Caller non-speech / echo | – | 1.00 | 0/0 | 3/3 | 3/3 |
| OV Overlap | 1.00 | 1.00 | 2/2 | 1/1 | 3/3 |

## Slices

| Slice | scenarios | R | S | F |
|---|---|---|---|---|
| najdi | 12 | 0.97 | 1.00 | 0.98 |
| other_dialects | 12 | 0.86 | 0.95 | 0.90 |
| clean_audio | 13 | 1.00 | 1.00 | 1.00 |
| noisy_or_phone | 11 | 0.88 | 0.96 | 0.92 |
| scripted | 24 | 0.95 | 0.97 | 0.96 |

Dialect gap (Najdi F − other F): 0.08 · noise gap (clean F − noisy F): 0.08

## Every event

| Scenario | Event | Category | Expected | Outcome | Credit | What the agent did |
|---|---|---|---|---|---|---|
| GI-D01 | GI-D01.e1 | GI | STOP | yield | 1.00 | stopped at 17302 ms (252 ms after evident) |
| GI-D02 | GI-D02.e1 | GI | STOP | yield | 0.71 | stopped at 16901 ms (871 ms after evident) |
| GI-D03 | GI-D03.e1 | GI | STOP | yield | 1.00 | stopped at 12942 ms (252 ms after evident) |
| BC-D01 | BC-D01.e1 | BC | KEEP | kept | 1.00 | kept speaking |
| BC-D02 | BC-D02.e1 | BC | KEEP | kept | 1.00 | kept speaking |
| BC-D02 | BC-D02.e2 | BC | KEEP | kept | 1.00 | kept speaking |
| BC-D02 | BC-D02.e3 | BC | KEEP | kept | 1.00 | kept speaking |
| BC-D03 | BC-D03.e1 | BC | KEEP | kept | 1.00 | kept speaking |
| HT-D01 | HT-D01.e1 | HT | TAKE_TURN | eot | 0.00 | no TAKE_TURN after the caller finished |
| HT-D02 | HT-D02.e1 | HT | TAKE_TURN | eot | 0.00 | no TAKE_TURN after the caller finished |
| HT-D03 | HT-D03.e1 | HT | KEEP | kept | 1.00 | kept speaking |
| BG-D01 | BG-D01.e1 | BG | KEEP | false_stop | 0.00 | false stop at 14602 ms |
| BG-D02 | BG-D02.e1 | BG | KEEP | kept | 1.00 | kept speaking |
| BG-D02 | BG-D02.e2 | BG | KEEP | kept | 1.00 | kept speaking |
| BG-D03 | BG-D03.e1 | BG | KEEP | kept | 1.00 | kept speaking |
| NV-D01 | NV-D01.e1 | NV | KEEP | kept | 1.00 | kept speaking |
| NV-D02 | NV-D02.e1 | NV | KEEP | kept | 1.00 | kept speaking |
| NV-D03 | NV-D03.e1 | NV | KEEP | kept | 1.00 | kept speaking |
| AM-D01 | AM-D01.e1 | AM | EITHER | kept | 1.00 | kept speaking |
| AM-D02 | AM-D02.e1 | AM | STOP | yield | 0.90 | stopped at 13363 ms (503 ms after evident) |
| AM-D03 | AM-D03.e1 | AM | EITHER | yield | 1.00 | stopped at 15423 ms |
| OV-D01 | OV-D01.e1 | OV | STOP | yield | 1.00 | stopped at 11263 ms (23 ms after evident) |
| OV-D02 | OV-D02.e1 | OV | KEEP | kept | 1.00 | kept speaking |
| OV-D03 | OV-D03.e1 | OV | STOP | yield | 1.00 | stopped at 12662 ms (22 ms after evident) |
| DV-D01 | DV-D01.e1 | DV | KEEP | kept | 1.00 | kept speaking |
| DV-D02 | DV-D02.e1 | DV | KEEP | kept | 1.00 | kept speaking |
| DV-D03 | DV-D03.e1 | DV | STOP | yield | 1.00 | stopped at 12189 ms (299 ms after evident) |

Scoring rules: SCORING.md. Times are scenario time in ms, measured when your decision reached the harness.
