FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/search/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
COPY services/search/app ./app

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8004

# Satu proses uvicorn memakai satu core (GIL), sehingga kapasitas per container
# ~1.000 r/s. UVICORN_WORKERS menaikkannya tanpa perlu menambah replika.
# Default 1 = perilaku lama. Metrik dari semua worker digabung lewat
# PROMETHEUS_MULTIPROC_DIR (lihat app/main.py:/metrics); direktori itu harus
# kosong saat start, jadi dibersihkan lebih dulu.
ENV UVICORN_WORKERS=1 \
    PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-multiproc

CMD ["sh", "-c", "rm -rf \"$PROMETHEUS_MULTIPROC_DIR\" && mkdir -p \"$PROMETHEUS_MULTIPROC_DIR\" && exec uvicorn app.main:app --host 0.0.0.0 --port 8004 --workers \"$UVICORN_WORKERS\""]
