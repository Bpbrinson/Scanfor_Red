# Scanfor_Red container image.
#
# Bundles Python, the OpenCV/Tesseract Python packages, AND the Tesseract OCR
# engine itself -- so there is nothing to install on the host except Docker.
FROM python:3.12-slim

# System packages:
#   tesseract-ocr  -> the OCR engine pytesseract calls (incl. English data)
#   libglib2.0-0   -> a shared lib opencv-python-headless needs at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so this layer is cached between code changes.
# gunicorn (added here, not in requirements.txt) serves the web frontend; it is
# Linux-only, so it stays out of the cross-platform requirements file.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Application code, web frontend, and the config tables (service enrichment +
# known-error ticket registry).
COPY scanfor_red.py enrich.py generate_excel.py ticket_registry.py \
     service_lookup.json ticket_registry.json app.py ./
COPY templates ./templates
COPY static ./static

# Inside the container Tesseract is on PATH, so pytesseract finds it with no
# TESSERACT_CMD needed.
EXPOSE 5000

# Default: serve the web frontend on port 5000. A long worker timeout covers
# slow multi-image OCR requests. The compose 'cli' service overrides this
# command to run the batch processor (python scanfor_red.py) instead.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "600", "app:app"]
