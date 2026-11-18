# SARJ v3 independent integration review — 2026-11-18

Status: **not cleared for publishing**. No remote branch was pushed and no Render deployment was triggered.

## Scope and provenance

- Destination: `rayancis/sarj-turn-taking`, `feat/demo-ui`.
- Fetched destination head: `3b76ebb1fd2a905a0059b16833b93d5c07420a1e`; exactly the patch's stated base. No newer destination work was overwritten.
- Local rollback: `codex/rollback-demo-ui-before-v3-20261118` at that head.
- Local integration: `codex/integrate-sarj-v3-20261118`, patch commit `8a63799d4605ebbac42dfda7500c326aa35ab103`.
- Isolated worktree: `C:/Users/rayan/Documents/Codex/sarj-v3-integration-20261118`. Original workspace remains on `main` at `bd2a3f0`.
- Source [patch](https://github.com/leenalotaibi-source/sarj/blob/claude/sarj-turn-taking-system-xwvrh0/integration/demo-add-sarj-v3.patch), Git blob `d2c592aa1f7b3c524c0dc54e96e7108373ac5a51`, mail commit `e0dada0936f785cdfe781ae389caa7af4e408eaa`.
- Source [instructions](https://github.com/leenalotaibi-source/sarj/blob/claude/sarj-turn-taking-system-xwvrh0/integration/README.md), blob `fc116d2829cca3b9d114cf7d2ed6b703de320bf1`.
- Local patch SHA-256: `1a998270a04fd2ba1565a1e88527d47725f354b11257fd1b43b880acd8cb3604`.
- All seven bundled v3 source files were separately fetched from teammate revision `18471a8` and matched the additions in the patch. All seven source SHA-256 pins match.
- The patch was applied unchanged for review, after `git apply --check`. No algorithm fixes have been mixed into these measurements.

## Findings before fixes

1. **P2 — run histories have no internal bound, including after STOP.** In `demo/models/sarj_v3/tt/decider.py:204–214`, `run_levels`, `run_flux` and `run_frames` grow before the stopped-state return at line 225. A continuing voiced run produces one STOP at frame 35, then leaves 990 entries after 1,000 frames and 9,990 after 10,000 frames. The review safety test similarly fails with 10,000 retained feature records. This is not evidence of a dev-call crash; it is an unresolved long-run memory requirement. Bound the statistics/history explicitly, avoid retaining training-only frame features in the deployed default, and verify STOP/RESUME after the change. A modified detector must receive new pins and its own measured result; do not attach the old exact-source score to it.
2. **P2 — deployment does not pin or verify the ASR assets.** `demo/Dockerfile:20` downloads the moving Hugging Face default revision without `revision=` or file digest verification. Source pins in `demo/server.py` cover Python files, not the weights/config/tokenizer. The current download matches the claimed `model.bin` hash, but a future image rebuild can silently load different assets under the same v3 revision/benchmark label. Pin the model snapshot and verify the complete artifact manifest; lock relevant transitive runtime versions.
3. **P2 — the required model declaration is missing.** The patch adds Whisper tiny/faster-whisper/CTranslate2 but leaves `WRITEUP.md` unchanged. Declare the new pretrained model, conversion/runtime versions, license, local processing and external build-time download. No new evaluation/training dataset is added by this patch.
4. **Test gate is not green on this host.** The 146 repository tests were executed in two disjoint groups: 58 relevant demo/public/live tests passed; the other group had 87 passes and one timing failure. `tests/test_replay.py:59` fails its unchanged 4x replay-versus-harness timestamp tolerance. It uses the unchanged root P3 detector, not v3. Initial paired decisions included 60/40 ms deviations; an isolated rerun with other review workloads stopped still had a 104 ms deviation. Decision types and order matched and protocol completion passed. This is a host/timing blocker to a blanket “146 passed” claim, not evidence that this patch changed P3. Do not relax the test or the scorer to conceal it.
5. **Browser regression checks need updating.** The existing browser script returns 21/25. Three failures are obsolete expectations about exactly two models or the previous headline wording; the remaining error check sees `/favicon.ico` 404s. The live browser script returns 7/8: barge-in and playback checks pass; its console-error assertion also sees favicon 404s. Favicon handling is unchanged by this patch. Keep meaningful behavioral assertions and add coverage of v3 being available/unavailable, selection/reset and all three decision lanes.

Repository visibility was `public` both initially and at the final GitHub check, contrary to the supplied project constraints. The user elected to make it private themselves and requested local review to continue. No remote write was attempted. The final fetch still shows destination `feat/demo-ui` at `3b76ebb`; no newer destination commit needs adaptation at this point. Recheck privacy before any later upload.

## Model, causal processing and state review

- v3 uses echo-aware telephone-band powers, online noise/echo estimates, caller-relative level, leaky speech evidence, and local Arabic stop/keep word recognition through Whisper tiny. Optional Silero and classifier paths are disabled by default.
- No inference-path reads of dev labels, scripts, scenario IDs, caller reference transcripts or future audio were found. Agent text/duration come from permitted harness context. The demo/test/report code reads labels to score/display results, separate from detector inference.
- ASR runs on a worker with one in-flight request, a queue of size one, and a bounded audio deque (170 default 20 ms frames, approximately 3.4 s including padding). Recognition uses already received caller samples. It does not call an external transcription API.
- Requests carry decider identity/utterance/run tags. Six added review checks passed: stale results from a different scenario, utterance or run are rejected; an accepted current stop word emits one STOP; no decision is emitted after agent end; very late word stops are ignored; RESUME waits for caller silence. The separate bounded-history check fails as described above. An object-identity reuse stress probe did not reproduce a cross-scenario collision; no such defect is claimed.
- Scenario reset constructs a fresh frontend/noise/caller-level state; agent start clears STOP/RESUME/run state. RESUME is disabled in the shipped default. The official WebSocket runner remains unchanged and handles pings; the demo preloads model backends before serving HTTP.
- No new persistence or outbound-audio path was found in v3. Normal recognition buffers/results remain in memory. Debug logging can include recognized words if `TT_DEBUG` is deliberately enabled, so leave it off for private/live data.
- The new model is downloaded during image construction; its current `model.bin` SHA-256 is `dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1`, snapshot `d90ca5fe260221311c53c58e660288d3deb8d356`.
- Primary license sources: [model card](https://huggingface.co/Systran/faster-whisper-tiny), [faster-whisper 1.2.1](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/LICENSE), [CTranslate2 4.9.0](https://github.com/OpenNMT/CTranslate2/blob/v4.9.0/LICENSE), [Whisper](https://github.com/openai/whisper/blob/main/LICENSE). They identify MIT licensing; deployment should retain the applicable notices.

## Demo and Gemini Live

- Adds an optional third playback detector, dynamic model cards/results/benchmark columns/timeline lanes, and selects v3 by default when available. Baseline and P3 remain required.
- Public-mode startup still refuses to start if any configured detector is unavailable, including v3. Its optional-client fallback is therefore not a complete server-startup fallback. Missing ASR takes the explicitly described acoustics-only path rather than disabling v3.
- `demo/live.py`, `demo/static/live.js`, `demo/static/live_worklet.js`, Live feature flags, key parsing, origin/consent/usage limits and model-check endpoint are byte-for-byte unchanged. Gemini Live still offers only P3/Baseline; v3 is **not** wired into the live controller.
- Browser BC-D02 verification completed with three sessions: Baseline STOP 13,660 ms, P3 STOP 13,820 ms, v3 no STOP; v3 kept all three KEEP events and remained audible. No page JavaScript exceptions were seen. Snapshot and screenshot are local in `.local/`.
- Browser regression behavior passed playback/muting, switching back to P3, reset, repeated playback, scenario switching, upload, microphone recording and 390 px mobile width. Exact script counts and its outdated assertions are documented above.
- Gemini Live browser testing used the protocol-faithful local `tests/fake_gemini.py`, synthetic/dev caller audio and a dummy local key. In repeated checks P3 STOP controlled activityStart; played agent audio stopped 20 ms later; no unrequested Gemini interruption was observed. This is not a real Google API connection, model-availability or Render test.
- Existing Render runtime/branch/autodeploy settings are unchanged except a capacity comment. Additional Whisper CPU/RAM and Linux image build behavior remain unverified on Render. Docker is not installed on this host; no container or extra hosted service was introduced.

## Benchmark integrity

- `ttbench/`, `schema/`, `data/dev/`, scoring thresholds and root `my_system.py` are unchanged from the destination head; the official kit/scorer/schema/dev data also match baseline `0915ace`.
- All 24 WAV and all 24 label hashes match the manifest. Manifest SHA-256: `93a23bcd8eddd0c615488ef1089b6d252182eac71144dc2f359ad7ecb5029366`.
- Existing entries in `experiments/evidence/runs.json` are semantically unchanged; the patch reformats them and adds a v3 record. Earlier baseline measurements and commit `0915ace` remain intact.
- Independent recomputation with the unmodified scorer exactly matches all fields in the supplied score: Core **96.37563488962688**, R **0.950875**, S **0.96875**, misses **0/7**, false stops **1**, median/p90 STOP latency **252.1/871.2 ms**, protocol errors **0**. All four evidence file hashes match. Its metadata says speed 1.0, 29,651 frames, 24 calls and completed. This validates the supplied record's internal consistency, not independent provenance of its original execution.
- The supplied run used a cloud host with 4 vCPUs. Render performance and generalization are not established by these 24 synthetic diagnostic calls. Historical P3/Baseline rows are not fresh controlled comparisons on this Windows host.
- Independent speed-1.0 run of the exact patched source completed: **Core 96.34123998436583**, R **0.950125**, S **0.96875**, misses **0/7**, false stops **1**, hiccups **0**, protocol errors **0**, unattributed/ignored decisions **0/0**, 9 decisions. This supports approximately the claimed result; it does not reproduce exactly 96.38.
- All **29,651/29,651** frames and **24** scenarios completed. STOP latency after evident: median **262 ms**, p90 **876 ms**, seven yields. Ping RTT: median **0 ms**, max **16 ms**, 119 replies. End-of-turn was not attempted. Recomputing the unmodified scorer from this new log and current labels exactly matches the generated score.
- **Timing limitation:** maximum harness send lag **25 ms**, above the kit's 20 ms caution level. Python 3.12 on this Windows host uses `GetTickCount64()` for monotonic time, with **15.625 ms** advertised resolution. Treat latency and zero-RTT values as coarse host-limited observations, not precise deployment performance. The run was speed 1.0, but that flag alone does not remove scheduling limitations. Do not substitute a fast run or alter kit clocks/thresholds to claim verification.
- Command: `python -m ttbench.play --system demo/models/sarj_v3/my_system.py --calls all --name codex-v3-8a63799-rt-20261118`. Source commit **8a63799** was clean at launch; `OMP_NUM_THREADS=1`, default v3 configuration, Whisper words enabled. Other review tests, browsers and local demo servers were stopped before the run; ordinary desktop activity was not disabled.
- Runtime: Windows 11 build 26200, Intel Family 6 Model 140, 8 logical CPUs, Python **3.12.10**. Versions: numpy **2.5.5**, websockets **17.4**, faster-whisper **1.2.1**, CTranslate2 **4.9.0**, onnxruntime **1.29.0**, huggingface-hub **2.4.0**, tokenizers **0.23.3**, av **19.0.2**, pytest **9.2.0**. Model and dataset hashes are listed above. No fresh Baseline/P3 score comparison on this host is claimed.
- New raw run stays local under `runs/codex-v3-8a63799-rt-20261118/`; environment and verification summaries stay in `.local/`. SHA-256: `run.json` = `04263be34ee8485c733a10b4523e2e1e83034e5c07151a686e762052afe353c0`; `score.json` = `b252ed8ebbe235917f2a69afcb13f7b0d1b50604d923ac9fa58c71a1c47c7a81`; `log.jsonl` = `b4d278e97de356805fd6f2c56f7abaab2be04515cf06525ef83647a461827be4`; `report.md` = `bb29036a720aae4031a5e7a9bbfb2a17daea2c5055c3c668492accd877e692c9`.

## Reproduction commands

Run from the integration worktree using its `.venv/Scripts/python.exe` on Windows and `PYTHONUTF8=1`, `OMP_NUM_THREADS=1`:

```text
python -m pytest -q tests/test_demo.py tests/test_demo_public.py tests/test_demo_live.py
# 58 passed in 127.45 s
python -m pytest -q tests --ignore=tests/test_demo.py --ignore=tests/test_demo_public.py --ignore=tests/test_demo_live.py
# 87 passed, 1 failed in 69.57 s; includes the entire original 26-test kit suite
python -m pytest -q tests/test_replay.py::test_replay_matches_real_harness_decisions
# isolated rerun: failed in 17.35 s, matching decision types but a 104 ms timestamp deviation
python -m pytest -q experiments/sarj_v3_review_checks.py
# 6 passed, 1 failed; expected review finding, not suppressed or added to the official scorer
```

The review checks use synthetic frame features and a deferred local recognizer stub, with no files of caller audio or labels. The terminal-stop regression observes retained-history lengths growing from `(16, 17, 16)` to `(10016, 10017, 10016)` during 10,000 more voiced frames while RESUME is disabled. The checks are deliberately outside `tests/` and run explicitly, so the 146-test repository result above describes the original proposed integration suite, not a silently altered collection.

Chrome **157.0.8103.79** was driven through the existing Playwright scripts, using a local launcher to point the bundled `playwright-core` at installed Chrome. Local command adapters, snapshots, fake-service launcher and screenshots are in `.local/`; production source was not changed to make the checks pass. Browser commands were `node .local/browser_runner.cjs demo/browser_check.js http://127.0.0.1:18770/` and `node .local/browser_runner.cjs demo/browser_check_live.js http://127.0.0.1:18770/ .local/live-caller.wav stop`. The fake key and simulated Gemini are only test fixtures. All review servers/browsers were stopped after use.

## Next steps

1. Implement a small bounded-history fix on the integration branch, with focused long-run/reset/late-result/STOP/RESUME tests; do not change the official scorer, thresholds or labels.
2. Pin and verify all ASR assets and declare the model/runtime in WRITEUP. Update browser assertions for the three-detector UI and address or narrowly classify the favicon noise.
3. Resolve the unchanged Windows replay timing failure with host evidence; retain its current tolerance. Run all relevant tests and the complete kit suite after any detector change.
4. Repeat all-24-call speed-1.0 evaluation with exact new source/assets, recording hashes, versions, R/S/Core, misses, false stops, latency, send lag and protocol errors. Validate the existing Linux image and constrained-host behavior. Real Gemini connectivity requires a separate genuine service check; fake tests cannot certify it.
5. Re-fetch destination head and repository visibility. Only when private and all publish gates are satisfied, preserve/refresh rollback, merge without overwriting newer commits, inspect staged paths/diff, and use a normal non-force push. This review has not authorized or performed an unconditional deployment.
