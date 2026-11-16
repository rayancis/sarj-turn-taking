# Teach a voice agent when to talk

**Sarj AI Hackathon · Human-Like Turn Taking · Starter Kit**

Voice agents are good at *what* to say and bad at *when*. They stop mid-sentence because someone coughed, talk over a
caller who said "wait, no", and jump in while you are still thinking. Humans do this effortlessly, in every dialect of
Arabic.

**Your job: while the agent is talking, decide whether it should keep talking or stop.** A live Arabic phone call
streams into your system, 20 ms at a time, and you decide as it happens:

| While… | A good agent… | Your system says | |
|---|---|---|---|
| the **agent** is talking, and the caller says "mm-hm", coughs, or a TV is on | keeps talking | nothing | **the challenge** |
| the **agent** is talking, and the caller really wants to speak | **stops**, fast | `STOP` | **the challenge** |
| the **caller** is talking, and pauses to think | waits | nothing | optional |
| the **caller** has finished | takes the turn, promptly | `TAKE_TURN` | optional, own leaderboard |

**The method is completely open.** No approach is preferred, and every approach gets exactly the same input and is
scored by exactly the same rules. To show the range, not to steer:

| | |
|---|---|
| Acoustic | voice activity, pitch, energy and rhythm, telling the caller apart from noise and echo |
| Speech recognition + reasoning | stream the caller's audio to speech recognition, decide from the partial words (rules, a small model, an LLM) |
| Audio-native models | a model that listens to the audio directly |
| Hybrid | combine several of these signals |
| Predictive | anticipate where the caller or the agent is heading, instead of reacting |
| Your own idea | the most interesting case |

The kit's baseline listens to loudness only, because that is the simplest thing that runs. It is a reference to beat,
not a starting design.

No account, no cloud service and no Sarj system is needed. Everything runs on your laptop.

---

## 1. Your first score (about 10 minutes)

You need Python 3.9 or newer (the `python3` that comes with macOS works). The kit has been tested on macOS; Windows
and Linux are expected to work but have not been tested yet. If something fails there, tell the organisers.

```bash
cd sarj-turn-taking-starter-kit
python3 -m venv .venv
source .venv/bin/activate                # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m ttbench.play                   # your system vs 8 example calls, in real time (~3.5 min)
```

Downloaded the zip instead of cloning? Run `git init && git add -A && git commit -qm start` once: you hand in a commit
hash at the end.

After about three minutes you see this (real output):

```
Score 48.6 / 100   (stops when it should: 0.50, keeps talking when it should: 0.38)   ·   end-of-turn track: 40 / 100
2 right, 0 could be faster, 12 wrong:
  ✗ GI-D02: a real interruption, but your agent had already stopped at 8.6 s for no reason, so it was not listening for this one (an early stop does not count).
  ✗ BC-D03: a backchannel ("mm-hm", "ايه"). You stopped, but the caller did not want the floor.
  ✗ HT-D02: you took the turn at 20.7 s while the caller was only pausing.
  ✗ BG-D01: background sound. You stopped, but the caller did not want the floor.
  ✗ NV-D01: a cough, laugh or echo. You stopped, but the caller did not want the floor.
    … 6 more in the report
Full report: runs/1114-212017/report.md
First run on these calls: this is your starting point.
Hear what the caller heard in GI-D02:  python -m ttbench.render --run runs/1114-212017 --scenario GI-D02
```

That is the baseline: it stops at **any** sound and takes the turn at any pause. Now you know what to fix.

## 2. Look at the calls

```bash
python -m ttbench.show                   # the 24 example calls: what happens in each
python -m ttbench.show HT-D02            # one call as a timeline: who says what, when, and what a good agent does
```

Each call is a stereo WAV in `data/dev/audio/`: **left = the agent, right = the caller's phone**. Open one in any audio
player. To hear what *your* agent did (it goes quiet where it stopped; a beep marks where it took the turn):

```bash
python -m ttbench.render --run runs/<name> --scenario BG-D01      # writes runs/<name>/BG-D01-heard.wav
```

## 3. Make it yours

Two ways in, both equally supported:

- **`my_system.py`**: a Python class. Every 20 ms, `on_frame(agent, caller)` gets the audio of both sides (16 kHz,
  16-bit) and returns nothing, `"STOP"` or `"TAKE_TURN"`. You are also told when the agent starts and stops talking,
  and the full text of what it is about to say. Replace the inside completely; the baseline logic is disposable.
- **Your own server**, in any language or framework, speaking the WebSocket protocol in `INTERFACE.md`
  (`examples/example_server.py` is a bare Python example). Start it, then test it with
  `python -m ttbench.play --url ws://127.0.0.1:8765`. Every tool in the kit works the same way.

