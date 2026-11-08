# Rencana Pengembangan: Final Project Scalable (Platform Publikasi Karya Sastra Puisi)

**Mata Kuliah:** PACS262521 — Pengembangan Perangkat Lunak Scalable
**Basis:** Monolithic-Stateful-AWS-Deployment (Tugas 1)
**Acuan arsitektur target:** Dokumen Rancangan Arsitektur — 7 layanan (auth, poem, nlp-worker, wordcloud, search, wiki-linker, tts opsional)
**Tanggal:** 2026-11-07

---

## 1. Ringkasan

Transformasi aplikasi puisi dari **monolit stateful** (satu `server.py` + MySQL + sesi file JSON) menjadi **microservice event-driven yang stateless dan scalable**.

**Prinsip kerja:** *vertical slice dulu* — bangun satu jalur end-to-end tipis yang jalan, baru melebar. Jangan siapkan semua store di awal.

**Keputusan yang sudah ditetapkan:**
- Acuan target: **Dokumen Downloads (7 layanan)**, store MySQL + Redis + RabbitMQ + Nginx.
- **Satya = `poem` + NLP** → sekaligus **pemilik tunggal seluruh kontrak event**.
- **TTS ditunda/opsional** → Anders fokus ke `search` + gateway.
- **Gateway Nginx = Anders** (digabung dengan `search`).

---

## 2. Pembagian Tugas Final

| Anggota | Layanan | Fase | Estimasi | Beban |
|---|---|---|---|---|
| **Ardi** | `auth` | F1 | ±4–5 hari | Sedang |
| **Satya** | `poem` + `nlp-worker` (+ pemilik kontrak event) | F2 + F3 | ±10–11 hari | **Bottleneck** |
| **Dzaky** | `wiki-linker` | F4 | ±4–5 hari | Sedang |
| **Lindra** | `wordcloud` | F4 | ±3–4 hari | Ringan |
| **Anders** | `search` + `gateway` Nginx (TTS ditunda) | F4 (+F2) | ±6–7 hari | Sedang |

**Total:** ±27–32 orang-hari.

**Efek keputusan:** karena Satya memegang produsen semua event (`poem` publish created/updated/deleted, `nlp-worker` publish analyzed), **seluruh schema event punya satu pemilik** → gesekan integrasi antar-tim berkurang.

---

## 3. Kontrak Event (Pemilik: Satya — dibekukan di F3)

| Routing key | Producer | Payload inti | Konsumen (queue) |
|---|---|---|---|
| `poem.created` / `poem.updated` | **Satya (poem)** | `event_id, correlation_id, poem_id, user_id, judul, isi, kategori, occurred_at` | nlp.q (Satya), search.q (Anders) |
| `poem.deleted` | **Satya (poem)** | `event_id, poem_id, occurred_at` | search.q (Anders), wordcloud.q (Lindra), wiki.q (Dzaky) |
| `poem.analyzed` | **Satya (nlp-worker)** | `event_id, correlation_id, poem_id, tags[], kata_puitif[], terms[{term,count}], occurred_at` | poem.q (Satya), wordcloud.q (Lindra), wiki.q (Dzaky), search.q (Anders) |

`tts.q` **disiapkan tapi tidak diisi** dulu (TTS ditunda).

**Aturan bersama:**
- Amplop `event_id` + `correlation_id`.
- Manual acknowledgement + `prefetch_count` kecil.
- Retry backoff → dead-letter queue `*.dlq`.
- Idempotency via `event_id` / upsert `poem_id`.
- Perubahan skema bersifat additive (tambah boleh, hapus tidak).
- **Secret JWT** dari Ardi (dipakai semua layanan untuk verifikasi lokal).

---

## 4. Fase Migrasi (F0–F8)

| Fase | Fokus | Menutup keterbatasan | Estimasi |
|---|---|---|---|
| F0 | Fondasi repo & Compose skeleton | — | 1–2 |
| F1 | Auth stateless (JWT) — akar masalah | Retrograde scalability | 3–4 |
| F2 | Gateway Nginx + pecah poem service + DB terpisah | SPOF DB, tight coupling | 4–5 |
| F3 | RabbitMQ + nlp-worker (jalur event pertama) | Cascading failure | 4–5 |
| F4 | Fan-out: wordcloud, search, wiki-linker (tts opsional) | Scale all-or-nothing | 6–8 |
| F5 | Multi-tier caching (Redis) | (performa baca) | 2–3 |
| F6 | Observability & SLO | — | 3–4 |
| F7 | Ketahanan: retry, DLQ, idempotency, health | Cascading failure | 3–4 |
| F8 | Uji beban, tuning, deploy EC2 | (validasi) | 4–5 |

