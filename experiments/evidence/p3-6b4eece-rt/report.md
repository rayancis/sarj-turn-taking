# Turn-taking benchmark report: my-team

> **Labels: script_derived.** Development labels are derived from the scripts of synthetic recordings and are not yet validated by native listeners.

## In plain words

- ✗ BC-D02: a backchannel ("mm-hm", "ايه"). You stopped, but the caller did not want the floor.
- ✗ BC-D02: a backchannel ("mm-hm", "ايه"). You stopped, but the caller did not want the floor.
- ✗ BC-D02: a backchannel ("mm-hm", "ايه"). You stopped, but the caller did not want the floor.
- ✗ BC-D03: a backchannel ("mm-hm", "ايه"). You stopped, but the caller did not want the floor.
- ✗ HT-D01: you took the turn at 15.1 s while the caller was only pausing.
- ✗ HT-D01: you took the turn at 19.4 s while the caller was still talking.
- ✗ HT-D02: you took the turn at 20.7 s while the caller was only pausing.
- ✗ HT-D02: you took the turn at 24.5 s while the caller was only pausing.
- ✗ HT-D02: you took the turn at 19.6 s while the caller was still talking.
- ✗ BG-D01: background sound. You stopped, but the caller did not want the floor.
- ✗ OV-D02: both starting at once. You stopped, but the caller did not want the floor.
- ✗ DV-D01: a dialect case. You stopped, but the caller did not want the floor.
- ✗ DV-D02: a dialect case. You stopped, but the caller did not want the floor.
- ✗ BG-D01: you stopped at 0.5 s when nothing was happening.
- ✗ NV-D03: you stopped at 1.8 s when nothing was happening.
- ~ DV-D03: a dialect case. You stopped 0.4 s after it was clear (full credit needs 0.3 s).
- ✓ GI-D01: a real interruption. You stopped 0.0 s after it was clear.
- ✓ GI-D02: a real interruption. You stopped 0.0 s after it was clear.
- ✓ GI-D03: a real interruption. You stopped 0.0 s after it was clear.
- ✓ BC-D01: a backchannel ("mm-hm", "ايه"). You kept talking, correctly.
- ✓ HT-D03: a hesitation. You kept talking, correctly.
- ✓ BG-D02: background sound. You kept talking, correctly.
- ✓ BG-D02: background sound. You kept talking, correctly.
- ✓ BG-D03: background sound. You kept talking, correctly.
- ✓ NV-D01: a cough, laugh or echo. You kept talking, correctly.
- ✓ NV-D02: a cough, laugh or echo. You kept talking, correctly.
- ✓ NV-D03: a cough, laugh or echo. You kept talking, correctly.
- ✓ AM-D01: an ambiguous word; either choice is acceptable here, you kept talking.
- ✓ AM-D02: an ambiguous word. You stopped 0.0 s after it was clear.
- ✓ AM-D03: an ambiguous word; either choice is acceptable here, you stopped.
- ✓ OV-D01: both starting at once. You stopped 0.0 s after it was clear.
- ✓ OV-D03: both starting at once. You stopped 0.0 s after it was clear.

## Headline

| Core score | Responsiveness R | Restraint S | F (harmonic) | Ambiguity A | End-of-turn (separate track) |
|---|---|---|---|---|---|
| **73.4** / 100 | 0.99 | 0.55 | 0.70 | 1.00 | 28.5 |

- Stop latency after the intent was evident: median 0 ms, p90 352 ms (7 yields)
- Missed interruptions: 0 of 7 · false stops: 10 (1.39 per minute of agent speech) · hiccups (stop + quick RESUME): 0
- Network round trip: median 0.88 ms, max 1.82 ms
- Protocol errors: 0 · decisions outside agent speech (ignored): 0

## By category

| Category | R | S | genuine stops caught | backchannels/noise kept | scenarios with no stray stop |
|---|---|---|---|---|---|
| AM Ambiguous | 1.00 | 1.00 | 1/1 | 0/0 | 3/3 |
| BC Backchannel | – | 0.20 | 0/0 | 1/5 | 3/3 |
| BG Background | – | 0.50 | 0/0 | 3/4 | 2/3 |
| DV Dialect variation | 0.97 | 0.00 | 1/1 | 0/2 | 3/3 |
| GI Genuine interruption | 1.00 | 1.00 | 3/3 | 0/0 | 3/3 |
| HT Hesitation | – | 1.00 | 0/0 | 1/1 | 3/3 |
| NV Caller non-speech / echo | – | 0.67 | 0/0 | 3/3 | 2/3 |
| OV Overlap | 1.00 | 0.00 | 2/2 | 0/1 | 3/3 |

