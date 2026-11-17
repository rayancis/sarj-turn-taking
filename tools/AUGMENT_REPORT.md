# Dev-set augmentation: report

Generator: `tools/augment_dev.py` (v1.0.0, numpy + stdlib). Tests: `tests/test_augment.py`.
Output (git-ignored): `data/aug/dev/<condition>/` (seed family A) and `data/aug/holdout/<condition>/`
(seed family B), 15 conditions x 24 scenarios x 2 trees = 720 stereo files, 513 MB per tree.

```
python3 tools/augment_dev.py --src data/dev --out data/aug --tree both --conditions all   # ~36 s, 4 processes
python3 tools/augment_dev.py --tree holdout --conditions street,tv --only GI-D01,BC-D03
python3 -m pytest tests/test_augment.py      # or: python3 tests/test_augment.py
```

## What changes, what does not
- Channel 0 (agent): bit-identical to the source in every file. Same scenario ids, same length, same
  timings; `events`, `user_segments`, `agent_utterances`, `background_events` are untouched.
- Channel 1 (caller) is processed. Label changes: `acoustic.conditions` (union of the original tokens and
  the added ones; `quiet` is dropped when a noisy condition is added), `acoustic.snr_db` (measured, additive
  conditions only; otherwise the original value is kept), `acoustic.notes`
  (`augmented: <cond>, seed <n>; base conditions: ...`), `acoustic.noise_source_ids` (an `AUG-...` id appended),
  `acoustic.echo` (speakerphone conditions), `audio.sha256`.
- Manifest: `thresholds` copied unchanged, `wav_sha256`/`label_sha256` recomputed, plus top-level
  `source_split: "dev"` and `augmentation {condition, tree, seed_family, generator, generator_version}`.
  Each scenario entry also carries an `aug` block (per-file seed, measured SNR, drawn parameters, e.g. partner
  scenario ids, burst times, lost-frame times). The manifest `split` field is left as `"dev"` for loader
  compatibility, so identify these splits by `augmentation`/`source_split` or by directory.
- Seeds: per file `sha256("aug|<family>|<condition>|<scenario>")[:4]`, family A = 1000, B = 2000. Same seed gives
  byte-identical output (verified, see Tests). Byte-identity is verified on this machine and numpy 2.5.3
  only; FFT results could differ in the last bit on other numpy/BLAS builds.

## SNR definition
Caller speech level = RMS of the ORIGINAL channel 1 inside labelled `user_segments` of kind
speech / backchannel / filler (nonverbal and side_speech excluded). Added noise is scaled so
that whole-file RMS(noise) = speech_rms / 10^(SNR/20). Because the original is sometimes already noisy, the
SNR is relative to the original mix, not to a clean source.
For `combo_phone_street` the noise is calibrated after the phone chain (phone(x+n) - phone(x) against phone(x)).
For `combo_speaker_ac` the SNR refers to the added AC hum only; the agent leak is reported as `echo`.

## Conditions, parameters, measured SNR (written to the labels)
Uniform draws per file within the range; texture parameters not fixed by SCRIPTS.md differ between the two trees
(see `PRM` in the script).

