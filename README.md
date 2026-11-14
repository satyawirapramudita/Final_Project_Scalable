# Final_Project_Scalable

Platform Publikasi Karya Sastra Puisi — **microservice, event-driven, stateless**.
Transformasi dari monolit stateful [Monolithic-Stateful-AWS-Deployment](https://github.com/satyawirapramudita/Monolithic-Stateful-AWS-Deployment) (Tugas 1).

**Mata kuliah:** PACS262521 — Pengembangan Perangkat Lunak Scalable

> Status: auth, poem, nlp-worker, wordcloud, search, kbbi-linker, gateway, dan observability sudah
> berjalan end-to-end. TTS ditunda. Dataset KBBI tidak disertakan (lihat bagian "Data KBBI").

## Arsitektur target

Tujuh layanan di balik satu gerbang Nginx:

| Layanan | Peran | Pemilik |
|---|---|---|
| `gateway` (Nginx) | Pintu masuk tunggal, static, rate limit, X-Request-ID | Anders |
| `auth` | Register/login, terbitkan JWT | Ardi |
| `poem` | CRUD puisi, publish event, cache baca | Satya |
| `nlp-worker` | Pipeline NLP → `poem.analyzed` | Satya |
| `wordcloud` | Frekuensi istilah dominan | Lindra |
| `search` | Indeks & pencarian | Anders |
| `kbbi-linker` | Kata Puitif → bentuk baku dan definisi KBBI | Dzaky |
| `tts` (opsional) | Puisi → audio | Anders — **ditunda** |

Komunikasi: sinkron (HTTP) untuk login/baca/cari; asinkron (**RabbitMQ**) untuk NLP, word cloud, KBBI, TTS.
Store: MySQL (database logis per layanan) + Redis (cache).

## Struktur

```
Final_Project_Scalable/
├── gateway/                 # Nginx: nginx.conf + templates/default.conf.template
├── services/                # satu folder per layanan (Dockerfile + app/)
│   ├── auth/                #   Ardi
│   ├── poem/                #   Satya
│   ├── nlp-worker/          #   Satya
│   ├── wordcloud/           #   Lindra
│   ├── search/              #   Anders
│   ├── kbbi-linker/         #   Dzaky
│   ├── tts/                 #   Anders (ditunda)
│   └── _template/           #   kerangka untuk layanan baru
├── frontend/                # HTML/CSS/JS (dari Tugas 1)
├── db/init.sql              # skema auth_db, poem_db, wordcloud_db, kbbi_db, search_db
├── shared/                  # events.py (kontrak event), metrics.py (histogram latensi)
├── observability/           # Prometheus, Grafana (dashboard), plugin RabbitMQ; lihat observability/README.md
├── loadtest/                # skrip k6 (search)
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

### Memperbarui dari versi lama (volume sudah ada)

`db/init.sql` hanya berjalan saat volume MySQL pertama kali dibuat. Setelah `git pull`:

1. Salin variabel baru dari `.env.example` ke `.env` (mis. blok `KBBI_*`; tanpa itu kbbi-linker tidak bisa login ke MySQL).
2. Bila volume dibuat sebelum ada `kbbi_db`, buat database + user-nya (atau `docker compose down -v` bila data boleh hilang):
   ```powershell
   "CREATE DATABASE IF NOT EXISTS kbbi_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE USER IF NOT EXISTS 'kbbi_user'@'%' IDENTIFIED BY 'ganti_kbbi_password'; GRANT ALL PRIVILEGES ON kbbi_db.* TO 'kbbi_user'@'%';" | docker compose exec -T mysql sh -c 'MYSQL_PWD=$MYSQL_ROOT_PASSWORD mysql -uroot'
   ```
3. Hapus antrean lama tanpa konsumen: `docker compose exec rabbitmq rabbitmqctl delete_queue tts.q`
4. `docker compose up -d --build --remove-orphans`
5. Puisi lama belum punya word cloud / KBBI: `Get-Content scripts/reanalyze_poems.py | docker compose exec -T poem python -`

### Data KBBI

Dataset KBBI tidak ada di repo (lisensi). Tanpa import, semua Kata Puitif tampil "tidak ditemukan".
Cara import ada di [`services/kbbi-linker/README.md`](services/kbbi-linker/README.md); setelahnya jalankan
`scripts/reanalyze_poems.py` (langkah 5 di atas) agar puisi lama ikut terhubung.

## Peta endpoint

Semua endpoint selain register/login butuh header `Authorization: Bearer <jwt>`.

| Endpoint | Layanan |
|---|---|
| `POST /api/auth/register`, `/login`, `/logout`, `GET /me` | auth |
| `GET/POST /api/poems` (puisi saya), `GET /api/poems/{id}` (semua user), `PUT/DELETE /api/poems/{id}` (pemilik) | poem |
| `GET /api/wordcloud/{poem_id}` | wordcloud |
| `GET /api/search?q=&kategori=&tag=&page=` | search |
| `GET /api/kbbi/{poem_id}` | kbbi-linker |
| `GET /api/tts/{poem_id}` | tts (ditunda) |

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
- [ ] TTS: AWS Polly / gTTS / tidak diimplementasikan.
- [ ] Ukuran instance EC2 & anggaran AWS.
- [ ] Fitur gambar puisi (Lambda) dipertahankan atau tidak.
