# Image for the PUBLIC demo site (see demo/DEPLOY.md). Build context: the repository root.
# Only the files the demo needs are copied, one by one: competition data (data/), evaluation evidence
# (experiments/, baseline/), tests, documents and .git never enter the image, so no route can ever serve them.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 SARJ_DEMO_PUBLIC=1
WORKDIR /app

COPY demo/requirements-public.txt demo/requirements-public.txt
RUN pip install --no-cache-dir -r demo/requirements-public.txt

COPY ttbench/ ttbench/
COPY my_system.py my_system.py
COPY demo/server.py demo/server.py
COPY demo/static/ demo/static/
COPY demo/models/ demo/models/
COPY demo/public_data/manifest.json demo/public_data/manifest.json
COPY demo/public_data/labels/ demo/public_data/labels/
COPY demo/public_data/audio/ demo/public_data/audio/

RUN useradd --create-home --uid 10001 demo
USER demo

# Render provides $PORT (10000 by default); the server reads it in public mode.
EXPOSE 10000
CMD ["python", "demo/server.py", "--public"]
