# Additional evaluation scenarios: provenance and licences

Built by `tools/build_extra.py` (deterministic; rebuild is bit-exact and checked against `*/manifest.json`).
The WAV files are **not committed** (`*.wav` is ignored); run the tool to recreate them.
`sources.json` lists every source file used with its URL, SHA-256, licence and attribution.

| Source | What is used | Licence | Notes |
|---|---|---|---|
| Arabic Speech Commands Dataset (ASC): Ghandoura, Hjabo, Al Dakkak, "Building and Benchmarking an Arabic Speech Commands Dataset for Small-Footprint Keyword Spotting", Engineering Applications of AI, 2021. https://github.com/abdulkaderghandoura/arabic-speech-commands-dataset | one-second human recordings of isolated Arabic keywords (30 speakers) for the agent channel, the caller, and background talkers | CC BY 4.0 | clips trimmed, level-scaled, concatenated, filtered and mixed (changes made) |
| ESC-50: K. J. Piczak, "ESC: Dataset for Environmental Sound Classification", ACM MM 2015. https://github.com/karolpiczak/ESC-50 | coughing, laughing, car horn, engine, rain, vacuum cleaner, door knock | **only clips whose own Freesound licence is CC0 or CC-BY** (per the dataset's `LICENSE` file; authors and URLs in `sources.json`) | the ESC-50 compilation as a whole is CC BY-NC 3.0; use here is non-commercial evaluation and the audio is not redistributed |
| Generated | room tone (white noise), synthetic room responses, telephone band-pass, echo path | none | `tools/build_extra.py` |

Not used: any customer data, private recording, checkpoint or final audio. Nothing here trains a model.

## Splits (no shared speaker, no shared noise clip)

| Split | Speakers (ASC) | Noise clips (ESC-50) | Use |
|---|---|---|---|
| `xdev` (25 scenarios, 30 events: 11 STOP, 19 KEEP) | the 18 "train" speakers: 2 agent voices, 2 background talkers, 14 callers | folds 1-3 | may be inspected while developing |
| `holdout` (25 scenarios, same templates, different seed) | the 12 "val"+"test" speakers: 2 agent, 2 background, 8 callers | folds 4-5 | **frozen**: scored once after the candidate is selected; never used to choose a parameter |

`design.json` in each split records the design factors per scenario (event level relative to the caller's
greeting, microphone gain, telephone band, noise bed and SNR, echo, cold start, simultaneous start).

## Label rules (script-derived, independent of any detector)

- A single acknowledgement word (نعم, موافق) spoken while the agent talks: **KEEP**.
- An explicit objection or stop word (لا, توقف, إلغاء): **STOP**; `evident_ms` is the end of the first such word.
- Another person talking, broadcast-like speech, the agent's own echo, and non-speech sounds: **KEEP**.
- Loudness is varied for both classes (soft interruptions; normal-level acknowledgements), so no detector cue is
  encoded in the labels.
- `tools/build_extra.py --verify` checks hashes, that every event starts inside agent speech, that `evident_ms`
  lies inside the caller's speech, and that each labelled sound is at least 6 dB above the preceding second.

## Limits

Isolated read keywords are not conversational speech: there is no natural backchannel prosody, no dialect
variety (speakers read Modern Standard Arabic words), and "interruptions" are concatenated command words. Noise,
echo and room effects are mixed digitally. Labels are not validated by listeners. Speaker gender/age are not
published by ASC; the label fields hold placeholders. These scenarios complement the kit's dev set; they do not
replace natural human calls.
