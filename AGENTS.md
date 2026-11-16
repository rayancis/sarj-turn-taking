# Shared constraints and independent review

Read PROJECT.md, INTERFACE.md, SCORING.md and CHALLENGE.md before work.

- ChatGPT and the user decide scope and interpret measurements. Claude Code is the
  primary implementer. Codex independently reviews changes and verifies tests;
  during review, report findings before making fixes. Do not edit concurrently.
- Keep my_system.py as the entry point and preserve the kit's paths and protocol.
  Do not change ttbench/, schema/, dev labels or scoring thresholds to improve a
  result. Report a suspected upstream defect separately with a reproducer.
- Decisions must depend only on audio already received and permitted streamed
  context. Never read dev labels, caller transcripts, scripts, scenario IDs or
  future audio from inference code. Agent text supplied by the harness is allowed.
- After bootstrap, new implementation goes on one focused branch. Preserve the original baseline commit
  0915ace and its measurements. Make the smallest evidence-supported change.
- The 24 supplied synthetic dev calls are diagnostic examples, not proof of
  generalisation. Do not optimise to their IDs, exact waveforms or label times.
- Keep audio handling bounded and non-blocking; answer pings. Slow workers must
  discard stale results across scenarios/utterances. Load models before accepting
  connections. Review reset behaviour and STOP/RESUME state transitions explicitly.
- Do not persist checkpoint/final audio, customer data, private recordings or
  credentials. Supplied dev data is approved by the user for this PRIVATE repo.
  Keep repository visibility private. Ignore rules are not a security boundary.
- Declare every new model, API and dataset in WRITEUP.md. Do not invent a team
  code, a calendar deadline, a score or a completed readiness/connection check.
- Run relevant tests, then the existing full kit suite for a proposed algorithm
  change. Use speed 1.0 on all 24 calls for reported comparisons; --fast is only
  debugging. Record commit, data hash, versions, R/S/Core, misses, false stops,
  latency, protocol errors and runtime limitations. Keep raw runs local by default.
- Inspect staged paths and diff before committing. Never force-push or upload
  hidden/checkpoint material. No CI, containers, extra services or scaffold unless
  a concrete need is demonstrated.

Codex review priorities: interface compatibility, causal processing and leakage,
state resets, bounded latency/memory, recording restrictions, meaningful tests,
and whether the reported improvement follows from comparable real-time runs.
