# Image for the PUBLIC demo site (see demo/DEPLOY.md). Build context: the repository root.
# Only the files the demo needs are copied, one by one. Never in the image: the Phase 3 research data
# (data/extra), evaluation evidence (experiments/, baseline/), tests, documents and .git, so no route can serve them.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 SARJ_DEMO_PUBLIC=1 OMP_NUM_THREADS=1
WORKDIR /app

COPY demo/requirements-public.txt demo/requirements-public.txt
RUN pip install --no-cache-dir -r demo/requirements-public.txt

# The detectors: P3 (my_system.py + neural_vad.py + Silero VAD model), the pinned Baseline file and SARJ v3
# (demo/models/sarj_v3: my_system.py + tt/, every file pinned in demo/server.py).
COPY ttbench/ ttbench/
COPY my_system.py neural_vad.py ./
COPY models/silero_vad.onnx models/SILERO_VAD_LICENSE models/
COPY demo/models/ demo/models/
# SARJ v3's words layer: Whisper tiny (OpenAI, MIT; CTranslate2 conversion Systran/faster-whisper-tiny), fetched once
# at build time so the running site never downloads anything. If this step is removed, v3 runs on acoustics alone.
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-tiny', local_dir='demo/models/sarj_v3/models/faster-whisper-tiny')"
# The demo itself.
COPY demo/server.py demo/live.py demo/collections.json demo/benchmark.json demo/
COPY demo/static/ demo/static/
# The 24 original competition recordings, published with the supervisor's permission (demo/collections.json).
# Only the manifest, labels and audio; the 50 Phase 3 research scenarios (data/extra) are not part of the demo.
COPY data/dev/manifest.json data/dev/
COPY data/dev/labels/ data/dev/labels/
COPY data/dev/audio/ data/dev/audio/

RUN useradd --create-home --uid 10001 demo
USER demo

# Render provides $PORT (10000 by default); the server reads it in public mode.
EXPOSE 10000
CMD ["python", "demo/server.py", "--public"]
