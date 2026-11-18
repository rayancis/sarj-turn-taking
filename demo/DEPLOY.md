# SARJ v3 deployment candidate

The integration branch configures SARJ v3 alone for the 24 original dev recordings, own-audio uploads/recording and Gemini Live. This document describes the candidate, **not a completed Render rollout**. The existing production branch is intentionally unchanged.

## Exact inputs and startup

- `demo/Dockerfile` pins Python 3.12.10 slim-bookworm by registry digest. `demo/requirements-public.txt` pins the complete Python runtime closure.
- `.dockerignore` admits only required production code and approved synthetic dev data. Historical P3/Baseline, research data, evidence, tests, local models/recordings, repository metadata and credentials are excluded from the image context.
- `python demo/models/sarj_v3/tt/assets.py --download` obtains only the six files in `model-assets.json` at its immutable snapshot. Every SHA-256 is checked. The runtime is offline for model loading, the assets are made read-only and source/manifest pins are checked before accepting HTTP requests.
- A missing or corrupted model fails startup; there is no legacy or acoustics-only production fallback. `/healthz` returns 503 if a configured model is unavailable.
- The proposed default cap is four recording playback sessions, one v3 each. Live retains its separate two-conversation cap. These limits are conservative configuration, not verified Render capacity.

Local production-equivalent configuration (Windows execution does not certify Linux):

```text
python demo/models/sarj_v3/tt/assets.py --download
python demo/server.py --public
```

Actual image gates, once Linux/Docker is available:

```text
docker build --pull -f demo/Dockerfile -t sarj-v3-candidate .
docker run --rm -p 10000:10000 -e PORT=10000 -e LIVE_MODE=hidden -e LIVE_V3_VERIFIED=0 sarj-v3-candidate
```

Record installed `libgomp1`, complete image digest, verified asset hashes, startup logs without secrets, health, CPU/RAM, sustained playback/live sessions and multiple users. Use approved dev/synthetic audio only; keep raw runtime records local. A successful Windows run or package download is not a Linux container test.

## Gemini and privacy

Keep `GEMINI_API_KEY` only in Render's environment; never copy it out for a local test. Preserve existing model selection, key parsing, consent, same-origin checks, usage limits and cooldown. `LIVE_MODE` supports off/hidden/on. Public visibility now additionally requires `LIVE_V3_VERIFIED=1`; this is false by default and in the proposed Blueprint.

The browser AudioWorklet synchronizes microphone and played-agent frames. v3 alone authorizes barge-in. A STOP flushes played audio and sends explicit Gemini activityStart plus bounded caller pre-roll; idle-turn activity is accounted separately. Gemini automatic activity detection remains disabled. The event evidence distinguishes v3 authorization, browser flush/playback stop and provider interruption; an unrequested interruption is a failed gate.

Before exposing Live publicly, use a separate protected test environment with its own service settings to verify the **actual** Google connection, audio generation, genuine interruption, backchannel/background behavior and absence of unrequested interruption. Do not expose private test recordings or log the API key. `/api/live/check` validates model availability only and cannot prove a real conversation. Fake Gemini tests cannot clear the real connection gate.

Own-audio recordings remain in memory, with the existing 60-second size limit, count/rate limits, unguessable IDs and 15-minute TTL. Gemini callers must consent to transmission to Google. No checkpoint/final or private audio is saved by this candidate. Leave recognized-word debug logging disabled.

## Rollout hold

The user explicitly requested local work and a deployment hold because no Docker/Linux or separate Render test service is available. Do not push `feat/demo-ui`, trigger Render, or enable public Live in this state. Repository privacy must also be verified before uploading approved-private project material.

Rollback branch: `codex/rollback-demo-ui-before-v3-20261118` at `3b76ebb`. At a later authorized rollout, fetch again, preserve a fresh rollback if production moved, merge without overwriting newer work, verify staged paths and the complete unchanged-kit test suite, then use a normal non-force push. Confirm the running revision, sole-v3 UI/config, uploads/microphone and health before considering the rollout complete.