| condition | what is done | spec range | measured SNR dev (min/mean/max) | holdout |
|---|---|---|---|---|
| phone_nb | FIR band-pass (edges drawn 280-320 / 3300-3500 Hz), decimate to 8 kHz, G.711 mu-law round trip, zero-stuff + FIR upsample | n/a | - | - |
| ac_hum | 50/100/150 Hz harmonics (f0 49.7-50.3 Hz, drifting amplitude) + band-passed fan noise, hum/fan -3..+6 dB | 20-25 dB | 20.4 / 22.7 / 24.9 | 20.2 / 22.4 / 24.9 |
| car | brown-ish rumble (low-pass 90-380 Hz) + pink hiss, 0.1-0.6 Hz slow modulation depth 0.2-0.4 | 10-15 dB | 10.4 / 13.2 / 14.8 | 10.1 / 11.8 / 14.8 |
| street | tilted traffic bed, 1-3 passing-vehicle swells, 1-3 horn bursts (0.3-1.2 s, two-tone harmonic), 1-2 motorbike rasps (1-3 s) | 5-12 dB | 5.0 / 8.0 / 11.5 | 5.5 / 9.3 / 12.0 |
| cafe_babble | 3-6 other-scenario caller channels (label-gated speech, random time shift, 3 dB gain spread, band-limited, short RIR) + 4-12 clinks | 5-10 dB | 5.0 / 7.4 / 9.7 | 5.3 / 7.5 / 9.8 |
| tv | one other-scenario caller channel, band-pass 180 Hz-3..5.5 kHz, RIR with RT60 0.25-0.5 s | 10-14 dB | 10.2 / 12.1 / 13.6 | 10.1 / 11.7 / 13.9 |
| household | 3-8 events (door slams, dish clinks, child-like voiced bursts 330-560 Hz) + room tone 8-16 dB under the events | 10-20 dB | 10.4 / 15.1 / 20.0 | 10.1 / 14.5 / 19.9 |
| nonspeech_events | 1-3 cough/laugh/sigh bursts, -10..-3 dB re caller speech, only in gaps outside labelled segments (+-400 ms) and event spans (-500 ms before onset, +300 ms after last anchor) and localized background events | n/a | bursts placed: 1 in 13 files, 2 in 6, 3 in 5 (every file got at least one) | 1: 9, 2: 6, 3: 9 |
| reverb | exponentially decaying noise RIR, RT60 0.3-0.8 s (dev draws the lower half, holdout the upper half), direct path kept, speech level renormalised | n/a | - | - |
| speakerphone | reverb on the caller + agent channel delayed 150-250 ms, -15..-25 dB, through the same RIR | echo -15..-25 dB | - | - |
| level_low | gain -10..-14 dB | n/a | - | - |
| level_high | gain +8..+12 dB, tanh soft clip at int16 full scale | n/a | - | - |
| packet_loss | 20 ms frames dropped by a two-state burst model, realised rate redrawn until 2-5 % (observed 2.2-4.8 % dev, 2.0-4.9 % holdout); mode zero or repeat-previous per file | 2-5 % | - | - |
| combo_phone_street | street, then phone_nb | 5-12 dB | 5.6 / 8.8 / 11.9 | 5.2 / 8.5 / 11.8 |
| combo_speaker_ac | speakerphone + ac_hum | 20-25 dB (hum) | 20.4 / 22.5 / 25.0 | 20.2 / 22.1 / 24.6 |

Soft limiter: every condition except `level_high` passes through an identity-below-28000 tanh knee that cannot
reach full scale, so no sample is clipped (observed max fraction of |x| >= 32767: 0).
Pairings: for babble/tv, dev uses the first k of a seeded permutation of the other 23 scenarios; holdout draws only
from scenarios outside dev's first six, so the partner sets never overlap (tested).

## Tests
`python3 -m pytest tests/test_augment.py`: **15 passed, 0 failed** (36 s). Also runs as a plain script
(`python3 tests/test_augment.py`). Covered, with 6 scenarios x 15 conditions x 2 trees generated into a temp dir
(GI-D01, BC-D03, BG-D02, HT-D01, NV-D03, DV-D02), plus the on-disk full trees:
load via `load_split` with the right ids; channel 0 bit-identical and length identical; labels identical apart from
`acoustic` and `audio.sha256`; conditions union and notes/seed; manifest thresholds and all sha256 values;
label SNR inside the range +-1.5 dB and re-measured from the written audio within 0.7 dB (six additive conditions);
no clipping (< 0.1 % of samples at +-32767) except `level_high`; level-gain ranges; echo level/delay ranges;
packet-loss rate 2-5 % and only dropped frames differ; determinism (same call twice, and two builds byte-identical);
dev vs holdout differ for every scenario x condition; partner pairings never equal the scenario and are disjoint
across trees; nonspeech bursts never touch segments or event spans (re-derived independently in the test; a mutant
with the exclusion zones disabled is caught); G.711 reference code values and phone band edges; a full 24-scenario
split loads; every generated split in `data/aug` has matching hashes, identical ch0 and loads.
Also checked by hand: regenerating the whole `holdout/cafe_babble` split reproduces the files on disk
byte for byte.
Runtime: full generation of both trees 36 s wall (about 93 s CPU, 4 processes).

## LIMITATIONS (please read)
- All noise is procedural. Horns, motorbikes, door slams, clinks, coughs, laughs and sighs are simple
  harmonic/filtered-noise models and sound synthetic. Passing these conditions says little about real recordings.