### F0 — Fondasi repo & Compose skeleton · ±1–2 hari
- Struktur: `gateway/`, `services/{auth,poem,nlp-worker,wordcloud,search,wiki-linker,tts}/`, `frontend/`, `db/`, `docs/`.
- Keputusan framework service HTTP (usul: FastAPI) vs stdlib.
- `docker-compose.yml` minimal: Nginx + 1 service placeholder + MySQL; jaringan `edge` & `data`.
- `.env.example`, `.gitignore`, `.dockerignore`.
- Salin aset reusable: `frontend/*`, `db/init.sql`, `lambda/`, `lambda-canvas-cdn/`.
- Nginx dasar: serve frontend, proxy `/api/`.
- **Selesai bila:** `docker compose up` bersih, 3 container sehat, tanpa dependensi sesi file.

### F1 — Auth stateless (JWT) ⭐ · ±3–4 hari
- Service `auth`: `POST /api/auth/register|login|logout`, `GET /api/auth/me`.
- PBKDF2 dipindah apa adanya (`server.py` L45-61); `auth_db` sendiri.
- JWT HS256 (PyJWT), secret via env.
- Hapus `load_session`/`require_session` → verifikasi JWT lokal.
- Frontend: simpan token, kirim `Authorization: Bearer`.
- `/health` auth.
- **Selesai bila:** `--force-recreate auth` → user tidak logout.

### F2 — Gateway Nginx + pecah poem service + DB terpisah · ±4–5 hari
- Nginx: route `/api/*`, serve frontend statis, rate limit, blokir akses langsung.
- Service `poem`: `GET/POST /api/poems`, `GET/PUT/DELETE /api/poems/{id}`.
- Pindahkan handler `submit_puisi`/`daftar_puisi` (L300-376).
- `poem_db` terpisah: tabel `puisi` + `tag`, `status` (`pending|done`), `versi`.
- Hapus FK ke `users` → referensi logis `user_id`.
- Fitur gambar (`gambar_file`/`gambar_url`, `ASSET_BASE_URL`) tetap.
- Frontend migrasi ke endpoint `/api/*`.
- **Selesai bila:** register → login → submit → daftar via gateway; auth mati ≠ poem mati.

### F3 — RabbitMQ + nlp-worker ⭐ · ±4–5 hari
- RabbitMQ + topic exchange `poem.events`; queue durable per konsumen.
- poem publish `poem.created/updated/deleted` setelah commit → respons 201 `pending`.
- `nlp-worker` stateless: tokenisasi, stopword, keyword, tag, Kata Puitif → publish `poem.analyzed`.
- poem konsumsi `poem.analyzed` → simpan tag, `status=done`.
- Amplop event + manual ack + prefetch kecil.
- **Selesai bila:** nlp-worker mati → submit tetap 201, pesan menunggu; hidup → terproses; tak ada puisi hilang.

### F4 — Fan-out: wordcloud, search, wiki-linker (tts opsional) · ±6–8 hari
- `wordcloud`: konsumsi analyzed → `wordcloud_db` + `GET /api/wordcloud/{id}`; konsumsi deleted.
- `search`: indeks sendiri; konsumsi created/updated/deleted/analyzed → `GET /api/search?q=`.
- `wiki-linker`: konsumsi analyzed → `wiki_db` + `GET /api/wiki/{id}`; cache Redis; konsumsi deleted.
- `tts` (opsional): konsumsi created/updated → S3 audio + `GET /api/tts/{id}`.
- Frontend: muat tiap panel terpisah.
- **Selesai bila:** satu puisi → tag + word cloud + tautan wiki tanpa saling memblokir.

### F5 — Multi-tier caching (Redis) · ±2–3 hari
- Tier 1: Nginx `proxy_cache` (GET publik + statis).
- Tier 2: Redis cache-aside di poem, search, wiki.
- Invalidasi saat `poem.updated`/`poem.deleted`.
- Ukur hit ratio sebelum/sesudah.

### F6 — Observability & SLO · ±3–4 hari
- `/metrics` tiap layanan (RED + cache hit/miss + job).
- Prometheus + Grafana.
- Log JSON ke stdout (`ts, level, service, request_id, event_id`).
- `correlation_id` dari HTTP → pesan RabbitMQ.
- RabbitMQ management (queue depth + DLQ) + alert dasar.

