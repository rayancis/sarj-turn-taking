# Image for the PUBLIC demo site (see demo/DEPLOY.md). Build context: the repository root.
# Only the files the demo needs are copied, one by one. Never in the image: the Phase 3 research data
# (data/extra), evaluation evidence (experiments/, baseline/), tests, documents and .git, so no route can serve them.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 SARJ_DEMO_PUBLIC=1 OMP_NUM_THREADS=1
WORKDIR /app

COPY demo/requirements-public.txt demo/requirements-public.txt
RUN pip install --no-cache-dir -r demo/requirements-public.txt

# The two detectors: P3 (my_system.py + neural_vad.py + Silero VAD model) and the pinned Baseline file.
COPY ttbench/ ttbench/
COPY my_system.py neural_vad.py ./
COPY models/silero_vad.onnx models/SILERO_VAD_LICENSE models/
COPY demo/models/ demo/models/
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
