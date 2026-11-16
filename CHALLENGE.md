# Sarj AI Hackathon: Human-Like Turn Taking Challenge

**When should a voice agent stop talking? Show us, in Arabic.**

---

## 1. The problem

Picture a caller in Jeddah on the phone with a voice agent. While the agent is talking, the caller might:

- say «أيوه» to show they are following;
- say «لحظة» because they need a second;
- say «اممم» while they think about a question;
- answer a question the agent asked a moment ago, while the agent has already moved on;
- say nothing at all, while the TV, a child or the street behind them is loud.

A good human agent handles all of this without thinking. They keep going through «أيوه». They stop for
«لحظة». They wait while the caller thinks. They never mistake the TV for the customer. Most voice
agents do not handle it as well: they either stop at every sound, or talk over people who really want to
speak. Arabic makes it harder: short acknowledgements are frequent, dialects differ, and the same word
(«طيب»، «خلاص») can mean "go on" or "my turn".

**This is an open problem.** Sarj AI works on it every day, and we do not think it is solved, by us or
by anyone. We are not telling you how we currently approach it, on purpose. We want to see what *you*
would build if you started from the problem rather than from someone else's design.

## 2. The challenge

Build a system that listens to a live conversation, frame by frame, and behaves the way a skilled human
agent would:

| While… | A skilled human agent | Your system says |
|---|---|---|
| the agent is speaking and the caller only acknowledges, hesitates, or makes no bid for the floor; or the sound is not the caller | **keeps speaking** | nothing (the default) |
| the agent is speaking and the caller genuinely wants the floor | **stops and listens**, quickly | `STOP` |
| the caller is answering, but pausing, thinking or reading something out | **waits** | nothing (the default) |
| the caller has finished | **takes the turn**, promptly | `TAKE_TURN` |

**The core of the challenge is the first two rows:** knowing, while the agent is talking, whether the
caller really wants to speak, and knowing it fast.

We judge **behaviour only**: what your agent would have done, and when, compared with what native
Arabic speakers say a good agent should do. How you get there is entirely up to you.

## 3. Arabic is mandatory

- **All benchmark audio is Arabic.** There is no English track. The code-switches Saudis use every day
  («أوكي»، «سوري»، «yes yes») appear inside Arabic speech.
- **Saudi and Gulf dialects come first:** Najdi, Hijazi, Southern, Eastern and Northern Saudi, Kuwaiti,
  Emirati, Bahraini and Qatari speakers. **At most 40 % of the evaluation audio is Najdi.**
- **Spontaneous speech, not reading.** Expect «إيه»، «أها»، «مم»، «طيب»، «لحظة»، «اسمع»، «اممم»،
  «خلاص», plus false starts, lengthened sounds, long pauses and self-corrections. Part of the hidden set
  is recorded from **unscripted** callers.
- **Real-world sound.** At least 40 % of the audio is narrowband telephone quality. The rest includes
  cars, streets, cafés, households, TV and speakerphone.

This document is in English. Your system must handle Arabic audio.

## 4. What good behaviour looks like

These describe behaviour, not method.

| While the agent speaks, the caller… | A good agent | |
|---|---|---|
| says «إيه» softly at the end of a phrase | keeps speaking | the caller is following |
| says «إيه والله صادق، صح», warmly agreeing | keeps speaking | long, but still just agreement |
| says «لا لا لحظة، بكرة أنا مو بالرياض» | stops | a correction |
| says only «لحظة», firmly | stops | a request to pause |
| says «أيوه… بس أبغى أغيّر الموعد» | stops, at the latest by «بس» | the intent becomes clear part-way through |
| says «طيب» with a falling tone, then nothing | either; keeping going is more natural | genuinely ambiguous |
| coughs, laughs; a child shouts «يُمّه!» nearby | keeps speaking | not the caller asking for the floor |
| is on speakerphone, so the agent's own voice comes back faintly | keeps speaking | nobody new is talking |