### F7 — Ketahanan · ±3–4 hari
- Retry backoff → DLQ `*.dlq`.
- Idempotency (dedupe `event_id` / upsert).
- Timeout panggilan keluar (Redis, DB, Wikipedia, TTS).
- Degradasi panel (gagal → "sedang diproses"/"tidak tersedia").
- Backpressure (prefetch kecil).
- `/health` semua layanan.

### F8 — Uji beban, tuning, deploy EC2 · ±4–5 hari
- k6/Locust: 5 skenario (baca+cari, submit, antrean, scale-out, ketahanan).
- Stub Wikipedia/TTS untuk uji skala besar.
- Setel TTL cache, prefetch, jumlah replika.
- Deploy EC2 (t2.micro → t3.small/medium), batas memori per container.
- `docker compose up --scale nlp-worker=3` sebagai bukti scale-out.
- Ambil panel Grafana sebagai bukti laporan.

---

## 5. Rincian per Anggota

### 🔵 Ardi — Servis Autentikasi · F1 · ±4–5 hari
**Tanggung jawab:** identitas & token; satu-satunya pemilik kredensial.

- Service `auth`: `POST /api/auth/register|login|logout`, `GET /api/auth/me`.
- PBKDF2 dipindah apa adanya; `auth_db` sendiri (migrasi tabel `users`).
- Terbitkan JWT HS256 (`sub`, `username`, `exp`); bagikan secret ke semua tim.
- Hapus sesi file JSON → verifikasi JWT lokal.
- Frontend: token + `Authorization: Bearer` (koordinasi pemilik poem/gateway).
- `/health`, `/metrics`, log JSON.
- **Kriteria:** `--force-recreate auth` → tidak logout; auth mati ≠ poem mati.
- **Menutup:** retrograde scalability.

### 🟠 Satya — `poem` + NLP · F2+F3 · ±10–11 hari ⭐ jalur kritis
**`poem` service (F2):**
- `GET/POST /api/poems`, `GET/PUT/DELETE /api/poems/{id}`; `poem_db` (tabel `puisi` + `tag`, `status`, `versi`).
- Verifikasi JWT lokal (tanpa panggil auth).
- Hapus FK ke `users` → referensi logis `user_id`.
- Fitur gambar (`gambar_file`/`gambar_url`, `ASSET_BASE_URL`) tetap.

**`nlp-worker` (F3):**
- Tokenisasi, stopword, keyword, tag, Kata Puitif → publish `poem.analyzed`.
- Keputusan terbuka: pustaka NLP Indonesia (mis. PySastrawi + kamus kata puitis).

**Tanggung jawab lintas-tim:**
- **Bekukan schema event lebih dulu** + sediakan **dummy event** agar rekan bisa paralel.
- Publish event setelah commit; konsumsi `poem.analyzed` → simpan tag, `status=done`.
- Idempotency, DLQ, `/health`, `/metrics`, log JSON.
- Penjaga infra Compose (RabbitMQ, Redis, MySQL).

- **Kriteria:** submit 201 `pending` instan → analyzed < 30 dtk; replay aman.
- **Risiko:** paling terbebani → **mitigasi:** kerjakan `poem` dulu (M1), rilis dummy event awal M2, baru nlp-worker.

### 🟢 Dzaky — Servis Wiki · F4 · ±4–5 hari
- Konsumsi `poem.analyzed` (`wiki.q`) → cocokkan `kata_puitif[]` ke Wikipedia; konsumsi `poem.deleted`.
- `wiki_db` + `GET /api/wiki/{poem_id}`.
- Cache lookup di Redis (TTL jam–hari).
- Timeout + retry + DLQ (Wikipedia rentan gagal); sediakan stub Wikipedia untuk uji beban.
- `/health`, `/metrics`, log JSON.
- **Kriteria:** puisi teranalisis → tautan wiki muncul; Wikipedia down → degradasi tanpa merusak panel lain.

### 🟣 Lindra — Servis Word Cloud · F4 · ±3–4 hari
- Konsumsi `poem.analyzed` (`wordcloud.q`) → `wordcloud_db`; konsumsi `poem.deleted`.
- `GET /api/wordcloud/{poem_id}`; (opsional) render PNG ke S3.
- `/health`, `/metrics`, log JSON.
- **Kriteria:** frekuensi konsisten dengan `terms`; hapus puisi → data terhapus.

