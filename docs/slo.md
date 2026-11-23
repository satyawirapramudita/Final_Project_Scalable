# SLI, SLO, dan Batas Kemampuan Pencarian

Target keandalan aplikasi Poetify, cara mengukurnya (Bab 8 dan 11.7), dan hasil stress test yang
menunjukkan sampai mana target itu bertahan untuk layanan search (Bab 10.5). Pemilik: Anders.

## SLI dan SLO

Nilai di bawah sama dengan garis merah di dashboard Grafana (`observability/README.md`).

| SLI | Cara ukur | SLO | Panel Grafana |
|---|---|---|---|
| Ketersediaan API | `1 - sum(rate(http_requests_total{status=~"5.."}[30d])) / sum(rate(http_requests_total[30d]))` | 99 persen per 30 hari | Error 5xx |
| Latensi pencarian | `histogram_quantile(0.95 / 0.99, http_request_duration_seconds{service="search"})` | p95 < 300 ms dan p99 < 500 ms | Search: pencarian |
| Latensi baca puisi | idem, `GET /api/poems/{poem_id}` | p95 < 300 ms | Poem: baca puisi |
| Latensi kirim puisi | idem, `POST /api/poems` | p95 < 500 ms | Poem: submit puisi |
| Kebenaran pemrosesan event | isi dead-letter queue | DLQ = 0, queue terkuras setelah beban | Pesan di DLQ |

p99 dipakai di samping p95 karena rata-rata dan p95 menyembunyikan ekor: 1 dari 100 pengguna yang
menunggu lebih dari setengah detik tetap terasa (Bab 10.1).

**Error budget.** SLO 99 persen per 30 hari memberi anggaran 1 persen x 43.200 menit = **432 menit**
(7,2 jam) gangguan per bulan, atau 1 dari 100 request boleh gagal. Selama anggaran masih ada, fitur baru
boleh dirilis; bila habis, prioritas pindah ke keandalan (misalnya outbox poem, replika MySQL).

**SLA** tidak ditetapkan. Ini proyek kuliah tanpa pelanggan berbayar; SLO di atas adalah target
internal tim.

## Stress test pencarian

`python loadtest/stress_test.py` menaikkan pengguna virtual (VU) bertahap 1, 5, 10, ... 320. Setiap VU
mengirim request berikutnya begitu jawaban datang, dengan campuran query dari `loadtest/search.js`:
60 persen kata populer, 30 persen kombinasi dua kata acak, 10 persen dengan filter kategori dan
halaman. Sebuah tahap **melanggar** SLO bila p95 >= 300 ms, p99 >= 500 ms, atau error >= 1 persen.

Kondisi: 23 November 2026, `main` setelah `1bba39f`, 2 replika search, 1000 puisi seed (user 99) +
146 puisi asli, laptop 16 thread (Docker Desktop, 6 GB RAM untuk VM), klien k6 di mesin yang sama dan
masuk lewat gateway. Rate limit gateway dinaikkan selama uji karena semua beban datang dari satu IP.

### Tanpa micro-cache gateway (kapasitas layanan search sendiri)

`python loadtest/stress_test.py --no-gateway-cache`. Cache Redis di dalam search tetap aktif.

| VU | Throughput (r/s) | p50 (ms) | p95 (ms) | p99 (ms) | Error | CPU search (%) | CPU MySQL (%) | SLO |
|---:|---:|---:|---:|---:|---:|---|---:|---|
| 1 | 159,6 | 4,5 | 12,1 | 16,1 | 0 % | 34, 33 | 19 | ok |
| 5 | 332,7 | 14,1 | 29,7 | 49,3 | 0 % | 99, 96 | 12 | ok |
| 10 | 350,7 | 27,7 | 56,9 | 84,3 | 0 % | 123, 118 | 23 | ok |
| 20 | 360,7 | 54,3 | 109,6 | 157,2 | 0 % | 122, 115 | 23 | ok |
| 40 | 363,3 | 117,7 | 222,7 | 289,2 | 0 % | 124, 106 | 19 | ok |
| 80 | 364,1 | 224,6 | 366,6 | 438,5 | 0 % | 124, 124 | 21 | **melanggar** (p95) |
| 160 | 366,4 | 438,8 | 581,9 | 690,0 | 0 % | 124, 124 | 20 | **melanggar** |
| 320 | 371,1 | 856,4 | 1145,9 | 1400,3 | 0 % | 123, 123 | 23 | **melanggar** |

