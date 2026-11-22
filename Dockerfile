# Official slim image with a pinned tag, so every build gets the same base.
FROM python:3.14.10-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/app

# LibreOffice turns DOC/DOCX/ODS/XLSX into PDF (MPL-2.0).
# Tesseract with Polish reads scans (Apache-2.0).
# Metric-compatible fonts keep the layout of converted Office files.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui libreoffice-calc-nogui \
      tesseract-ocr tesseract-ocr-pol \
      fonts-liberation fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# The service never runs as root.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
