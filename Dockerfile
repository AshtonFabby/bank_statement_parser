FROM python:3.12-slim

# Copy the official uv binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install system dependencies for pdfplumber and OCR (tesseract).
# libjemalloc2 replaces glibc malloc: glibc's per-thread arenas fragment badly
# in long-running Python processes, inflating RSS until OOM on small instances.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    tesseract-ocr \
    libjemalloc2 \
    && rm -rf /var/lib/apt/lists/*

ENV LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2
ENV UV_COMPILE_BYTECODE=1
ENV PATH="/app/.venv/bin:$PATH"

# Install dependencies using uv lockfile for fast, cached layer builds
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .

EXPOSE 8000

# --max-requests recycles the worker periodically so fragmentation/leaks
# can't accumulate indefinitely on a 1GB instance.
CMD ["gunicorn", "main:app", \
    "--worker-class", "uvicorn_worker.UvicornWorker", \
    "--workers", "1", \
    "--timeout", "300", \
    "--graceful-timeout", "30", \
    "--max-requests", "200", \
    "--max-requests-jitter", "50", \
    "--bind", "0.0.0.0:8000"]