- **Titik jenuh** sekitar 10 VU: throughput berhenti di kisaran 350 sampai 370 r/s. Setiap tambahan
  pengguna sesudahnya hanya menambah antrean, sehingga latensi naik hampir lurus dengan jumlah VU
  (hukum Little: 80 VU / 364 r/s = 220 ms, sama dengan p50 terukur).
- **Titik patah SLO** di antara 40 dan 80 VU untuk p95 < 300 ms, dan di antara 80 dan 160 VU untuk
  p99 < 500 ms.
- **Tidak ada error di tahap mana pun.** Sistem melambat, bukan rusak: pool koneksi, breaker, dan
  batas `limit`/`page` menahan beban tanpa 5xx.
- **Bottleneck: proses Python search**, bukan basis data. Setiap replika menjalankan satu worker
  uvicorn dan berhenti di sekitar 120 persen CPU, sementara MySQL hanya 12 sampai 23 persen.
  Karena itu menambah replika menaikkan kapasitas hampir lurus (lihat uji skalabilitas di bawah).

### Dengan micro-cache gateway (konfigurasi bawaan)

`python loadtest/stress_test.py`. Gateway menyimpan hasil pencarian 10 detik per token pengguna.

| VU | Throughput (r/s) | p50 (ms) | p95 (ms) | p99 (ms) | Error | CPU gateway (%) | SLO |
|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 420,5 | 0,5 | 12,2 | 17,1 | 0 % | 26 | ok |
| 5 | 4.256,1 | 0,6 | 1,9 | 12,1 | 0 % | 185 | ok |
| 10 | 6.497,8 | 0,7 | 2,4 | 17,8 | 0 % | 236 | ok |
| 20 | 10.626,8 | 0,8 | 3,5 | 16,1 | 0 % | 322 | ok |
| 40 | 11.120,6 | 1,2 | 7,1 | 26,4 | 0 % | 417 | ok |
| 80 | 5.438,1 | 2,3 | 34,0 | 366,5 | 0 % | 463 | ok |
| 160 | 10.339,7 | 4,2 | 33,8 | 184,8 | 0 % | 263 | ok |
| 320 | 11.223,3 | 7,1 | 57,3 | 157,8 | 0 % | 399 | ok |

SLO terpenuhi sampai 320 VU dan throughput naik sekitar 30 kali, tetapi angka ini **terlalu optimis**:
semua VU memakai satu token dan kosakata query kecil, sehingga hampir setiap request terjawab dari
cache gateway. Pengguna sungguhan memakai token berbeda dan jarang mengulang query yang persis sama
dalam 10 detik, jadi kapasitas nyata lebih dekat ke tabel tanpa cache. Yang terukur di sini adalah
Nginx, yang menjadi bottleneck baru (hingga 463 persen CPU). Penurunan pada tahap 80 VU muncul satu
kali dan tidak diselidiki lebih lanjut.

### Skalabilitas horizontal (uji terpisah)

`python loadtest/scale_test.py`, 64 VU tetap, tanpa micro-cache search di gateway saat itu.

| Replika search | Throughput (r/s) | Speedup | p95 (ms) | p99 (ms) | CPU MySQL (%) |
|---:|---:|---:|---:|---:|---:|
| 1 | 158,7 | 1,00x | 502,0 | 575,1 | 15 |
| 2 | 329,1 | 2,07x | 350,8 | 388,1 | 16 |
| 3 | 442,5 | 2,79x | 323,4 | 388,9 | 9 |
| 4 | 526,5 | 3,32x | 292,9 | 391,8 | 8 |

Satu replika melayani sekitar 160 r/s. Pada 64 VU, p95 baru turun di bawah 300 ms dengan 4 replika.

### Pembaruan pengukuran (24 November 2026)

Tabel di atas diambil pada lingkungan Docker Desktop (VM 6 GB) sebelum beberapa perbaikan.
Pengukuran ulang di host 16 thread setelah perbaikan berikut:

- micro-cache pencarian **per pengguna** di gateway (`API_SEARCH_CACHE_TTL`, kunci memuat token),
- mitigasi TIME_WAIT/ephemeral port gateway (`net.ipv4.tcp_tw_reuse=1`, port range 10240–65535),
- k6 `discardResponseBodies` + container k6 berjalan sebagai UID pemanggil,
- metrik CPU dirata-ratakan dari 3 sampel (bukan satu instan).

**Tanpa micro-cache gateway, 1 worker uvicorn per replika** (`--no-gateway-cache`):