| After the agent asks a question, the caller… | A good agent | |
|---|---|---|
| says «اممم~ … بعد المغرب … يعني الساعة سبع» | waits through the pauses, then takes the turn | the caller was still answering |
| reads an order number in groups, with long pauses | waits until the number is complete | cutting in loses the number |

## 5. What you build

### Mandatory

1. **A real-time system** that connects to the evaluation harness (section 6), consumes the stream as it
   arrives, and sends `STOP` decisions. It must run through the final stream (about 30 minutes) without
   manual intervention.
2. **Arabic audio**, under the conditions in section 3.
3. **Your own evaluation on the development set**, including at least five cases your system gets wrong,
   with your explanation of why.
4. **A short technical write-up (2–4 pages)**: the idea, why you chose it, its latency and compute cost,
   trade-offs, and failure cases. Declare every external API, pretrained model and dataset.
5. **A code freeze**: a commit hash handed in on Day 5 at 18:00. The final run uses exactly that code.

### Stretch goals (never required)

| Stretch goal | How it counts |
|---|---|
| **End-of-turn decisions** (`TAKE_TURN`) | scored automatically on its **own leaderboard**; it does not change your total |
| **Recovering gracefully**: if your system stops and then realises it should not have, it sends `RESUME` | a quick recovery recovers part of the credit (section 7) |
| **Event labels**: tag each decision with what you think happened (backchannel, hesitation, background…) | shown on the scoreboard; considered by the judges |
| **Dialect analysis**: where your system behaves differently across dialects, and why | considered by the judges |
| **Live-microphone demo**: your system with a judge speaking Arabic | Day 6, unscored; considered by the judges |

### Method: completely open

We do not prescribe, recommend or prefer any approach, and we will not reward resemblance to any
existing system, including ours. Some directions teams take on problems like this, to show the range,
not to steer:

- acoustic and prosodic modelling (pitch, energy, rhythm, voice quality);
- streaming speech recognition with reasoning over partial transcripts;
- end-to-end audio models, or audio-capable language models;
- predicting where the agent's and the caller's turns will end, rather than reacting;
- modelling the caller's voice to separate them from everyone else in the room;
- using the agent's own audio and text as context;
- something nobody on this list has thought of. That is the most interesting case.

Telephony integration is not needed: the harness is your phone line.

## 6. Input/output contract

This interface is the only fixed part, so every system is tested identically. It describes **what
happens in the call**, not how to process it.

**Connection.** Your system runs a WebSocket server at `ws://<your-ip>:8765`; the harness connects to it.
Any language works.

**Audio in.** Binary messages, one every 20 ms, in real time:

```
16 kHz · 16-bit signed little-endian · 2 channels, interleaved [agent, user]
320 samples × 2 channels × 2 bytes = 1,280 bytes per message
channel 0 = agent   what the agent is saying
channel 1 = user    the caller's microphone: their voice, background sound, sometimes the agent's echo
```

**Context in** (JSON text messages). You may use it or ignore it:

```json
{"type":"session_start","session_id":"final","sample_rate":16000,"frame_ms":20,"channels":["agent","user"]}
{"type":"scenario_start","scenario_id":"x7f3","stream_ms":431000,"context":{"use_case":"clinic appointment reminder","language":"ar"}}
{"type":"agent_utterance_start","utt_id":"A1","stream_ms":436600,"duration_ms":12200,
 "text":"أهلين أستاذة هند. حبيت أذكّرك بموعدك يوم الأحد…","words":[{"w":"أهلين","start_ms":0,"end_ms":420}]}
{"type":"agent_utterance_end","utt_id":"A1","stream_ms":448800}
{"type":"scenario_end","scenario_id":"x7f3","stream_ms":452000}
{"type":"ping","id":41}
```

The agent's text arrives the moment it starts speaking, because a real agent knows what it is about to
say. Scenario IDs in the final run are random.

**Decisions out** (JSON text messages):

