# Image for the PUBLIC demo site (see demo/DEPLOY.md). Build context: the repository root.
# Only the files the demo needs are copied, one by one. Never in the image: the competition recordings and labels
# (data/dev), evaluation evidence (experiments/, baseline/), tests, documents and .git, so no route can serve them.
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
COPY demo/server.py demo/collections.json demo/benchmark.json demo/
COPY demo/static/ demo/static/
# The 50 Phase 3 constructed scenarios (publicly licensed, with attribution) and their source records.
COPY data/extra/SOURCES.md data/extra/sources.json data/extra/
COPY data/extra/xdev/manifest.json data/extra/xdev/design.json data/extra/xdev/
COPY data/extra/xdev/labels/ data/extra/xdev/labels/
COPY data/extra/xdev/audio/ data/extra/xdev/audio/
COPY data/extra/holdout/manifest.json data/extra/holdout/design.json data/extra/holdout/
COPY data/extra/holdout/labels/ data/extra/holdout/labels/
COPY data/extra/holdout/audio/ data/extra/holdout/audio/

RUN useradd --create-home --uid 10001 demo
USER demo

# Render provides $PORT (10000 by default); the server reads it in public mode.
EXPOSE 10000
CMD ["python", "demo/server.py", "--public"]
