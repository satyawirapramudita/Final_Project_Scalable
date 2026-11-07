# Final_Project_Scalable

Platform Publikasi Karya Sastra Puisi — **microservice, event-driven, stateless**.
Transformasi dari monolit stateful [Monolithic-Stateful-AWS-Deployment](https://github.com/satyawirapramudita/Monolithic-Stateful-AWS-Deployment) (Tugas 1).

**Mata kuliah:** PACS262521 — Pengembangan Perangkat Lunak Scalable

> Status: **F0 (fondasi)** — struktur repo + Compose skeleton. Layanan masih kerangka
> (hanya `/health` dan `/metrics`); logika bisnis menyusul pada F1–F4.

## Arsitektur target

Tujuh layanan di balik satu gerbang Nginx:

| Layanan | Peran | Pemilik |
|---|---|---|
| `gateway` (Nginx) | Pintu masuk tunggal, static, rate limit, X-Request-ID | Lindra |
| `auth` | Register/login, terbitkan JWT | Ardi |
| `poem` | CRUD puisi, publish event, cache baca | Satya |
| `nlp-worker` | Pipeline NLP → `poem.analyzed` | Satya |
| `wordcloud` | Frekuensi istilah dominan | Lindra |
| `search` | Indeks & pencarian | Anders |
| `wiki-linker` | Kata Puitif → halaman Wikipedia | Dzaky |
| `tts` (opsional) | Puisi → audio | Anders — **ditunda** |

Komunikasi: sinkron (HTTP) untuk login/baca/cari; asinkron (**RabbitMQ**) untuk NLP, word cloud, wiki, TTS.
Store: MySQL (database logis per layanan) + Redis (cache).

## Struktur

```
Final_Project_Scalable/
├── gateway/                 # Nginx: nginx.conf + conf.d/default.conf
├── services/                # satu folder per layanan (Dockerfile + app/)
│   ├── auth/                #   Ardi
│   ├── poem/                #   Satya
│   ├── nlp-worker/          #   Satya
│   ├── wordcloud/           #   Lindra
│   ├── search/              #   Anders
│   ├── wiki-linker/         #   Dzaky
│   ├── tts/                 #   Anders (ditunda)
│   └── _template/           #   kerangka untuk layanan baru
├── frontend/                # HTML/CSS/JS (dari Tugas 1)
├── db/init.sql              # skema auth_db, poem_db, wordcloud_db, wiki_db
├── shared/events.py         # kontrak event bersama (dibekukan di F3)
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
- Cek kesehatan: `curl http://localhost:8080/api/poems` → 404 dari poem (belum diimplementasikan) itu normal untuk F0.

Menghentikan: `docker compose down` (data bertahan di volume; tambah `-v` untuk hapus).

## Peta endpoint (rencana)

| Endpoint | Layanan | Fase |
|---|---|---|
| `POST /api/auth/register`, `/login`, `/logout`, `GET /me` | auth | F1 |
| `GET/POST /api/poems`, `GET/PUT/DELETE /api/poems/{id}` | poem | F2 |
| `GET /api/wordcloud/{poem_id}` | wordcloud | F4 |
| `GET /api/search?q=` | search | F4 |
| `GET /api/wiki/{poem_id}` | wiki-linker | F4 |
| `GET /api/tts/{poem_id}` | tts | F4 (opsional) |

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
