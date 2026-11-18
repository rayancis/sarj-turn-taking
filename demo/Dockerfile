# Exact public image inputs. Legacy algorithms, research data and local recordings are not copied.
FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 SARJ_DEMO_PUBLIC=1 OMP_NUM_THREADS=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY demo/requirements-public.txt demo/requirements-public.txt
RUN pip install --no-cache-dir -r demo/requirements-public.txt && pip check
COPY ttbench/ ttbench/
COPY demo/models/sarj_v3/ demo/models/sarj_v3/
RUN python demo/models/sarj_v3/tt/assets.py --download
COPY demo/server.py demo/live.py demo/collections.json demo/benchmark.json demo/
COPY demo/static/ demo/static/
COPY data/dev/manifest.json data/dev/
COPY data/dev/labels/ data/dev/labels/
COPY data/dev/audio/ data/dev/audio/
RUN useradd --create-home --uid 10001 demo && chmod -R a-w /app/demo/models/sarj_v3
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
USER demo
EXPOSE 10000
CMD ["python", "demo/server.py", "--public"]
