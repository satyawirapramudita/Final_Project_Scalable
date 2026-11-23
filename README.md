# Final_Project_Scalable

Platform Publikasi Karya Sastra Puisi — **microservice, event-driven, stateless**.
Transformasi dari monolit stateful [Monolithic-Stateful-AWS-Deployment](https://github.com/satyawirapramudita/Monolithic-Stateful-AWS-Deployment) (Tugas 1).

**Mata kuliah:** PACS262521 — Pengembangan Perangkat Lunak Scalable

> Status: auth, poem, nlp-worker, wordcloud, search, kbbi-linker, gateway, dan observability sudah
> berjalan end-to-end. Dataset KBBI tidak disertakan (lihat bagian "Data KBBI").

## Arsitektur

Enam layanan di belakang satu gerbang Nginx:

| Layanan | Tugas | Pemilik |
|---|---|---|
| `gateway` (Nginx) | Pintu masuk tunggal: routing `/api/*` ke layanan, menyajikan frontend, rate limit per IP, `X-Request-ID`, pemeriksaan akses puisi privat (`auth_request`) untuk wordcloud/kbbi, dan micro-cache wordcloud/kbbi | Anders |
| `auth` | Registrasi dan login, hashing password, penerbitan JWT (stateless, tanpa sesi di server) | Ardi |
| `poem` | CRUD puisi, visibilitas publik/privat, idempotency key, cache baca, publish event lewat transactional outbox, menyimpan tag dari `poem.analyzed` | Satya |
| `nlp-worker` | Analisis teks asinkron (tag, Kata Puitif, frekuensi istilah) lalu publish `poem.analyzed` | Satya |
| `wordcloud` | Menyimpan dan menyajikan istilah dominan per puisi dari `poem.analyzed`, dengan cache Redis | Lindra, Anders |
| `search` | Indeks pencarian (MySQL FULLTEXT) yang diperbarui dari event; pencarian kata, filter kategori/tag, visibilitas, dan cache hasil | Anders |
| `kbbi-linker` | Mencocokkan Kata Puitif ke bentuk baku dan definisi KBBI (import dataset, lookup, cache) | Dzaky |

Tugas lintas layanan:

| Bagian | Isi | Pemilik |
|---|---|---|
| Kontrak event | `shared/events.py`, [`docs/event-contracts.md`](docs/event-contracts.md) | Satya |
| Observability | Prometheus, Grafana (dashboard p95/p99), nginx-exporter, autoheal | Anders |
| Deployment AWS & gambar puisi | EC2, S3, CloudFront, Lambda generator gambar ([`docs/deployment.md`](docs/deployment.md)) | Ardi |

Komunikasi: sinkron (HTTP) untuk login, baca, dan cari; asinkron (**RabbitMQ**) untuk NLP, word cloud,
KBBI, dan indeks pencarian. Store: MySQL (database terpisah per layanan) + Redis (cache).

## Struktur

```
Final_Project_Scalable/
├── gateway/                 # Nginx: nginx.conf + templates/default.conf.template
├── services/                # satu folder per layanan (Dockerfile + app/)
│   ├── auth/                #   Ardi
│   ├── poem/                #   Satya
│   ├── nlp-worker/          #   Satya
│   ├── wordcloud/           #   Lindra, Anders
│   ├── search/              #   Anders
│   ├── kbbi-linker/         #   Dzaky
│   └── _template/           #   kerangka untuk layanan baru
├── frontend/                # HTML/CSS/JS (dari Tugas 1)
├── db/init.sql              # skema auth_db, poem_db, wordcloud_db, kbbi_db, search_db
├── shared/                  # events.py (kontrak event), metrics.py (histogram latensi)
├── observability/           # Prometheus, Grafana (dashboard), plugin RabbitMQ; lihat observability/README.md
├── loadtest/                # skrip k6 (search, submit) + scale_test.py (uji 1..N replika)
├── docs/event-contracts.md  # dokumentasi kontrak event
├── lambda/                  # fitur gambar puisi (dari Tugas 1)
├── lambda-canvas-cdn/       # aset generator gambar (dari Tugas 1)
├── docker-compose.yml
├── docker-compose.prod.yml  # override port 80 untuk EC2
└── .env.example
```

## Menjalankan

```powershell
Copy-Item .env.example .env      # sesuaikan password/secret
docker compose up --build -d
```

- Aplikasi (via gateway): **http://localhost:8080**
- RabbitMQ management: **http://localhost:15672** (user/pass dari `.env`)
- Grafana (dashboard SLO): **http://localhost:3000** (`admin` / `GRAFANA_ADMIN_PASSWORD`), Prometheus: **http://localhost:9090**
- Cek kesehatan: `curl http://localhost:8080/health`

Menghentikan: `docker compose down` (data bertahan di volume; tambah `-v` untuk hapus).

**Self-healing.** Container `puisi-autoheal` me-restart layanan stateless (gateway, auth, poem,
wordcloud, search, kbbi-linker, nlp-worker) yang health check-nya gagal, kasus yang tidak ditangani
`restart: unless-stopped` (yang hanya bereaksi pada crash). Uji: `docker kill --signal=SIGSTOP <container>`
membuat layanan macet; sekitar 1 menit kemudian autoheal me-restart-nya (`docker logs puisi-autoheal`).

### Memperbarui dari versi lama (volume sudah ada)

`db/init.sql` hanya berjalan saat volume MySQL pertama kali dibuat. Setelah `git pull`:

1. Salin variabel baru dari `.env.example` ke `.env` (mis. blok `KBBI_*`; tanpa itu kbbi-linker tidak bisa login ke MySQL).
2. Bila volume dibuat sebelum ada `kbbi_db`, buat database + user-nya (atau `docker compose down -v` bila data boleh hilang):
   ```powershell
   "CREATE DATABASE IF NOT EXISTS kbbi_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE USER IF NOT EXISTS 'kbbi_user'@'%' IDENTIFIED BY 'ganti_kbbi_password'; GRANT ALL PRIVILEGES ON kbbi_db.* TO 'kbbi_user'@'%';" | docker compose exec -T mysql sh -c 'MYSQL_PWD=$MYSQL_ROOT_PASSWORD mysql -uroot'
   ```
3. `docker compose up -d --build --remove-orphans`
4. Puisi lama belum punya word cloud / KBBI: `Get-Content scripts/reanalyze_poems.py | docker compose exec -T poem python -`

Kolom `visibilitas` (puisi publik/privat) ditambahkan otomatis oleh layanan poem dan search saat
start (`ALTER TABLE` bila belum ada); puisi lama menjadi publik. Cukup langkah 3 (`--build`).

### Data KBBI

Dataset KBBI tidak ada di repo (lisensi). Tanpa import, semua Kata Puitif tampil "tidak ditemukan".
Cara import ada di [`services/kbbi-linker/README.md`](services/kbbi-linker/README.md); setelahnya jalankan
`scripts/reanalyze_poems.py` (langkah 4 di atas) agar puisi lama ikut terhubung.

## Peta endpoint

Semua endpoint selain register/login butuh header `Authorization: Bearer <jwt>`.

| Endpoint | Layanan |
|---|---|
| `POST /api/auth/register`, `/login`, `/logout`, `GET /me` | auth |
| `GET/POST /api/poems` (puisi saya), `GET /api/poems/{id}` (publik, atau privat milik sendiri), `PUT/DELETE /api/poems/{id}` (pemilik) | poem |
| `GET /api/wordcloud/{poem_id}` (akses diperiksa gateway) | wordcloud |
| `GET /api/search?q=&kategori=&tag=&page=` (puisi publik + privat milik sendiri) | search |
| `GET /api/kbbi/{poem_id}` (akses diperiksa gateway) | kbbi-linker |

**Visibilitas.** `POST`/`PUT /api/poems` menerima `"visibilitas": "public" | "private"` (default
`public`). Puisi privat hanya terlihat oleh pemiliknya; pengguna lain mendapat 404 di poem, search,
wordcloud, dan kbbi. Uji kebocoran end-to-end:
`Get-Content scripts/check_visibility.py | docker compose exec -T poem python -`

**Idempotency key.** `POST /api/poems`, `PUT/DELETE /api/poems/{id}` menerima header
`Idempotency-Key: <string-unik>`. Klien yang mengirim ulang request dengan key + payload yang sama
menerima respons pertama kembali (header `Idempotent-Replay: true`) tanpa data ganda; key dengan
payload berbeda ditolak `409 Conflict`. Key diingat 24 jam (`IDEMPOTENCY_TTL`, detik) dan di-scope
per user — tidak bisa dipakai lintas akun. Tanpa header, perilaku tidak berubah. Frontend sudah
mengirim key otomatis pada submit dan mengulang otomatis (maks 3 attempt, backoff eksponensial
berjitter) untuk GET dan submit saat jaringan/5xx.

**Connection pool.** poem dan auth memakai pool MySQL (DBUtils): koneksi dibuat ulang otomatis bila
MySQL restart (`ping=1`). Ukuran via env `DB_POOL_MIN` (default 2) dan `DB_POOL_MAX` (default 10);
naikkan `DB_POOL_MAX` saat stress test bila MySQL punya `max_connections` lebih besar. Pemakaian pool
pantau lewat metrik `db_pool_connections{service,state=idle|in_use}`; hasil dedup idempotency lewat
`idempotency_total{service,result=new|replay|conflict}`.

**Replika.** auth, poem, search, wordcloud, kbbi-linker, dan nlp-worker berjalan **2 replika** secara
default (`deploy.replicas`). Ubah lewat `.env` (`AUTH_REPLICAS`, `POEM_REPLICAS`, `SEARCH_REPLICAS`,
`WORDCLOUD_REPLICAS`, `KBBI_REPLICAS`, `NLP_WORKER_REPLICAS`) atau sementara dengan
`docker compose up -d --scale search=4`. Gateway membagi request ke semua replika lewat DNS Docker
(`resolver 127.0.0.11 valid=10s`), Prometheus men-scrape tiap replika, dan worker berbagi antrean
(competing consumers). Satu replika mati tidak memutus layanan.

**Transactional outbox (poem).** Perubahan puisi dan event-nya (`poem.created/updated/deleted`) ditulis
dalam **satu transaksi MySQL** (tabel `outbox`), lalu relay di tiap replika poem mengirimnya ke RabbitMQ
(`SELECT ... FOR UPDATE SKIP LOCKED`, publisher confirms). Saat RabbitMQ mati, submit tetap sukses dan
event dikirim otomatis begitu broker pulih; tidak ada event yang hilang. Pantau lewat
`poem_outbox_pending`, `poem_outbox_oldest_pending_seconds`, `poem_outbox_published_total`, dan
`poem_outbox_relay_errors_total`. Pengiriman bersifat *at-least-once* (konsumen sudah idempoten).

**Uji skalabilitas.** `python loadtest/scale_test.py` menjalankan k6 lewat gateway pada 1–4 replika
search dan mencetak throughput, p50/p95/p99, serta CPU per replika (hasil di `loadtest/results/`).

**Micro-cache gateway.** Nginx menyimpan respons 200 `GET /api/wordcloud/{id}` dan `/api/kbbi/{id}`
selama `API_CACHE_TTL` (default 30 dtk), dibagi antar-pengguna karena isinya tidak bergantung pada
pengguna. Aman: `auth_request` (pemeriksaan akses puisi) tetap berjalan **sebelum** cache dibaca, jadi
token tidak valid tetap 401 dan puisi yang dijadikan privat atau dihapus langsung 404 walau ada
salinannya di cache. `proxy_cache_lock` mencegah stampede, `proxy_cache_use_stale` tetap menjawab saat
layanan mati, dan kbbi mengirim `Cache-Control: no-store` untuk hasil yang belum final (`pending`).
Status terlihat di header `X-Cache-Status` dan field `cache` log gateway. search dan poem **tidak**
di-cache di gateway (isinya bergantung pada pengguna). Matikan dengan `API_CACHE=off`; bandingkan
dengan `python loadtest/cache_test.py`.

## Kontrak event

Lihat [`docs/event-contracts.md`](docs/event-contracts.md) dan [`shared/events.py`](shared/events.py).
Pemilik schema: **Satya**. Perubahan bersifat *additive*.

## Deployment

Rencana & langkah deployment AWS ada di [`docs/deployment.md`](docs/deployment.md)
(EC2 + Docker Compose; S3 + CloudFront + Lambda; region `us-east-1`).

## Aturan kontribusi

1. Setiap layanan punya `Dockerfile` + `/health` + `/metrics`.
2. Satu Compose file; perubahan infra lewat PR.
3. Secret lewat `.env` (tidak di-commit).
4. Branch per layanan; merge setelah `docker compose up` hijau.

## Keputusan terbuka

- [ ] Framework service HTTP: FastAPI (dipakai di skeleton ini) atau stdlib.
- [ ] Mesin pencarian: MySQL FULLTEXT atau Meilisearch.
- [ ] Pustaka NLP bahasa Indonesia.
- [ ] Ukuran instance EC2 & anggaran AWS.
- [ ] Fitur gambar puisi (Lambda) dipertahankan atau tidak.