To see the feedback loop work (this is not a strategy), change `STOP_AFTER_MS = 200` to `400` in `my_system.py`, then

```bash
python -m ttbench.play --fast            # the same 8 calls, 4x faster (~1 min)
```

The last lines tell you whether you improved:

```
▲ +11.4 vs your last run (48.6, 1114-212017-fast).  Best on these calls: 60.0.
```

Every run is kept in `runs/` and listed in `runs/history.csv`. Want to keep the baseline and try an idea next to it?
Copy the file and run `python -m ttbench.play --system my_idea.py`.

### Using slow components: speech recognition, language models, large audio models

All of these work through the same interface. Four things to get right:

1. **Never block the audio.** Do the slow work in a background thread or an async task, and keep reading frames and
   answering pings. With `my_system.py`, let the worker set a result and return it from the next `on_frame`
   (at most 20 ms later). A blocked system falls behind the stream and fails the connection check.
2. **Load models before the call starts.** Your system must say hello within 10 s of the connection opening.
3. **Your thinking time is your latency.** A decision counts when it arrives. Full credit lasts until 0.3 s after the
   moment a listener would know the caller wants to speak, which is often a word or two in, not the first sound;
   credit then falls until 1.5 s. If you stop for something that was not an interruption (a backchannel, a cough) and
   send `RESUME` within 0.5 s, you keep half of that moment's credit.
4. **Cloud services are allowed and must be declared** (`WRITEUP.md`). Internet access at the final is still being
   confirmed with the organisers: ask before you depend on it.

## 4. Measure properly

| Command | Use it for |
|---|---|
| `python -m ttbench.play --fast` | quick iteration (timing approximate) |
| `python -m ttbench.play` | 8 calls, real time: does a change really help? |
| `python -m ttbench.play --calls all` | all 24 calls, real time (~10 min): the number to report |
| `python -m ttbench.play --calls GI-D01,HT-D02` | just the calls you are working on |
| `python -m ttbench.play --url ws://127.0.0.1:8765` | your own server, already running (any language or framework) |
| `python -m ttbench.summary runs/<name>` | every right and wrong decision of a run, in plain words |
| `runs/<name>/report.md` | the full report: per category, per event, slices by dialect and noise |
| `python -m ttbench.leaderboard runs/a runs/b --out runs/board` | compare runs side by side |

**Reference points on all 24 calls:** never stopping scores 8.0; stopping at any sound (`my_system.py` as delivered)
41.0. Both are there to show the scale, not as targets.

## 5. How you are scored

- **Stop when the caller really wants to talk, fast.** Full credit up to 0.3 s after it became clear, less after,
  nothing after 1.5 s.
- **Keep talking through everything else**: backchannels, coughs, background voices, echo. Stopping when nothing is
  happening costs you.
- Your **score** combines the two so that both must be good: "stop at every sound" and "never stop" both score low.
- **Taking the turn** after the caller finishes (without jumping into their pauses) is a bonus track with its own
  leaderboard.
- Decisions are timed when they **arrive**: thinking time counts, as in a real call.

The final uses the same scorer on **hidden calls**: other voices, other noise, some real speakers. Don't tune to these
24 voices. Full rules: `SCORING.md`.

## 6. Submit

Run `python -m ttbench.ready`: it tells you what is missing. Then read **`SUBMISSION.md`**: code freeze (your
commit hash), a 2–4 page `WRITEUP.md` with at least five failure cases, and your system running on your laptop for the
final.

## Files

| | |
|---|---|
| `my_system.py` | one way to build your system: a Python class (starts as the loudness baseline) |
| `WRITEUP.md` | your write-up template |
| `CHALLENGE.md` | the full challenge brief: timeline, rules, judging |
| `SUBMISSION.md` | what to hand in, when, and how the final run works |
| `SCORING.md` · `INTERFACE.md` | the exact scoring rules · the WebSocket protocol |
| `DATA.md` · `SCRIPTS.md` | what the 24 example calls are (synthetic voices) · their scripts and the category definitions |
| `data/dev/` | the example calls: `audio/` (stereo WAV), `labels/` (who speaks when, and what a good agent does) |
| `ttbench/` | the harness, the scorer and the tools above. You should not need to change it |
| `examples/` | a bare WebSocket server, for systems written without `ttbench` |
| `TROUBLESHOOTING.md` | when something does not work |

The example calls are **synthetic** (computer-generated Saudi and Gulf voices, generated noise), and their labels come
from the scripts. They show you the situations, the timing and the format; the hidden calls are different audio.
