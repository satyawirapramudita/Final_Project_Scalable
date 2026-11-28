# Final_Project_Scalable

Platform Publikasi Karya Sastra Puisi — **microservice, event-driven, stateless**.
Transformasi dari monolit stateful [Monolithic-Stateful-AWS-Deployment](https://github.com/satyawirapramudita/Monolithic-Stateful-AWS-Deployment) (Tugas 1).

**Mata kuliah:** PACS262521 — Pengembangan Perangkat Lunak Scalable

> Status: auth, poem, nlp-worker, wordcloud, search, kbbi-linker, gateway, dan observability sudah
> berjalan end-to-end. Dataset KBBI tidak disertakan (lihat bagian "Data KBBI").

> **Stress test untuk demo kelas:** `bash loadtest/kelas.sh`, panduan di
> [`docs/Demo-Kelas-AWS.md`](docs/Demo-Kelas-AWS.md). Ini satu-satunya skenario yang dipakai saat demo.
> Skrip dan dokumen uji lain (mis. `stress_test.py`, `scale_test.py`, `Stress-Test-AWS.md`) untuk
> keperluan lain atau laporan, bukan untuk demo.

## Arsitektur

Enam layanan di belakang gateway Nginx (dua instans identik):

| Layanan | Tugas | Pemilik |
|---|---|---|
| `gateway` (Nginx) | Pintu masuk, dua instans identik (`gateway` = gateway-a dan `gateway-b`; CloudFront pindah ke gateway-b bila gateway-a gagal): routing `/api/*` ke layanan, menyajikan frontend, rate limit per IP, `X-Request-ID`, pemeriksaan akses puisi privat (`auth_request`) untuk wordcloud/kbbi, dan micro-cache wordcloud/kbbi | Anders |
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
| Observability | Prometheus, Grafana (dashboard p95/p99), alert rule, nginx-exporter, autoheal | Anders |
| Deployment AWS & gambar puisi | EC2, S3, CloudFront, Lambda generator gambar ([`docs/deployment.md`](docs/deployment.md)) | Ardi |
| Deploy AWS Academy Sandbox | 3 server `t3.small` + S3 + CloudFront dengan satu skrip ([`deploy/sandbox/`](deploy/sandbox/README.md)) | Anders |

Komunikasi: sinkron (HTTP) untuk login, baca, dan cari; asinkron (**RabbitMQ**, klaster 3 node dengan
quorum queue) untuk NLP, word cloud, KBBI, dan indeks pencarian. Store: MySQL (database terpisah per
layanan) + Redis (cache).

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

- Aplikasi (via gateway-a): **http://localhost:8080**; gateway-b (salinan identik): **http://localhost:8081**
- RabbitMQ management: **http://localhost:15672** (user/pass dari `.env`; menampilkan ketiga node klaster)
- Grafana (dashboard SLO): **http://localhost:3000** (`admin` / `GRAFANA_ADMIN_PASSWORD`), Prometheus: **http://localhost:9090**
- Cek kesehatan: `curl http://localhost:8080/health`

Menghentikan: `docker compose down` (data bertahan di volume; tambah `-v` untuk hapus).

Stack berisi **24 container** (±2,2 GiB RAM saat idle): 2 gateway, 6 layanan × 2 replika, RabbitMQ 3
node, MySQL, Redis, Prometheus, Grafana, 2 nginx-exporter, dan autoheal. Setiap container punya
`mem_limit` dan `pids_limit`, sehingga container yang bocor memori di-restart sendiri tanpa
menjatuhkan container lain.

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

**Klaster RabbitMQ.** Broker berjalan sebagai **klaster 3 node** (`rabbit-1..3`, konfigurasi di
`rabbitmq/20-cluster.conf`) dengan **quorum queue**: setiap pesan baru dikonfirmasi setelah tersimpan
di 2 dari 3 node, sehingga matinya satu node tidak menghentikan pengiriman event dan tidak
menghilangkan pesan. Klien menerima daftar ketiga node (`RABBITMQ_HOSTS`), mencobanya dalam urutan
acak, dan pindah ke node lain saat koneksi putus. Bila nlp-worker gagal menerbitkan `poem.analyzed`,
pesan asalnya diulang lalu masuk DLQ, bukan di-ack dan hilang. Uji: `docker kill puisi-rabbit-1`,
kirim puisi, dan puisi tetap dianalisis.

**Alert dan DLQ.** Prometheus memuat rule di `observability/prometheus/alerts.yml` (DLQ berisi
pesan, outbox menumpuk, node RabbitMQ mati, replika/gateway mati, breaker pencarian terbuka, rasio
5xx); statusnya di http://localhost:9090/alerts. Pesan DLQ diperiksa dan dikirim ulang dengan
`scripts/replay_dlq.py` (cara pakai di docstring skrip).

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

**Tutorial dari nol sampai running:** [`docs/deploy-aws-tutorial.md`](docs/deploy-aws-tutorial.md)
(akun AWS biasa, satu EC2 `t3.large`).

**AWS Academy Sandbox (Learner Lab):** sandbox hanya mengizinkan EC2 sampai `t3.small` dan menghapus
semua sumber daya saat sesi habis, jadi stack dipecah ke 3 server dan dibuat ulang dengan satu skrip
dari CloudShell: [`deploy/sandbox/README.md`](deploy/sandbox/README.md).

Skrip bantu ada di `deploy/`:

| Skrip | Kegunaan |
|---|---|
| `deploy/gen-secrets.sh` | membuat `.env` produksi dengan secret acak (mode 600) |
| `deploy/ec2-bootstrap.sh` | user-data EC2 Ubuntu 24.04: Docker, Compose v2, swap 4 GB, rotasi log |
| `deploy/backup-mysql.sh` | `mysqldump` semua database + unggah ke S3 (opsional) |
| `deploy/sandbox/deploy.sh` | deploy AWS Academy Sandbox: 3 × `t3.small` + S3 + CloudFront (origin group gateway-a → gateway-b) |

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
