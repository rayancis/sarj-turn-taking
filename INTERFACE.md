# Interface specification (v1.0)

This is the only fixed part of the challenge. It describes **what happens in the call**, not how to
process it.

## Transport

- Your system is a **WebSocket server**. Default `ws://<your-ip>:8765`. The harness is the client.
- One connection per run. The harness connects, waits up to **10 s for your `hello`**, then streams.

## Audio (binary messages)

| | |
|---|---|
| Rate | one message every **20 ms**, in real time (the harness never sends audio early) |
| Format | 16 kHz, 16-bit signed little-endian PCM, **2 channels interleaved** |
| Size | 320 samples × 2 channels × 2 bytes = **1,280 bytes** |
| Channel 0 `agent` | what the agent is saying: a clean reference |
| Channel 1 `user` | the caller's microphone: their voice, background sound, sometimes the agent's echo; often telephone quality |

Frame *n* covers stream time `[20n, 20n + 20)` ms. Between scenarios there are 1.5 s of silence.

## Messages from the harness (JSON text)

```json
{"type":"session_start","session_id":"dev","sample_rate":16000,"frame_ms":20,"channels":["agent","user"]}
{"type":"scenario_start","scenario_id":"GI-D01","stream_ms":0,"context":{"use_case":"delivery scheduling","language":"ar"}}
{"type":"agent_utterance_start","utt_id":"A1","stream_ms":19200,"duration_ms":26200,"text":"حياك الله يا أستاذ فهد…"}
{"type":"agent_utterance_end","utt_id":"A1","stream_ms":45400}
{"type":"scenario_end","scenario_id":"GI-D01","stream_ms":53300}
{"type":"ping","id":7}
{"type":"session_end"}
```

- A control message is sent **just before** the audio frame it refers to.
- `agent_utterance_start` carries the agent's full text, because a real agent knows what it will say.
  Use it or ignore it.
- In the final, `scenario_id` is a random string and `use_case` is generic. Never rely on ids.

## Messages from your system (JSON text)

```json
{"type":"hello","team":"T07","system_version":"3f2a9c1","tracks":["core","eot"]}
{"type":"decision","decision":"STOP"}
{"type":"decision","decision":"RESUME"}
{"type":"decision","decision":"TAKE_TURN"}
{"type":"pong","id":7}
```

| Decision | Meaning | When it counts |
|---|---|---|
| `STOP` | the agent falls silent **now** | while the agent is speaking |
| `RESUME` | the agent carries on where it stopped (optional) | after your own `STOP`, same utterance |
| `TAKE_TURN` | the caller has finished; the agent may answer | while the agent is silent (end-of-turn track) |

Optional fields on a decision: `label` (one of `interruption`, `backchannel`, `hesitation`, `background`,
`nonverbal`, `echo`, `end_of_turn`, `other`), `confidence` (0–1), `transcript` (string),
`client_stream_ms`. They are logged and shown in your report; they **never change your score**.

## Timing rules

- **Your decision's time = when it arrives at the harness**, on the harness clock. Processing time and
  network time are part of your latency, as in a real call. `client_stream_ms` is logged only.
- **Open loop:** the recording cannot react to you. After `STOP`, the audio keeps coming. The scorer
  reconstructs what your agent would have done (SCORING.md).
- **Answer every `ping` with a `pong`** carrying the same `id`. It measures your network round trip.
- Invalid messages are logged as protocol errors and ignored.

Machine-readable schemas: `schema/messages.schema.json` and `schema/scenario.schema.json`.
