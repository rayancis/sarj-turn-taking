# The public development set

24 Arabic scenarios, 9.3 minutes. Version 0.1.0-synthetic, built 2026-11-14.

## Read this first: what the audio is

- **Synthetic.** synthetic: Gemini TTS voices (gemini-3.8-flash-tts) + procedural noise and effects. The voices are computer-generated, asked to speak in each dialect; how well
  they manage it varies. Noise, coughs, horns and echo are generated too.
- **Labels are `script_derived`**: computed from the scripts and the exact placement of each clip, not yet
  checked by native listeners. `qa_report.md` shows a machine check that each clip says its scripted words.
- **The hidden evaluation set is different audio**: other voices and noise; part of it may be human-recorded.
  Do not tune to these voices.

## Files

| | |
|---|---|
| `audio/<id>.wav` | 16 kHz, 16-bit, stereo: channel 0 = agent, channel 1 = caller microphone |
| `labels/<id>.json` | transcripts, timings, events, expected behaviour (schema: `schema/scenario.schema.json`) |
| `events.csv` | one row per labelled event |
| `manifest.json` | file list with sha256, scoring thresholds in force |
| `SCRIPTS.md` (kit root) | the scripts, timing rules and category definitions |

## Scenarios

| Id | Category | Expected | Dialect | Acoustic | Difficulty | Seconds |
|---|---|---|---|---|---|---|
| GI-D01 | GI | STOP | sa-najdi | quiet | 1 | 26.9 |
| GI-D02 | GI | STOP | sa-hijazi | phone-nb + street | 2 | 25.7 |
| GI-D03 | GI | STOP | sa-najdi | car | 4 | 24.8 |
| BC-D01 | BC | KEEP | sa-najdi | quiet | 1 | 23.6 |
| BC-D02 | BC | KEEP, KEEP, KEEP | sa-eastern | phone-nb | 2 | 30.2 |
| BC-D03 | BC | KEEP | sa-najdi | ac-hum | 4 | 25.5 |
| HT-D01 | HT | TAKE_TURN | sa-najdi | quiet | 2 | 24.7 |
| HT-D02 | HT | TAKE_TURN | sa-hijazi | phone-nb | 3 | 29.9 |
| HT-D03 | HT | KEEP | gulf-bahraini | quiet | 2 | 24.5 |
| BG-D01 | BG | KEEP | gulf-qatari | tv | 1 | 21.2 |
| BG-D02 | BG | KEEP, KEEP | sa-southern | street + phone-nb | 2 | 25.9 |
| BG-D03 | BG | KEEP | sa-najdi | household | 4 | 22.5 |
| NV-D01 | NV | KEEP | sa-northern | quiet | 1 | 19.2 |
| NV-D02 | NV | KEEP | sa-hijazi | quiet | 2 | 20.0 |
| NV-D03 | NV | KEEP | sa-najdi | speakerphone | 4 | 19.6 |
| AM-D01 | AM | EITHER (prefer KEEP) | sa-najdi | quiet | 2 | 22.7 |
| AM-D02 | AM | STOP | sa-najdi | phone-nb | 3 | 21.3 |
| AM-D03 | AM | EITHER (prefer STOP) | sa-najdi | quiet | 3 | 24.0 |
| OV-D01 | OV | STOP | sa-najdi | quiet | 2 | 21.0 |
| OV-D02 | OV | KEEP | sa-hijazi | quiet | 3 | 19.4 |
| OV-D03 | OV | STOP | sa-najdi | ac-hum | 1 | 22.4 |
| DV-D01 | DV | KEEP | gulf-kuwaiti | phone-nb | 2 | 19.6 |
| DV-D02 | DV | KEEP | gulf-emirati | car | 2 | 23.1 |
| DV-D03 | DV | STOP | gulf-kuwaiti | quiet | 2 | 19.9 |