```json
{"type":"hello","team":"T07","system_version":"<git commit>","tracks":["core","eot"]}
{"type":"decision","decision":"STOP"}
{"type":"decision","decision":"RESUME"}
{"type":"decision","decision":"TAKE_TURN","label":"end_of_turn","confidence":0.82}
{"type":"pong","id":41}
```

- **`STOP`** = the agent falls silent now. **`RESUME`** (optional) = the agent continues where it
  stopped. **`TAKE_TURN`** = the caller has finished; the agent may answer.
- **Only `decision` is required.** `label`, `confidence` and `transcript` are optional: logged and shown,
  never scored.
- **The harness timestamps your decision when it arrives.** Your own timestamps are logged, never used.
  Processing and network time are part of your latency, just as in a real call.
- **The recording cannot react to you.** After `STOP`, the audio keeps playing. The scorer works out what
  your agent would have done from your decisions (section 7); the showcase plays what the caller would
  have heard.
- **Answer every `ping`** with a `pong`.

The full schemas, a minimal Python client and the harness itself are in the starter kit.

## 7. How you are evaluated

### Ground truth comes from people, not from a system

Every label was set by **native Arabic speakers** judging what a skilled human agent should do. For a
genuine interruption, listeners heard the audio cut off at successive points and marked the moment the
caller's intent became **evident** (for «أيوه… بس أبغى…», at «بس», not at «أيوه»). No label was set by
running any system, ours included. Where native listeners disagree, the case is labelled
**ambiguous**, and both behaviours earn credit.

### The benchmark: previously unseen Arabic audio, streamed live

On Day 6, every system receives the **same hidden stream at the same moment**: 64 to 80 scenarios,
roughly 25–30 minutes (the final count is confirmed before the event). Some are scripted and acted; some come from unscripted callers. Speakers, scripts
and noise recordings are all different from the development set. You never receive the hidden files.

**Core score (80 % of the total)** combines:

- **Responsiveness**: genuine interruptions where your agent fell silent, and how fast. Full credit up
  to 300 ms after the intent became evident; less after that; after 1.5 s it counts as a miss.
- **Restraint**: moments your agent should have kept talking and did. A stop that you take back with
  `RESUME` within half a second earns half credit; an unrecovered stop earns none. Stops when nothing is
  happening count against you.

The two are combined with a **harmonic mean**, which is high only when both are high: "stop at every
sound" and "never stop" both score near zero. Ambiguous cases (about 10 %) accept either behaviour.

**End-of-turn (stretch, own leaderboard, 0 % of the total):** waiting through the caller's pauses, combined
with taking the turn promptly once they finish. Because it is optional, it does not affect the main ranking:
skipping it costs you nothing, and doing it well earns a place on its own leaderboard.

**Judges (20 %):**

| Criterion | Points | What earns them |
|---|---|---|
| Originality and reasoning | 10 | a genuinely different idea, well argued, with honest error analysis |
| Practical applicability | 5 | could it run in a real call? latency, compute, robustness, what it would take to deploy |
| Clarity of demo and evaluation | 5 | a clear demo, and your own failure cases with Arabic examples |

Judges reward **difference and reasoning**. They do not reward similarity to any existing product.

### The scoreboard

Each team's row shows its total, Responsiveness, Restraint, median and p90 latency, false stops per
minute, and a pass rate for each category:

| GI | BC | HT | BG | NV | AM | OV | DV |
|---|---|---|---|---|---|---|---|
| Genuine interruptions | Backchannels | Hesitation | Background | Coughs, laughs, echo | Ambiguous words | Overlap | Dialect variation |

It also shows separate results for **unscripted** callers, a **dialect gap** (Najdi against other
dialects) and a **noise gap** (quiet against noisy), next to two simple reference lines: "never stop" and
"stop at any voice". Those show the scale. They are not targets.

### Sarj's own system

For our internal research, Sarj will run its current system on the same recordings, with the same
harness and metrics. It runs blind: it is frozen before the hidden set is seen, and nobody tunes it on
the set. It is **not a competitor, not on the scoreboard, and not a bar you have to clear**. Your ranking
depends only on your own results.

