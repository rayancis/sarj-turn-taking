# How to submit

You hand in three things, and you bring your system to the final run.

| What | When | How |
|---|---|---|
| **Connection check** passed with the organisers | by Day 4 | they stream 6 calls to your laptop over the venue network |
| **Code freeze**: your commit hash, and your list of external services | Day 5, 18:00 | `python -m ttbench.ready` prints both when you are ready |
| **WRITEUP.md**, 2–4 pages, with at least 5 failure cases | when the organisers ask, before the presentations | the template is in this folder |
| **Your system, running**, for the final | Day 6, morning | on your laptop, reachable on the venue network (see below) |

## Check yourself first

```bash
python -m ttbench.ready
```

It checks each item below and runs the connection check against your system locally. Running your own server (any
language)? Start it and use `python -m ttbench.ready --url ws://127.0.0.1:8765`.

```
  ✓ team name set in my_system.py
  ✓ all changes committed to git (commit: 3f2a9c1)
  ✓ WRITEUP.md written (2–4 pages, no <...> left)
  ✓ WRITEUP.md lists at least 5 failure cases from the dev set
  ✓ WRITEUP.md section 8 declares external services, models and data (or says none)
  ✓ connection check PASS
Ready. Hand in: team 'falcons', commit 3f2a9c1, WRITEUP.md.
```

| If this fails | Do this |
|---|---|
| team name | set `TEAM = "..."` at the top of `my_system.py` (the name the organisers gave your team) |
| committed | `git add -A && git commit -m "..."`. Your system reports this commit hash; the final run must use exactly that code |
| WRITEUP.md | fill every `<...>`; judges read it before your talk |
| failure cases | run on all 24 calls (`python -m ttbench.play --calls all`), then `python -m ttbench.summary runs/<name> --failures`, listen to each case, explain it |
| external services | list every cloud API, pretrained model and dataset, or write "none". Cloud calls are allowed; they count in your latency |
| connection check | see TROUBLESHOOTING.md |

## The final run (Day 6)

1. Check out your frozen commit: `git checkout <your hash>`.
2. Connect to the network the organisers provide (wired, or Wi-Fi that passes a latency check), and start your system
   so they can reach it: `python my_system.py --host 0.0.0.0` (or your own server, listening on all interfaces).
   If your system uses cloud services, confirm with the organisers that internet access is available at your table.
3. Tell the organisers your address: `ipconfig getifaddr en0` (macOS), `hostname -I` (Linux), `ipconfig` (Windows).
4. Leave it running. The hidden stream is about 30 minutes; every team gets it at the same moment. If your system
   crashes, you get one restart within 60 s.

**Do not record or save the audio** you receive during the checkpoint or the final. Your code may be checked.

## How the final is scored

80 % is the automatic **core score** on the hidden calls (same scorer as `ttbench.play`; the hidden calls are
different voices and noise). 20 % comes from the judges: originality and reasoning (10), practical applicability (5),
clarity of your demo and evaluation (5). End-of-turn (`TAKE_TURN`) has its own leaderboard and does not change your
total. Details: `SCORING.md` and `CHALLENGE.md`.

By submitting, you accept the participation terms in `CHALLENGE.md` (section 10), including the licence they grant.
