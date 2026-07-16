FROM python:3.12-slim

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

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

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
