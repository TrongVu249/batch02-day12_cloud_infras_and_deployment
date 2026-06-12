# ============================================================
# Production Dockerfile for Scaling & Reliability Part 5
# ============================================================

# Stage 1: Builder
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY 05-scaling-reliability/production/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim AS runtime

# Create agent user with home directory /app
RUN groupadd -r agent && useradd -r -g agent -d /app agent

WORKDIR /app

# Copy installed dependencies directly to /app/.local
COPY --from=builder /root/.local /app/.local

# Copy application source code and mock LLM helper
COPY 05-scaling-reliability/production/app.py ./app.py
COPY 05-scaling-reliability/production/utils/ ./utils/

RUN chown -R agent:agent /app

USER agent

ENV PATH=/app/.local/bin:$PATH
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
