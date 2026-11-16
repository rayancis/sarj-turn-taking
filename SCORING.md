# How you are scored

The scorer (`ttbench/score.py`) is a pure function of the labels and your run log. Run it yourself:
`python -m ttbench.score --data data/dev --run runs/<name>`.

## 1. What your agent "did"

From your decisions, the scorer rebuilds the agent's state during each agent utterance:

```
SPEAKING --STOP--> SILENT --RESUME--> SPEAKING     (each new agent utterance starts SPEAKING)
```

Decisions while the agent is not speaking (outside each utterance + 200 ms) are ignored.

## 2. Each labelled event

| Label | Passes when | Credit |
|---|---|---|
| **STOP**: the caller genuinely wants the floor | the agent goes silent by `evident + 1500 ms` **and stays silent until the caller stops talking** | latency `L` from the moment the intent became evident: 1.0 up to 300 ms, falling linearly to 0.4 at 1500 ms; after that, or a `RESUME` over the caller, it is a **miss** (0) |
| **KEEP**: acknowledgement, hesitation, noise, echo | the agent keeps speaking from the onset to 500 ms after the sound ends | 1.0; a stop taken back by `RESUME` within 500 ms ("hiccup") 0.5; otherwise 0. Already silent because of an earlier stop: 0 |
| **EITHER**: genuinely ambiguous | either behaviour | the preferred behaviour 1.0, the other 0.6 (a STOP is also scaled by its latency credit) |
| **TAKE_TURN**: end of the caller's answer | no `TAKE_TURN` during a required pause or while the caller speaks; then a `TAKE_TURN` soon after they finish | patience items pass/fail; promptness 1.0 up to 600 ms, 0.4 at 2000 ms, 0 at 3000 ms |

`evident` = the moment a native listener, hearing the audio only up to that point, would say "the caller
wants to speak". A stop between the caller's onset and `evident` counts as latency 0. A stop that began more than
300 ms *before* the caller's onset is a stray stop, and does not count as yielding to them.

**Stray stops:** a stop that starts while nothing labelled is happening counts against Restraint (each
scenario has a "no stray stop" item: 1 − 1 per stray stop, − 0.5 per hiccup).

## 3. The headline numbers

| | |
|---|---|
| **Responsiveness R** | per category, the mean STOP credit; then the mean over categories |
| **Restraint S** | per category, (mean KEEP credit) × (mean "no stray stop" item); then the mean over categories |
| **F** | `2RS / (R + S)`, high only when both are high |
| **Ambiguity A** | the mean EITHER credit |
| **Core score** | `100 × (0.9 F + 0.1 A)` |
| **End-of-turn score** | `100 × 2PQ/(P+Q)` (P = patience, Q = promptness): **its own leaderboard**, not in the total |

Never stopping gives R = 0. Stopping at every sound gives S ≈ 0. Both make F ≈ 0, so the Core score is
at most 10.

**The final ranking:** 80 % Core score on the hidden set + 20 % judges (originality 10, practical
applicability 5, clarity 5).

## 4. Also reported (diagnostics, not in the total)

Pass rate per category, median and p90 latency, false stops per minute of agent speech, misses, and F on
slices: Najdi vs other dialects, clean vs noisy/phone audio, scripted vs unscripted.

## 5. Thresholds

The timing values (300 / 1500 ms for STOP; 600 / 2000 / 3000 ms for TAKE_TURN) are **provisional**. They
will be calibrated on how fast human agents actually yield in Arabic role-plays, and then published
before the event. `manifest.json` → `thresholds` always holds the values in force.