## Slices

| Slice | scenarios | R | S | F |
|---|---|---|---|---|
| najdi | 12 | 1.00 | 0.79 | 0.88 |
| other_dialects | 12 | 0.99 | 0.48 | 0.64 |
| clean_audio | 13 | 0.99 | 0.79 | 0.88 |
| noisy_or_phone | 11 | 1.00 | 0.50 | 0.67 |
| scripted | 24 | 0.99 | 0.55 | 0.70 |

Dialect gap (Najdi F − other F): 0.24 · noise gap (clean F − noisy F): 0.21

## Every event

| Scenario | Event | Category | Expected | Outcome | Credit | What the agent did |
|---|---|---|---|---|---|---|
| GI-D01 | GI-D01.e1 | GI | STOP | yield | 1.00 | stopped at 16882 ms (0 ms after evident) |
| GI-D02 | GI-D02.e1 | GI | STOP | yield | 1.00 | stopped at 14442 ms (0 ms after evident) |
| GI-D03 | GI-D03.e1 | GI | STOP | yield | 1.00 | stopped at 12582 ms (0 ms after evident) |
| BC-D01 | BC-D01.e1 | BC | KEEP | kept | 1.00 | kept speaking |
| BC-D02 | BC-D02.e1 | BC | KEEP | false_stop | 0.00 | false stop at 13822 ms |
| BC-D02 | BC-D02.e2 | BC | KEEP | false_stop | 0.00 | agent was already silent (stopped at 13822 ms) |
| BC-D02 | BC-D02.e3 | BC | KEEP | false_stop | 0.00 | agent was already silent (stopped at 13822 ms) |
| BC-D03 | BC-D03.e1 | BC | KEEP | false_stop | 0.00 | false stop at 15822 ms |
| HT-D01 | HT-D01.e1 | HT | TAKE_TURN | eot | 0.99 | TAKE_TURN at 15081 ms during a pause the caller had not finished; TAKE_TURN at 19422 ms while the caller was speaking; TAKE_TURN 612 ms after turn end |
| HT-D02 | HT-D02.e1 | HT | TAKE_TURN | eot | 0.98 | TAKE_TURN at 20682 ms during a pause the caller had not finished; TAKE_TURN at 24481 ms during a pause the caller had not finished; TAKE_TURN at 19582 ms while the caller was speaking; TAKE_TURN 642 ms after turn end |
| HT-D03 | HT-D03.e1 | HT | KEEP | kept | 1.00 | kept speaking |
| BG-D01 | BG-D01.e1 | BG | KEEP | false_stop | 0.00 | false stop at 8681 ms |
| BG-D02 | BG-D02.e1 | BG | KEEP | kept | 1.00 | kept speaking |
| BG-D02 | BG-D02.e2 | BG | KEEP | kept | 1.00 | kept speaking |
| BG-D03 | BG-D03.e1 | BG | KEEP | kept | 1.00 | kept speaking |
| NV-D01 | NV-D01.e1 | NV | KEEP | kept | 1.00 | kept speaking |
| NV-D02 | NV-D02.e1 | NV | KEEP | kept | 1.00 | kept speaking |
| NV-D03 | NV-D03.e1 | NV | KEEP | kept | 1.00 | kept speaking |
| AM-D01 | AM-D01.e1 | AM | EITHER | kept | 1.00 | kept speaking |
| AM-D02 | AM-D02.e1 | AM | STOP | yield | 1.00 | stopped at 12062 ms (0 ms after evident) |
| AM-D03 | AM-D03.e1 | AM | EITHER | yield | 1.00 | stopped at 15242 ms |
| OV-D01 | OV-D01.e1 | OV | STOP | yield | 1.00 | stopped at 10543 ms (0 ms after evident) |
| OV-D02 | OV-D02.e1 | OV | KEEP | false_stop | 0.00 | false stop at 11022 ms |
| OV-D03 | OV-D03.e1 | OV | STOP | yield | 1.00 | stopped at 12542 ms (0 ms after evident) |
| DV-D01 | DV-D01.e1 | DV | KEEP | false_stop | 0.00 | false stop at 9522 ms |
| DV-D02 | DV-D02.e1 | DV | KEEP | false_stop | 0.00 | false stop at 14702 ms |
| DV-D03 | DV-D03.e1 | DV | STOP | yield | 0.97 | stopped at 12242 ms (352 ms after evident) |

## Stops when nothing was happening

- BG-D01: at 482 ms (scenario time)
- NV-D03: at 1762 ms (scenario time)

Scoring rules: SCORING.md. Times are scenario time in ms, measured when your decision reached the harness.
