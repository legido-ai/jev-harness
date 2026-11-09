# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    LEDGER_PATH=/data/ledger.jsonl \
    QUESTIONS_PATH=/app/examples/email_triage.questions.json

WORKDIR /app

RUN groupadd --system --gid 10001 harness \
 && useradd --system --uid 10001 --gid harness --home /app harness \
 && mkdir -p /data && chown harness:harness /data

COPY pyproject.toml README.md ./
COPY src ./src
COPY examples ./examples
COPY docs ./docs

RUN pip install --no-cache-dir . && rm -rf /root/.cache

USER harness

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "jev_harness.api:app", "--host", "0.0.0.0", "--port", "8080"]
