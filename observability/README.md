# Observability: Prometheus + Grafana

Pemilik: Anders. Metrik semua layanan, kedalaman queue RabbitMQ, dan gateway dalam satu dashboard.

## Membuka

| Alat | URL (lokal) | Login |
|---|---|---|
| Grafana | http://localhost:3000 | `admin` / `GRAFANA_ADMIN_PASSWORD` dari `.env` (default dev: `admin`) |
| Prometheus | http://localhost:9090 | - |
| RabbitMQ UI | http://localhost:15672 | `RABBITMQ_USER` / `RABBITMQ_PASSWORD` |

Grafana langsung membuka dashboard **"Puisi: ringkasan sistem"** (refresh 5 dtk). Data source dan dashboard dimuat dari file
di `observability/grafana/`, jadi tidak perlu klik apa pun setelah `docker compose up -d`.
Port Prometheus dan Grafana hanya dibind ke `127.0.0.1`.

## Isi dashboard dan kaitannya dengan SLO

| Panel | SLO / tujuan | Sumber |
|---|---|---|
| Poem: baca puisi (p95) | p95 < 300 ms | `http_request_duration_seconds`, `GET /api/poems/{poem_id}` |
| Poem: submit puisi (p95) | p95 < 500 ms | `POST /api/poems` |
| Search: pencarian (p95) | p95 < 300 ms | `/api/search` |
| Error 5xx | < 1% request (ketersediaan 99%) | `http_requests_total{status=~"5.."}` |
| Kedalaman queue, DLQ, consumer per queue | queue terkuras setelah beban; DLQ = 0 | plugin `rabbitmq_prometheus` (per queue) |
| Cache hit: search / poem | bahan penyetelan TTL | `search_cache_total`, `poem_cache_total` |
| Gateway: request/s, koneksi aktif | beban masuk | `nginx-prometheus-exporter` (stub_status) |
| Search: event indeks, pool DB habis | kesehatan indeks | `search_events_total`, `search_db_pool_timeouts_total` |

Garis merah pada panel latensi = batas SLO. Keterlambatan analisis (`poem.created` ke `poem.analyzed`, target 30 dtk)
belum punya panel: belum ada metrik waktu antar-event; yang tersedia hanya kedalaman queue `nlp.q`.

## Cara kerja

- **Scrape tiap 5 dtk.** Layanan HTTP ditemukan lewat DNS Docker: `docker compose up -d --scale search=3 search` membuat
  ketiga replika otomatis ter-scrape (diuji), tanpa mengubah konfigurasi.
- **Label `path` memakai template route** (`/api/poems/{poem_id}`), lewat `shared/metrics.py`. URL mentah akan membuat satu
  deret waktu per id puisi.
- **RabbitMQ**: plugin `rabbitmq_prometheus` diaktifkan lewat `observability/rabbitmq/enabled_plugins`. Metrik per queue
  diambil dari `/metrics/detailed`.
- **Retensi 3 hari**; Prometheus memakai volume `prometheus_data`, Grafana `grafana_data`.

## Memakai `shared/metrics.py` di layanan lain

Saat ini `auth`, `poem`, `search`, `wordcloud`, dan `kbbi-linker` memiliki histogram latensi. `tts` masih kerangka dan
`nlp-worker` tidak punya API HTTP (lihat metrik `worker_*`-nya). Tambahkan ke middleware layanan masing-masing agar muncul di dashboard:

```python
import time
from shared.metrics import REQUEST_SECONDS, route_path

@app.middleware("http")
async def observe(request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    path = route_path(request)
    REQUEST_COUNT.labels(config.SERVICE_NAME, request.method, path, response.status_code).inc()
    REQUEST_SECONDS.labels(config.SERVICE_NAME, request.method, path).observe(time.perf_counter() - started)
    return response
```

Jangan mendefinisikan histogram `http_request_duration_seconds` sendiri di layanan itu; pakai yang dari `shared.metrics`.

## Di EC2

Port tidak dibuka ke internet (Security Group cukup port 22 dan 80). Akses lewat SSH tunnel dari laptop:

```bash
ssh -L 3000:localhost:3000 -L 9090:localhost:9090 -L 15672:localhost:15672 ubuntu@<IP_EC2>
```

Lalu buka http://localhost:3000. Ganti `GRAFANA_ADMIN_PASSWORD` di `.env` sebelum deploy.

## Memori

Terukur lokal tanpa beban berat: Prometheus ~50 MiB, Grafana ~70 MiB, nginx-exporter ~10 MiB. Seluruh 14 container ~1,0 GiB.
Prometheus tumbuh seiring jumlah deret waktu dan lama retensi; ukur ulang saat uji beban di instance EC2.

## Batasan yang diketahui

- **Tidak ada metrik CPU/memori container** (butuh cAdvisor). Gunakan `docker stats` selama uji beban.
- **Tidak ada alert rule**; DLQ dan error 5xx hanya berubah merah di dashboard.
- Latensi gateway tidak tersedia (stub_status tidak memuat latensi); gunakan histogram per layanan.
