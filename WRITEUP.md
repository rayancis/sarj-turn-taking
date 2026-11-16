# Write-up: <your team name>

2–4 pages. Replace every `<...>`. Judges read this before your presentation.

## 1. The idea

**Approach, in a few words:** <for example: acoustic; speech recognition + reasoning; audio model; hybrid; predictive;
other. Your own words.>

<In two or three sentences: how does your system decide when the agent should stop, and (optionally) when to take
the turn? What does it use that the baseline does not?>

## 2. Why this approach

<What did you notice in the calls, or in the baseline's mistakes, that led you here? What did you try that did not
work?>

## 3. How it works

<The architecture, from the 20 ms frames to a decision: each component, what it reads (caller audio, agent audio,
agent text), and what it passes on. A diagram is welcome. Name every library and model here; section 8 lists the
external ones.>

## 4. Speed and cost

| | |
|---|---|
| Median stop latency on the dev set (real-time run, from the report) | <ms> |
| Where that time goes (each stage, roughly) | <e.g. buffering, recognition, model, network> |
| Hardware it ran on, and how busy it was | <laptop model, CPU/GPU, % used, memory> |
| Model sizes | <parameters or MB, or none> |
| Cloud calls per minute of audio, and their typical delay | <0, or how many and ms> |
| What it would cost to run on a real call | <your estimate> |

## 5. Results on the development set

Paste the summary of your best real-time run on all 24 calls (`python -m ttbench.play --calls all`, then
`python -m ttbench.summary runs/<name>`), and compare with the baseline (B1: 41.0).

<...>

## 6. Where it fails (at least five cases)

Generate the table with `python -m ttbench.summary runs/<name> --failures`, listen to each case, and explain it.
Arabic examples are welcome.

| # | Call | What happened | Listen | Why it went wrong (your analysis) |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |
| 5 | | | | |

## 7. Strengths, weaknesses and trade-offs

<Where is your approach strong (which situations, dialects, noise), and where is it weak? What did you give up for
what? What would you change with another week?>

## 8. External services, models and data

Every cloud API, pretrained model and dataset you used. Write "none" if there are none.

| Kind | Name and version | What for |
|---|---|---|
| <API / model / dataset> | <...> | <...> |

## 9. Team

<names>