- Babble and tv reuse the same 24 dev TTS voices (the caller channels of other scenarios, gated to their labelled
  speech). The "crowd" is a few synthetic voices in Arabic, with identical prosody style; a tv or babble voice can
  therefore resemble the target caller voice or even share a speaker, and these are intelligible Arabic speech.
  Partner speech comes from channel 1 and so carries the partner's own original noise inside its segments.
- RIRs are statistical (exponential noise tails plus a few spikes), no real room geometry. Reverb is applied to the
  caller only; the agent channel is dry. The agent leak uses the same RIR as the caller.
- Label validity: labels remain only time-valid. I did not listen to any output; nothing was verified perceptually.
  Cases where short or quiet intent-carrying sounds probably become inaudible or ambiguous:
  - Nonverbal and other quiet segments sit far below speech level in the source, so at 5-12 dB SNR relative to
    speech they are buried. Local SNR measured on short/nonverbal/backchannel/filler segments goes as low as
    -19.9 dB (holdout cafe_babble HT-D02 X1), -17.7 (holdout street HT-D02 X1), -9.8 (dev street GI-D03 X1);
    median short-segment SNR is about 8-9 dB for street and cafe_babble and about 12 dB for car.
    Where the label says a nonverbal event should be ignored (NV/BG), burying it does not invalidate the label; where
    the label depends on it being heard (GI-D03 nonverbal ends the interruption), it may.
  - packet_loss: 9 of 18 backchannel segments are hit by at least one dropped frame; in holdout DV-D02 U1, BC-D02 U1b
    and OV-D02 U1, 5-6 frames (100-120 ms) fall inside the segment. A 100 ms word can be partly erased.
  - cafe_babble and tv: competing intelligible speech overlapping a backchannel can be mistaken for caller speech;
    the label (KEEP vs STOP) still describes the caller, as intended, but the label author's "intent audible" assumption
    is weaker. tv at 10-14 dB below speech is mild. Babble at 5 dB is hard even for people.
  - street/household events can land on top of caller speech and mask words.
  - level_low (-10..-14 dB) leaves caller speech RMS at -33 to -40 dBFS (measured over labelled segments), fine for 16-bit audio but low
    for energy VADs with fixed thresholds.
  - Scenarios already carrying a condition stack with it: base `speakerphone` (NV-D03) + added speakerphone puts two
    echoes in the audio but the label's `echo` shows only the added one; base `phone-nb` + phone_nb is a second
    band-limit/mu-law pass; base `tv` (BG-D01) + tv has two tv voices.
  - BG-D01's labelled event spans most of the file, so `nonspeech_events` has little room there (placed only in the
    first ~6 s or after the span).
- `acoustic.conditions` uses extension tokens that are not in the schema enum: `reverb`, `level-low`, `level-high`,
  `packet-loss`, `nonspeech-events`. `ttbench.dataset.load_split` does not validate the schema, so loading works, but
  `schema/scenario.schema.json` validation of these labels would fail on those tokens (I did not run a schema
  validator). `ttbench/score.py` treats any token outside {quiet, ac-hum} as noisy_or_phone. `ac-hum`-only augmented
  files therefore count as clean there.
- SNR is relative to the already-noisy original mix, so the effective SNR in originally noisy scenarios is lower than
  the label shows. The label `snr_db` for non-additive conditions is the unchanged original value.
- The evaluation harness was not run on any generated data.

## Suggested listening list (riskiest first)
1. `data/aug/holdout/street/audio/HT-D02.wav` : nonverbal X1 (about 1.8 s) buried, local SNR about -17.7 dB; horns/bikes.
2. `data/aug/holdout/cafe_babble/audio/HT-D02.wav` : same segment under six-voice babble at about -19.9 dB local SNR.
3. `data/aug/dev/street/audio/GI-D03.wav` : nonverbal X1 about -9.8 dB local SNR in a STOP scenario.
4. `data/aug/holdout/packet_loss/audio/DV-D02.wav` : 6 lost frames inside the backchannel-bearing segment U1 (repeat mode).
5. `data/aug/holdout/speakerphone/audio/NV-D03.wav` : RT60 0.77 s plus echo -16.9 dB / 211 ms stacked on an original speakerphone.
6. `data/aug/dev/tv/audio/BG-D01.wav` : base already has tv speech, plus a second intelligible tv voice (check the
   synthetic voices are not confusable with the caller; also check `data/aug/dev/cafe_babble/audio/BC-D02.wav` for three backchannels under babble).
