# SARJ v3 — bounded streaming turn taking

## Idea and scope

SARJ v3 is the sole detector configured for the proposed public recording demo, Test Your Own Audio and Gemini Live barge-in. It combines online echo/noise-aware acoustics, the caller's previously observed speaking level, leaky speech evidence and local Arabic speech recognition. Short acknowledgements should preserve the agent's turn; explicit floor claims and sustained speech can authorize STOP. No hidden-set performance is claimed.

This is a local production candidate, **not a deployed or production-ready release**. Linux/Docker execution, Render load and a genuine Gemini connection remain gates. Baseline/P3 source, commits and benchmark records are retained for historical audit; they are excluded from the production image and default/public model registry. The kit's root `my_system.py` is retained as the historical P3 runner. The v3 harness entry point is `demo/models/sarj_v3/my_system.py`, selected explicitly with `ttbench.play --system`; protocol paths and kit files are unchanged.

## Architecture and causal inputs

Each 20 ms frame supplies 320 agent-reference and caller samples, 16 kHz PCM16. `tt/frontend.py` computes telephone-band spectral power, online noise floor, echo delay/gain estimates, voicing and speech evidence from frames already received. Caller-relative level is learned from prior caller speech while the agent is silent. The decider accumulates speech across short gaps, applies caller-level/context adjustments and emits one STOP until the next agent utterance. Permitted agent text and duration provide approximate phrase/question context. Caller labels, reference transcripts, scripts, scenario IDs and future audio are never inference inputs.

An offline CPU Whisper tiny worker transcribes a bounded window of already received caller audio. Arabic stop/keep cues adjust the acoustic decision; vocabulary rules are general words, not dev identifiers or waveform signatures. No prompt copies benchmark text into recognition. One request per session is in flight; the queue holds at most one request. Scenario object tokens and utterance/run sequences reject late results across resets. Very late word stops after caller silence are ignored. The shared model is loaded, integrity-checked and warmed before HTTP requests are accepted.

The frontend's echo/noise/reference histories, ASR ring, run-level/flux/prefix histories and diagnostic event log are bounded. Run level and flux retain at most 500 speech observations; prefix history retains at most 500 frames and is not collected when neither classifier nor training hook is active. The latter optional components are off in production. A terminal STOP with RESUME disabled updates only bounded frontend/audio state. With RESUME enabled for regression checks, run tracking remains bounded; RESUME requires caller silence and has a per-utterance limit. Production retains the upstream default of RESUME off.

**Cold start (revision `v3-coldseed-1`, `experiments/E03-v3-cold-start.md`).** When the agent starts before v3 has learned the caller's level (Gemini Live greets first), the noise floor is seeded from the line instead of about -90 dB, and for 1 s it falls straight to the quietest frame. Without this, steady street noise read as strong speech for more than 10 s (BG-D02 cold: false STOP after 1.1 s). Calls heard before the agent speaks keep identical decisions. A first attempt that changed the floor for every call was rejected because it missed GI-D03 in real time.

The bounded-history change preserves the original thresholds. Truncating histories can affect runs longer than their windows; this is documented rather than assuming identical benchmark behavior. Object tokens replace reusable Python object IDs for pending ASR request identity. No scoring threshold, scorer, schema, audio or label is changed.

## Website and Gemini control

The browser sends playhead progress for stored/own-audio demos; the server feeds v3 alone and returns its validated decisions. Optional hosted look-ahead is a playback convenience, not a real-time latency benchmark. Scores shown in the site must reference the exact new measured source/assets, never the pre-fix 96.38 record. Own recordings have no official labels and no accuracy score; they remain in server memory with size/count/TTL limits.

For Gemini Live, the browser AudioWorklet streams synchronized played-agent and microphone frames. Gemini automatic activity detection is disabled. While agent audio is playing, caller samples remain local to this server. Only a v3 STOP triggers playback flush, explicit `activityStart` and bounded 1.5 s caller pre-roll. Idle caller turns use separate activity signals and are not counted as barge-in. A silence fallback closes the caller's turn because v3 does not implement TAKE_TURN. No legacy detector participates and an unknown controller is refused.

The permanent API key is read only on Render from `GEMINI_API_KEY` (existing alternative-name support is retained). It is sent in the Google connection header, never returned by public APIs or browser code. Existing consent, same-origin checks, concurrency/hour/day limits and quota cooldown remain. Public Live visibility requires both `LIVE_MODE=on` and `LIVE_V3_VERIFIED=1`; defaults are hidden and unverified. A successful model-list check alone does not prove a Live conversation or v3-controlled interruption. Protocol-faithful fake-Gemini tests are regression evidence only.

## Reproducibility, models and licenses

`demo/models/sarj_v3/model-assets.json` pins **Systran/faster-whisper-tiny** snapshot `d90ca5fe260221311c53c58e660288d3deb8d356`. All six files, including model weights, tokenizer, vocabulary and configuration, have SHA-256 hashes verified during image construction and before the backend first loads. `model.bin` hash: `dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1`. Missing, changed or unsafe artifacts fail clearly; no runtime download or P3 fallback occurs. An explicit acoustics-only diagnostic configuration is rejected by the production registry.