### The showcase

After the run, we play several hidden scenarios aloud. The room guesses first. Then we show every
team's decisions on a timeline, and play what the caller **would have heard** with the top teams'
systems.

## 8. Timeline: 6 days

| Day | What happens |
|---|---|
| **Day 1** | Kickoff: the problem, the data, the harness. Teams start building. |
| **Day 2** | First connection check available (6 public scenarios through the live harness). Mentor office hours daily from now on. |
| **Day 3** | **Checkpoint run** (to be confirmed): 16 new scenarios (in neither the dev set nor the final). You get your own scores per category; an anonymised leaderboard is posted. |
| **Day 4** | Last day for a passing connection check. |
| **Day 5** | Build. **Code freeze at 18:00:** hand in your commit hash and the list of external services. |
| **Day 6** | Final run on the hidden benchmark (morning), showcase, presentations by all five teams (8 min + 5 min Q&A), results. |

## 9. What Sarj provides

- **The starter kit:** the streaming harness (run it on your laptop against the dev set, in real time,
  exactly like the final), the scorer, the interface specification, an example server, and the two
  reference lines.
- **The development set:** 24 Arabic scenarios with audio, scripts and labels, covering all eight
  categories. The current version is **synthetic** (computer-generated voices and noise) with labels taken
  from the scripts. Human-recorded, listener-validated versions replace it where available, and are marked.
- *To be confirmed with the organisers:* engineering mentors for daily Q&A (mentors help you debug and
  think; they do not steer you towards any design), and a wired network at every table for the checkpoint
  and final runs.

No production systems, credentials or customer data are involved, and none are needed.

## 10. Rules

1. **Teams:** five teams of five students (25 participants).
2. **Any models, libraries and cloud APIs are allowed**, declared in your write-up. Cloud calls count
   towards your latency.
3. **No customer data, no private recordings.** If you record extra training audio, record people who
   consented, and say so.
4. **Do not store the audio of the checkpoint or final runs.** Teams' code may be checked.
5. **The hidden benchmark stays hidden.** Organisers and mentors do not share or describe it.
6. **One scored final run.** If our side fails, we re-run the affected scenarios. If your system crashes,
   you get one restart within 60 s.
7. **Appeals:** after the results, each team may flag up to 3 labels it believes are wrong. Two native
   speakers who were not involved with any system re-listen. If a label changes, it changes for every
   team.
8. **Intellectual property and usage rights.** Participants retain ownership of their original
   submissions. By participating and submitting their work, teams grant Sarj AI a perpetual, worldwide,
   non-exclusive, royalty-free license to use, reproduce, modify, adapt, develop, integrate, and
   commercially utilize their submitted solutions, including source code, algorithms, and technical
   implementations, without requiring further approval or compensation. This license is subject to
   applicable laws and third-party intellectual property rights. These terms must be clearly disclosed and
   accepted before participation. (The final wording is subject to Sarj AI's legal review before the event.)

## 11. Why this matters

Turn-taking is what makes a voice agent feel like a conversation instead of a recording. Arabic turn-taking
is a hard, open problem with little public data. We are running this because we want to be surprised: by
an idea we have not tried, or by evidence that an idea we dismissed works. The expected outcome is a set
of measurable prototypes and lessons. There is no promise that any submission will be deployed.

## FAQ

**Do we need a speech recogniser?** No. Use whatever you think works.

**Do we have to use the agent channel or the agent's text?** No. They are there because a real agent has
them. Use them or ignore them.

**Is the development set enough to train on?** No. It is for understanding and testing. Any extra data
(public corpora, your own consented recordings, synthetic speech) must be declared.

**What if our system is slow?** Then it would be late in a real call too. A correct decision after 1.5 s
counts as a miss.

**Do we need to beat Sarj's system?** No. It is not part of the ranking, and you will not see its results
during the event.

**What if a case is genuinely ambiguous?** It is labelled ambiguous in advance, and both behaviours earn
credit. You can also appeal a label (rule 7).
