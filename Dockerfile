FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for pdfplumber and OCR (tesseract)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["gunicorn", "main:app", \
    "--worker-class", "uvicorn.workers.UvicornWorker", \
    "--workers", "2", \
    "--timeout", "300", \
    "--graceful-timeout", "30", \
    "--bind", "0.0.0.0:8000", \
    "--limit-request-line", "0", \
    "--limit-request-field_size", "0"]