| VU | Throughput (r/s) | p50 (ms) | p95 (ms) | p99 (ms) | Error | CPU search/replika | CPU Redis | CPU MySQL |
|---:|---:|---:|---:|---:|---|---:|---:|---:|
| 1 | 701,6 | 1,0 | 4,4 | 8,1 | 0 % | 29 % | 4 % | 12 % |
| 10 | 1.992,0 | 4,9 | 9,2 | 11,9 | 0 % | 64 % | 5 % | 5 % |
| 40 | 2.014,0 | 19,8 | 37,9 | 46,3 | 0 % | 68 % | 4 % | 3 % |
| 80 | **2.090,2** | 38,6 | 72,2 | 82,3 | 0 % | 71 % | 6 % | 26 % |

- Titik jenuh **±10 VU** (1.992 r/s sudah 95 % puncak); setelah itu latensi naik hampir linear
  (hukum Little) tanpa error. p95 tetap 72 ms di 80 VU, jadi SLO 300 ms **belum** terlanggar.
- Bottleneck tetap **proses Python per replika** (71 % CPU); Redis 4–6 % dan MySQL 3–26 % bukan bottleneck.

**Dengan micro-cache gateway** (konfigurasi bawaan):

| VU | Throughput (r/s) | p95 (ms) | Cache hit | CPU gateway | CPU k6 |
|---:|---:|---:|---:|---:|---:|
| 10 | 61.398 | 0,2 | 100 % | 149 % | 597 % |
| 40 | **85.254** | 1,1 | 100 % | 182 % | 777 % |
| 80 | 84.634 | 2,2 | 100 % | 200 % | 832 % |

- Angka ini **batas bawah**: yang jenuh adalah **klien k6** (777–832 % CPU, 8+ core), bukan gateway
  (≤200 %). Dengan dua container k6, plafon gateway kemungkinan jauh lebih tinggi.
- Tetap berlaku peringatan di atas: satu token + kosakata kecil membuat hit ratio ~100 %,
  sehingga ini mengukur nginx, bukan layanan search.

**Dengan `SEARCH_WORKERS=2` (dua proses uvicorn per container, tanpa micro-cache):**

| VU | Throughput (r/s) | p50 (ms) | p95 (ms) | Error | CPU search/replika |
|---:|---:|---:|---:|---|---:|
| 10 | 3.184,4 | 2,9 | 5,4 | 0 % | 126–129 % |
| 40 | **3.336,1** | 11,8 | 22,4 | 0 % | 136–137 % |
| 80 | 3.165,0 | 23,9 | 49,9 | 0 % | 135–140 % |

- Dua worker menaikkan kapasitas per replika dari ±1.050 r/s menjadi ±1.670 r/s (**1,66×**, efisiensi
  ±83 %), dan menurunkan p50 pada beban yang sama (19,8 → 11,8 ms di 40 VU).
- Metrik Prometheus tetap benar: `search_breaker_state` muncul sebagai **2 seri per replika**
  (label `pid`) karena digabung lewat `PROMETHEUS_MULTIPROC_DIR`.
- Setiap worker membuka pool DB sendiri (2 replika × 2 worker × `DB_POOL_SIZE` 10 = 40 koneksi
  hanya untuk search) — perhitungkan `max_connections` MySQL.

## Batasan pengukuran

- **Satu IP dan satu token.** Rate limit per IP harus dinaikkan, dan cache per pengguna terlihat lebih
  efektif daripada kenyataannya.
- **Klien dan server di satu mesin.** k6 memakai CPU yang sama dengan layanan; angka mutlak bergantung
  pada laptop. Yang dicari adalah bentuk kurva dan letak titik patahnya.
- **MySQL dipakai bersama semua layanan.** Saat uji ini MySQL bukan bottleneck (paling tinggi 23 persen
  tanpa cache), tetapi uji beban campuran (pencarian dan kirim puisi bersamaan) akan bersaing di
  instance yang sama. MySQL juga titik gagal tunggal untuk semua layanan.
- **Satu host.** Semua replika berada di mesin yang sama, jadi menambah replika hanya membantu selama
  masih ada core CPU yang menganggur.

## Langkah berikutnya

1. ~~Jalankan uvicorn dengan beberapa worker per container.~~ **Selesai (24 Nov 2026):**
   `SEARCH_WORKERS` (default 1) di `docker-compose.yml`; diuji 2 worker → 1,66× kapasitas
   per replika, metrik tetap teragregasi.
2. Tambahkan alert Prometheus untuk SLO di atas (p95/p99 search, rasio 5xx, DLQ tidak kosong).
3. Uji beban campuran pencarian dan kirim puisi untuk melihat persaingan di MySQL.
