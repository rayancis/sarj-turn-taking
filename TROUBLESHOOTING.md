# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Your system did not start` (from `ttbench.play`) | `my_system.py` raised an error on start-up | the message shows its output; also in `runs/.<name>.system.log`. Run `python my_system.py` alone to see the error |
| `on_frame returned ...: a decision must be one of` | `on_frame` returned something other than nothing, `"STOP"`, `"RESUME"`, `"TAKE_TURN"` or a dict with `decision` | return `None` when you have nothing to say |
| The run stops early, `The run did not finish` | your system crashed during the run | see `runs/.<name>.system.log`; an exception inside `on_frame` closes the connection |
| `No hello within 10 s` with a large model | the model loads after the connection opens | load models when your program starts, before it accepts connections |
| Connection check fails on round trip, or `max send lag` grows | speech recognition, a language model or other slow work runs inside the audio loop | run it in a background thread or async task; keep reading frames and answering pings (README, "Using slow components") |
| Scores jump between `--fast` runs | fast runs compress time; a slow `on_frame` looks faster or slower than it is | confirm with a real-time run (`python -m ttbench.play`) |
| `Could not connect to ws://…` | your server is not running, or it is on another port | start it first; check `--port` on both sides |
| `No hello within 10 s` | your server does not send `hello` when the connection opens | send `{"type":"hello","team":…,"system_version":…}` immediately on connect (see the example) |
| `First message must be a valid hello` | missing `team` or `system_version`, or another message was sent first | fix the hello; send nothing before it |
| Protocol errors in `run.json` | a decision with an unknown value, or a bad `label`/`confidence` | see INTERFACE.md; the error text says which field |
| `max send lag` above 20 ms | your laptop was too busy to keep real time | close heavy apps; do not run the harness and a big model on one weak CPU; never trust latency from a lagging run |
| Every decision "ignored" | you send `STOP` while the agent is silent, or `TAKE_TURN` during agent speech | track `agent_utterance_start` / `_end` |
| Latency looks impossibly good or bad | you ran with `--speed` ≠ 1 | official numbers need `--speed 1` |
| Organisers cannot reach your server | it listens on 127.0.0.1 only, or a firewall blocks the port | run with `--host 0.0.0.0`; allow incoming connections on the port; give the organisers your LAN IP (`ipconfig getifaddr en0` on macOS) |
| `ModuleNotFoundError: websockets` | the virtual environment is not active | `source .venv/bin/activate`, then `pip install -r requirements.txt` |
| WAV files missing | `data/dev/audio` was not unpacked | re-download the kit; `manifest.json` lists every file with its sha256 |

Still stuck: bring `runs/<name>/run.json` and the first lines of `log.jsonl` to a mentor or the organisers.