### 🟡 Anders — Servis Search + Gateway · F4 (+F2) · ±6–7 hari
**`search`:**
- Konsumsi `poem.created/updated/deleted/analyzed` (`search.q`) → indeks sendiri.
- `GET /api/search?q=` (judul, isi, tag).
- Keputusan terbuka: MySQL FULLTEXT vs Meilisearch.
- Cache hasil populer di Redis (30–60 dtk).
- `/health`, `/metrics`, log JSON.
- Observability: pemilik Prometheus/Grafana.

**`gateway` Nginx (F2):**
- Route `/api/*` ke service, serve frontend statis, rate limit, blokir akses langsung service.
- `X-Request-ID` + rate limit (kerangka sudah ada di `gateway/`).

- **TTS ditunda** → `tts.q` disiapkan tapi kosong.
- **Kriteria:** pencarian menemukan judul/isi/tag; tag baru dari analyzed ikut terindeks; gateway meneruskan seluruh `/api/*` dengan benar.

---

## 6. Komponen Bersama

| Komponen | Pemilik |
|---|---|
| Infra Compose (RabbitMQ, Redis, MySQL) | **Satya** (penjaga) + kontribusi semua |
| Skema & dummy event | **Satya** |
| JWT secret bersama | **Ardi** |
| Gateway Nginx | **Anders** |
| Observability Prometheus/Grafana | **Anders** (tiap orang instrumentasi `/metrics` sendiri) |
| Uji beban + stub | **Dibagi**: stub Wikipedia (Dzaky), skenario (semua) |

---

## 7. Jadwal 4 Minggu

| Minggu | Fokus | Ardi | Satya | Dzaky | Lindra | Anders |
|---|---|---|---|---|---|---|
| **M1** | F0 + F1 + F2 | Auth | **Poem** + infra | Baca kontrak, siapkan stub | Skema wordcloud | **Gateway** + skema indeks |
| **M2** | F3 | Hardening auth | **nlp-worker** + **bekukan event** + dummy | Stub konsumen | Word cloud mulai | Search mulai (pakai dummy) |
| **M3** | F4 | Bantu auth/caching | Poem+NLp hardening | **Wiki** | **Word cloud** selesai | **Search** selesai |
| **M4** | F5–F8 | Cache/invalidate | Cache poem | DLQ/cache wiki | Grafana/infra | Uji beban/deploy |

**Jalur kritis:** F0 → F1 (Ardi) → F2 poem (Satya) → F3 + event dibekukan (Satya) → F4 paralel.

**Titik demo bertahap:**
- Selesai F1 → demo "stateless, restart tidak logout".
- Selesai F3 → demo "submit instan, analisis di belakang layar" (kontras kuat dengan Tugas 1).
- Selesai F4 → demo "satu event, banyak layanan".
- Selesai F8 → demo skala + SLO.

---

## 8. Aturan Koordinasi

1. **Satya bekukan kontrak event di F3** sebelum F4 mulai — satu-satunya pemilik schema.
2. **Secret JWT** dari Ardi lewat `.env` bersama.
3. **Satu Compose file**, perubahan infra via PR.
4. Tiap layanan: `Dockerfile` + `/health` + `/metrics` sendiri (seragam).
5. **Branch fitur berumur pendek** per layanan/tugas (lihat §9), merge ke `main` setelah `docker compose up` hijau.
6. **Dummy event dulu** — Lindra/Dzaky/Anders tak menunggu nlp-worker matang.

---

## 9. Repositori & Strategi Branch

### 9.1 Repositori

| Peran | URL |
|---|---|
| Baseline Tugas 1 (monolit stateful) | `https://github.com/satyawirapramudita/Monolithic-Stateful-AWS-Deployment` |
| Implementasi target (Tugas 2) | `https://github.com/satyawirapramudita/Final_Project_Scalable` |

- Repo target memakai branch `main` sebagai sumber kebenaran.
- `main` harus **selalu bisa** dijalankan dengan `docker compose up -d`.
- `.env` tidak pernah di-commit; hanya `.env.example`.
- Container Monolithic lama tetap berjalan di port 8000; gateway baru di port 8080.

### 9.2 Model Branch

Model: **trunk-based / GitHub Flow** — satu branch jangka panjang + branch fitur berumur pendek.