| Kind | Source/version | Purpose and provenance |
|---|---|---|
| Pretrained model | OpenAI Whisper tiny, converted by Systran to CTranslate2; snapshot above | Arabic partial transcription on CPU/int8, two CPU threads; MIT; `WHISPER_LICENSE` is retained |
| ASR runtime | faster-whisper 1.2.1, CTranslate2 4.9.0, tokenizers 0.23.3, av 19.0.2 | Local inference; MIT/BSD notices supplied in installed packages; complete Python runtime closure is pinned in `demo/requirements-public.txt` |
| Model delivery | huggingface-hub 2.4.0, immutable public snapshot | Explicit build-time download only; six asset hashes independently checked; no caller audio sent to Hugging Face |
| Online API | Google Gemini Live, server-configured model | Voice agent for consented live conversations only; not used by v3's official dev evaluation; no genuine connection is certified here |
| Evaluation data | Supplied SARJ dev 0.1.0-synthetic, 24 calls | Synthetic diagnostic examples, script-derived labels; original audio/labels retained unchanged; public demonstration permission recorded in `demo/collections.json` |
| Historical research data | Arabic Speech Commands (Ghandoura et al., CC BY 4.0); selected CC0/CC BY ESC-50 clips (Piczak) | Existing P3 research resources only; not newly trained on, not in the production image or v3's official 24-call comparison |
| Historical pretrained model | Silero VAD v6 / silero-vad 6.2.3, MIT | Preserved for historical P3 tests; not a production v3 component |

The full dependency list and model-asset hashes, rather than only a model name, define the candidate. The Docker Python base is pinned by image digest. The Debian `libgomp1` dependency still needs its installed version and runtime recorded during an actual Linux build. Public-model licensing sources: [Whisper](https://github.com/openai/whisper/blob/main/LICENSE), [converted model](https://huggingface.co/Systran/faster-whisper-tiny), [faster-whisper](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/LICENSE), [CTranslate2](https://github.com/OpenNMT/CTranslate2/blob/v4.9.0/LICENSE). Gemini terms/pricing are not inferred from a fake service test.

## Evaluation and cost

Exact source `a8c93bb` (`v3-coldseed-1`), all 24 calls, speed 1.0, Linux 4 vCPU: Core **96.38** (repeat 97.17), R 0.951, S 0.969, 0/7 misses, 1 false STOP (BG-D01), STOP latency after evident median 252 ms / p90 872 ms, 0 protocol errors, max send lag 0.03 ms; the unmodified scorer recomputes it exactly (`experiments/evidence/runs.json`: `v3-a8c93bb-rt`, `-rt2`). The repeat spread is Whisper timing. The historical 96.38 record of `18471a8` is kept separately. The release report records Core/R/S, missed interruptions, false STOP events separately from STOP actions, latency from onset and evident, protocol errors, send lag, source and asset hashes, dependency versions and hardware. End-of-turn is separate and not attempted. Raw runs remain local by default.

Windows Python 3.12 uses a coarse monotonic clock here; Python 3.14 uses QueryPerformanceCounter. The unchanged 4x historical replay test failed on both default configurations, but passed with a scoped Windows `timeBeginPeriod(1)` scheduler request via `tools/windows_realtime.py`. The request is released on exit and does not alter harness clocks, message times, speeds, tolerances or scoring. The same explicit runtime setting is used for the new Windows measurement. This does not validate Linux/Render timing.

All model inference is CPU-local; no ASR API cost or cloud call enters v3 evaluation latency. Gemini usage is separate, subject to configured limits and provider terms. Measured CPU/RAM and concurrent-user behavior are recorded in the release report; no Render capacity claim is made without its actual runtime. Synthetic accelerated 30-minute state tests establish bounded data structures, not 30 minutes of wall-clock or final-set endurance.

## Failure analysis and practical limits

Five concrete cases/limitations to examine in the new report are BG-D01's background-speech false STOP, GI-D02's delayed yield in noise, AM-D02's late correction after an acknowledgement, and HT-D01/HT-D02's unsupported end-of-turn behavior. The latter two are optional EOT omissions, not missed Core interruptions. Their observed outcomes must be updated from the new run rather than copied from the historical record. Explanations are code/label-based hypotheses; no new human listening validation is claimed.

Whisper tiny can mistranscribe a short command or acknowledgement, background speech can resemble a caller, and quiet callers can yield delayed acoustic evidence. Shared CPU contention can delay recognition and make the late-result guard discard useful words. Echo estimates can suppress overlapping caller speech. A high synthetic dev score does not establish performance on spontaneous speech, novel speakers, telephone audio or the hidden set. A real-time dev run does not establish a 30-minute final stream or Render free-tier capacity. In the Linux container at 0.5–1 CPU, the Whisper word for GI-D03 arrived too late every time (0/5); at 2 CPUs about half the time, with the same distribution with and without the cold-start change. A cold-start detector still cannot tell soft side talk from the caller during the greeting (8 acoustic false stops across 24 cold replays, down from 11).

## Team and readiness

Official team name and names remain unspecified; the kit placeholder is not presented as a confirmed team code. No organizer connection/readiness check, calendar deadline, hidden-set score, real Gemini connection or production deployment is claimed. Existing production remains intact until every rollout gate passes and the user permits the held deployment to resume.