| Jenis | Umur | Jumlah | Contoh |
|---|---|---|---|
| `main` | permanen | **1** | `main` |
| `feat/*` | hari–minggu | 1 per pekerjaan (maks ±5) | `feat/auth`, `feat/poem-nlp` |
| `fix/*`, `chore/*`, `docs/*` | jam–hari | sesuai kebutuhan | `fix/jwt-expiry`, `chore/infra` |
| `integration/*` | sementara | 1 bila perlu | `integration/f3` |
| tag rilis | penanda | — | `v0.1.0-demo` |

**Total branch aktif yang sehat: ±1–6.** Lebih dari itu = ada branch lupa di-merge/dihapus.

### 9.3 Pemetaan Branch ke Pemilik

| Branch | Pemilik | Cakupan |
|---|---|---|
| `feat/auth` | Ardi | `services/auth/` |
| `feat/poem-nlp` | Satya | `services/poem/`, `services/nlp-worker/`, `shared/events.py` |
| `feat/wiki` | Dzaky | `services/wiki-linker/` |
| `feat/wordcloud` | Lindra | `services/wordcloud/` |
| `feat/search-gateway` | Anders | `services/search/`, `services/tts/`, `gateway/` |

### 9.4 Aturan Branch

1. Selalu cabang dari `main` terbaru.
2. Satu branch = satu pekerjaan/fitur.
3. PR ke `main`, minimal 1 reviewer.
4. `main` dilindungi: wajib PR + CI hijau.
5. Hapus branch setelah merge.
6. **Jangan** membuat branch permanen per layanan.
7. Perubahan `docker-compose.yml`, `shared/`, `db/init.sql` = PR lintas tim (pemilik: Satya).

### 9.5 CODEOWNERS

```text
# .github/CODEOWNERS
# Ganti dengan username GitHub masing-masing.
/services/auth/        @<username-ardi>
/services/poem/        @<username-satya>
/services/nlp-worker/  @<username-satya>
/services/wiki-linker/ @<username-dzaky>
/services/wordcloud/   @<username-lindra>
/gateway/              @<username-anders>
/services/search/      @<username-anders>
/services/tts/         @<username-anders>
/shared/               @<username-satya>
/docker-compose.yml    @<username-satya>
/db/                   @<username-satya>
```

### 9.6 Alur Kerja

```bash
git switch main
git pull
git switch -c feat/auth
# ...kerja...
git add .
git commit -m "feat(auth): add register and login with JWT"
git push -u origin feat/auth
# buka Pull Request ke main
```

Konvensi commit: `feat(...)`, `fix(...)`, `chore(...)`, `docs(...)`.

### 9.7 Status Saat Ini

- Repo `Final_Project_Scalable` baru memiliki **satu branch: `main`**.
- Scaffold F0 sudah dibuat dan dijalankan (11 container sehat).
- Branch fitur dibuat saat masing-masing anggota mulai F1–F4.

---

## 10. Keputusan Terbuka

- [ ] Mesin pencarian: MySQL FULLTEXT atau Meilisearch.
- [ ] TTS: AWS Polly, gTTS, atau tidak diimplementasikan (ditunda).
- [ ] Pustaka NLP bahasa Indonesia untuk nlp-worker.
- [ ] Framework service HTTP: FastAPI atau stdlib `http.server`.
- [ ] Ukuran instance EC2 dan batas anggaran AWS.
- [ ] Apakah fitur gambar puisi (Lambda) dipertahankan pada versi baru.

---

## 11. Risiko

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Satya = bottleneck (poem + NLP + kontrak) | Menghambat F3/F4 | Kerjakan poem dulu, rilis dummy event awal, delegasi infra ke rekan |
| Anders = search + gateway (+ TTS opsional) | Beban menumpuk di M1/M4 | Gateway sudah jadi di F0 (skeleton), TTS ditunda; kerjakan gateway lebih dulu |
| Memori EC2 terbatas (±10 container) | OOM saat demo | Batas memori per container; instance t3.small/medium |
| MySQL bersama = SPOF fisik (prototipe) | Downtime | Dicatat sebagai kompromi; produksi pisah instance |
| API Wikipedia/TTS rate limit | Analisis gagal | Cache, retry, stub saat uji beban |
| Kualitas NLP Indonesia | Tag kurang akurat | Evaluasi manual sampel |
| Konsistensi eventual | Counter/hasil tertinggal sesaat | TTL pendek, event idempoten, rekonsiliasi |
