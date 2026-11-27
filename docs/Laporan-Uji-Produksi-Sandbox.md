# Laporan Uji Produksi — AWS Academy Sandbox (5 Fitur Utama)

| | |
|---|---|
| **Tanggal uji** | 28 November 2026, 01:30–01:50 WIB (27 Nov 18:30–18:50 UTC) |
| **Objek uji** | Deployment produksi penuh di AWS Academy Sandbox melalui `deploy/sandbox/deploy.sh` |
| **Referensi tugas** | *Hands On Reliability dan Failover* — bukti empiris untuk Tabel Analisis Risiko |
| **Laporan pendamping** | Audit statis: [`Laporan-Reliability-Failover.md`](Laporan-Reliability-Failover.md) (24 Nov); SLO: [`slo.md`](slo.md) |
| **Sifat data** | Seluruh angka di dokumen ini **aktual** — hasil eksekusi nyata, bukan estimasi |
| **Status** | ✅ 5/5 fitur lulus dengan bukti; 3 temuan operasional baru |

---

## 1. Ringkasan eksekutif

Deployment produksi (3 server `t3.small` + S3 + CloudFront) diselesaikan dalam **±11 menit**
dari satu skrip, lulus smoke test, dan kelima fitur ketahanan utama diuji dengan injeksi
kegagalan nyata. Seluruh pengujian menghasilkan **nol kehilangan data** (outbox 0, DLQ 0,
idempotency 100% konsisten).

| # | Fitur | Skenario injeksi | Hasil | Verdict |
|---|---|---|---|---|
| 1 | Failover gateway | `docker stop puisi-gateway` di app-1 | **Zero downtime** — semua probe 200 selama gateway `Exited`; CloudFront pindah ke app-2 | ✅ LULUS |
| 2 | Cascading failure (poem) | `docker compose stop poem` di app-1 | Langsung ke app-1: wordcloud **503 ±88 s**, search tetap **200**; via CloudFront: **200 terus** (failover menutupi) | ✅ LULUS (2 sudut) |
| 3 | Quorum RabbitMQ | `docker kill puisi-rabbit-1` | Klaster hidup (2/3); 3 puisi dikirim saat node mati → semua **`done`**; DLQ semua 0 | ✅ LULUS |
| 4 | Data layer + circuit breaker | `docker pause puisi-mysql` | Breaker search terbuka → **503 cepat ±1,3 dtk** (bukan menggantung); pulih **±4 dtk** setelah unpause; poem (tanpa breaker) menggantung 20 dtk | ✅ LULUS |
| 5 | Idempotency key | k6 `submit-duplikat` + `key-bentrok` via CloudFront | 720 POST → **240 baris unik** (nol duplikasi), **480 replay**, `id_puisi` konsisten 100%, **107× 409** | ✅ LULUS |

**Temuan baru (belum ada di audit 24 Nov):**

| No | Temuan | Dampak | Tindak lanjut |
|---|---|---|---|
| T1 | CloudFront (mode split) **menutupi** cascading failure poem bagi pengguna akhir | Sisi positif: mitigasi bekerja; sisi risiko: masalah tersembunyi dari pengguna — hanya terlihat lewat monitoring | Monitoring/alert poem per replika |
| T2 | `docker compose start <svc>` **gagal di server aplikasi** ("missing dependency rabbit-2") | Recovery manual tersandung; RTO membesar bila operator tidak tahu | Restart harus memakai `-f deploy/sandbox/app.yml up -d --no-deps`; perbaiki tooling `jalankan`/README |
| T3 | `/api/poems` **menggantung 20 dtk** saat MySQL pause (poem/auth tidak punya breaker) | Pool & worker terbebani saat DB sakit | Breaker generik (temuan R7 audit) — kini ada bukti produksi |

---

## 2. Lingkungan & deployment

### 2.1 Topologi (aktual)

```
Browser ─HTTPS─▶ CloudFront d2lvm0i8ghkvs9.cloudfront.net (mode split)
                 ├── /*            ─▶ S3 (frontend statis)
                 └── /api/search*, /api/wordcloud/*, /api/kbbi/*
                        ─▶ origin group: app-1 → app-2 (failover 500/502/503/504)
                 └── POST/PUT/DELETE  ─▶ app-1 (mode split; origin group ditolak AWS
                                          untuk method non-cacheable — InvalidArgument)
app-1  i-07ca19fd99c54f423  publik 44.222.210.73  privat 172.31.30.103
       gateway-a + 1 replika tiap layanan + Prometheus + Grafana
app-2  i-088917fa40c5baca1  publik 52.70.63.150  privat 172.31.38.120
       gateway-b + 1 replika tiap layanan
data   i-03535665700e0215d  publik 98.93.46.131  privat 172.31.31.212
       MySQL + Redis + RabbitMQ 3 node (5672/5673/5674, quorum queue)
```

- Region `us-east-1`; kredensial sesi dari AWS Details (Learner Lab); AWS CLI v2.42.4
  dijalankan dari laptop (jalur "Alternatif: serahkan kredensial lab ke asisten AI" pada
  `deploy/sandbox/README.md`, jalur yang tervalidasi 27 Nov).
- CloudFront origin group untuk POST ditolak AWS (`InvalidArgument`) → skrip otomatis
  fallback ke **mode split** (terdokumentasi, perilaku normal).

### 2.2 Hasil deployment & smoke test (salinan mentah)

Durasi `bash deploy/sandbox/deploy.sh buat`: **±11 menit** (terukur dari log eksekusi).

```
==== Smoke test ====
app-1 /health                    200
app-2 /health                    200
CloudFront / (S3)                200
CloudFront /api/search           401
CloudFront POST /api/auth        401
CloudFront : https://d2lvm0i8ghkvs9.cloudfront.net/  (mode split)
app-1  i-07ca19fd99c54f423  publik 44.222.210.73  privat 172.31.30.103
app-2  i-088917fa40c5baca1  publik 52.70.63.150  privat 172.31.38.120
data   i-03535665700e0215d  publik 98.93.46.131  privat 172.31.31.212
```

Seed data (jalur submit asli, idempotency aktif):

```
terkirim 200/1000 (4 detik) … terkirim 1000/1000 (16 detik)
SELESAI: 1000 puisi dianalisis, antrean kosong (50 detik)
```

Import KBBI (`kbbi-v-27b7dc6-ringkas`, ±71.737 entri) berjalan **di latar belakang**
selama pengujian; verifikasi final via `deploy.sh kbbi-status`.

Kesehatan akhir: **22/22 kontainer healthy** (app-1: 9, app-2: 8, data: 5).

### 2.3 Baseline (sebelum injeksi)

| Request | Status | Latensi |
|---|---|---|
| `GET https://…cloudfront.net/api/search?q=senja` | 200 | 1,386 s |
| `GET http://44.222.210.73/api/search?q=senja` (langsung app-1) | 200 | 0,622 s |
| `GET http://44.222.210.73/api/wordcloud/1` | 200 | 0,603 s |

> Latensi didominasi **RTT internet Indonesia → us-east-1 (±250–300 ms)**. Angka mutlak
> dari laptop TIDAK sebanding dengan SLO/uji lokal (`docs/slo.md`); yang dibandingkan
> antar-probe adalah **perubahan perilaku** (status code, tren latensi), bukan nilai mutlak.

---

## 3. Uji 1 — Failover gateway (CloudFront origin group)

**Injeksi:** `deploy.sh jalankan app-1 'docker stop puisi-gateway'` pada 01:34:28 WIB,
sambil probe `GET /api/search?q=<unik>` tiap ±2 detik via CloudFront.

**Bukti mentah (ringkas):**

```
01:34:20 gw=200      ← sebelum injeksi
01:34:28 gw=200      ← detik injeksi: TETAP 200
01:34:30 gw=200 … 01:35:47 gw=200
(RINGKAS: 200=46  5xx=0  koneksi000=0  — probe dihentikan karena tidak pernah ada 5xx)
```

**Bukti kontainer saat probe terus 200:**

```
puisi-gateway   Exited (0)   About a minute ago      ← app-1
```

Setelah `docker start puisi-gateway`: `Up … (healthy)`.

**Analisis:** Selama ±70 detik gateway-a benar-benar mati (Exited 0, terverifikasi
SSM), **nol request gagal** — CloudFront origin group melayani seluruh trafik dari
app-2 tanpa jendela 502/503. **RTO terukur: 0 detik** (dari sudut pengguna). Ini
menutup temuan R2/R3 audit (gateway tunggal tanpa failover origin).

**Batasan:** origin group hanya mencakup jalur baca (mode split); POST/PUT/DELETE
tetap ke app-1. Keandalan tulis bergantung retry frontend + Idempotency-Key.

---

## 4. Uji 2 — Cascading failure layanan poem (2 sudut)

**Injeksi:** `deploy.sh jalankan app-1 'cd /opt/poetify && docker compose stop poem'`
pada 01:36:49 WIB, sambil probe ganda tiap ±5 detik: wordcloud **via CloudFront**,
wordcloud **langsung app-1**, dan search **langsung app-1**.

**Bukti mentah:**

```
01:36:39 CF_wordcloud=200 app1_wordcloud=200 app1_search=200
01:36:44 CF_wordcloud=200 app1_wordcloud=200 app1_search=200
01:36:48 CF_wordcloud=200 app1_wordcloud=200 app1_search=200   ← detik injeksi
01:36:52 CF_wordcloud=200 app1_wordcloud=503 app1_search=200   ← CASCADE MULAI
01:36:57 CF_wordcloud=200 app1_wordcloud=503 app1_search=200
01:37:02 … 01:37:30  (pola sama terus: CF=200, app1=503, search=200)
01:37:34 START poem → GAGAL: "poem is missing dependency rabbit-2" (temuan T2)
01:37:39–01:38:17  app1_wordcloud masih 503 (karena start gagal)
01:38:20  start ulang dengan -f deploy/sandbox/app.yml --no-deps → poem healthy
          → app1_wordcloud pulih ke 200
```

**Analisis:**

| Sudut | Hasil | Makna |
|---|---|---|
| Langsung ke app-1 | wordcloud/kbbi **503**, search tetap **200** sepanjang poem mati | Bukti titik kritis: poem = hard dependency wordcloud/kbbi via `auth_request` gateway; search independen (verifikasi JWT lokal + cache) |
| Via CloudFront | **200 terus, nol kegagalan** | Bukti mitigasi: gateway-a menjawab 503 → origin group memindahkan ke app-2 yang poem-nya sehat |

Downtime langsung terukur: **01:36:52 → ±01:38:20 (±88 s)**, tetapi **±45 s di antaranya
adalah kesalahan operator** (perintah start yang salah). RTO riil setelah perintah start
yang benar: **±15–20 dtk** (uvicorn healthy + probe 200 pertama). Pemulihan
`docker compose stop` → gateway menutup subrequest `auth_request` dalam < 1 detik
(503 JSON, bukan menggantung — konsisten dengan desain `error_page @err_access`).

**Tindak lanjut T2:** seluruh `start/stop` layanan aplikasi di sandbox harus memakai
`docker compose -f docker-compose.yml -f deploy/sandbox/app.yml … --no-deps`. Perlu
diperbaiki di README/tooling.

---

## 5. Uji 3 — Klaster RabbitMQ: satu node mati (quorum queue)

**Injeksi:** `deploy.sh jalankan data 'docker kill puisi-rabbit-1'` pada 01:39:52 WIB
(kill = crash simulasi, bukan stop rapi).

**Bukti mentah — kondisi klaster:**

```
sebelum : puisi-rabbit-1 Up 15 minutes (healthy) | -2/-3 Up (healthy)
          queue: poem.q.dlq 0, nlp.q 0, kbbi.q 0
sesudah kill:
          puisi-rabbit-1 Exited (137) 14 seconds ago
          puisi-rabbit-2 Up 15 minutes (healthy)
          puisi-rabbit-3 Up 15 minutes (healthy)
```

**Submit 3 puisi VIA CLOUDFRONT saat node mati:**

```
POST 1 -> 201  1.486s
POST 2 -> 201  1.112s
POST 3 -> 201  1.068s
```

**30 detik kemudian (verifikasi DB, node masih mati):**

```
Uji Quorum 1   done
Uji Quorum 2   done
Uji Quorum 3   done
DLQ: poem.q.dlq 0 | kbbi.q.dlq 0 | nlp.q.dlq 0 | wordcloud.q.dlq 0 | search.q.dlq 0
```

**Restore:** `docker start puisi-rabbit-1` → `Up 24 seconds (healthy)`, klaster kembali 3/3.

**Analisis:** Antrean quorum (replikasi mayoritas 2/3) menyerap crash satu node tanpa
jendela gagal yang teramati: publish → outbox relay → nlp-worker → `poem.analyzed` →
tag tersimpan, semuanya sukses saat node mati. Nol pesan di DLQ. Ini bukti live untuk
temuan R6 audit — klaster (baru di-merge dari `feat/sandbox-deploy`) bekerja.

---

## 6. Uji 4 — Data layer: MySQL di-pause, circuit breaker search

**Injeksi:** `deploy.sh jalankan data 'docker pause puisi-mysql'` pada 01:41:56 WIB
(pause = proses hidup tapi tidak merespons — kasus terburuk tanpa RST TCP).

**Bukti mentah — search via CloudFront (breaker search):**

```
probe 1 : 000  15.001s   ← menggantung sampai timeout klien (breaker belum terbuka)
probe 2 : 503   6.526s
probe 3 : 503   1.227s   ← breaker terbuka: gagal cepat
probe 4 : 503   1.404s
probe 5 : 503   1.422s
probe 6 : 503   1.422s
probe 7 : 503   1.406s
probe 8 : 503   1.261s
```

**Bukti mentah — `/api/poems` langsung app-1 (tanpa breaker):**

```
probe 1 : 000  20.002s   ← menggantung penuh sampai timeout
probe 2 : 000  20.003s
```

**Pulih:** `docker unpause puisi-mysql` 01:43:19 →

```
t=4s  CF search -> 200       ← RTO ±4 detik
```

**Analisis:**

1. Breaker search bekerja persis desain 3 status-nya: kegagalan pertama membuka
   (request 1–2 masih sempat menggantung), selanjutnya **gagal cepat ±1,3 dtk**
   (RTT WAN + penolakan instan) alih-alih menggantung 15–20 dtk.
2. **Kontras poem:** tanpa breaker, semua request menggantung sampai timeout klien —
   risiko saturasi pool/worker yang dikhawatirkan audit (R7) kini terbukti live.
3. MySQL tetap **titik kegagalan tunggal**: pause = seluruh endpoint data down.
   RTO pulih normal **±4 dtk**, tetapi ini hanya berlaku untuk gangguan sementara;
   kehilangan server data permanen tetap butuh replika/restore (R4, belum diuji).

---

## 7. Uji 5 — Idempotency key di beban (via CloudFront)

**Beban:** k6 `loadtest/submit.js` dari laptop, `BASE_URL=https://d2lvm0i8ghkvs9.cloudfront.net`.

### 7.1 Skenario `submit-duplikat` (VUS=5, 60 dtk — key sama dikirim 3× per iterasi)

```
checks_total.......: 1440     23.54/s
checks_succeeded...: 100.00%  1440 out of 1440
✓ status 201
✓ id_puisi konsisten
idempotent_replays.............: 480   7.85/s
http_reqs............: 720     11.77/s
http_req_failed......: 0.00%   0 out of 720
http_req_duration....: avg=415.09ms  med=451.99ms  p(95)=533.77ms  max=858.57ms
```

### 7.2 Skenario `key-bentrok` (VUS=3, 30 dtk — key sama, payload beda)

```
checks_succeeded...: 100.00%  107 out of 107
✓ status 409
idempotent_conflicts...........: 107    3.46/s
(http_req_failed 50% = 2×107: POST ke-2 memang dijawab 409 — perilaku yang divalidasi check)
```

### 7.3 Verifikasi basis data (kebenaran, bukan hanya respons)

```
SELECT COUNT(*) FROM poem_db.puisi WHERE judul LIKE 'Uji Duplikat%';   → 240
SELECT COUNT(*) FROM poem_db.outbox WHERE published_at IS NULL;        → 0
SELECT COUNT(*) FROM poem_db.puisi WHERE judul LIKE 'Uji Quorum%' AND status='done'; → 3
```

**Analisis:**

- 720 POST = 240 iterasi × 3 → DB berisi **tepat 240 baris** = **nol duplikasi**,
  480 replay (2 per iterasi) dengan `Idempotent-Replay: true` dan `id_puisi` identik.
- 107 key berbeda-payload semuanya ditolak **409 Conflict**.
- Idempotency terbukti **di beban, lintas CloudFront, dengan Redis bersama di server data** —
  bukan hanya unit test.

> Catatan latensi: p95 534 ms melewati ambang 500 ms **karena RTT WAN**, bukan beban
> server (lihat §2.3). Angka SLO yang sah tetap miliki uji lokal se-region.

---

## 8. Baseline beban (soak) — untuk pembanding demo

k6 `aws_stress.js` profil `soak`, 20 VU × 60 dtk, via CloudFront:

```
checks_succeeded...: 100.00%  3330 out of 3330
http_reqs............: 3330    55.15/s
http_req_failed......: 0.00%
gateway_cache_hit....: 71.89%  2394 out of 3330
http_req_duration....: avg=355ms  med=307ms  p(95)=474ms  p(99)=566ms  max=1.07s
```

Nol error; hit-rate cache gateway 72% (campuran query populer + 1 token). Bandingkan
kapasitas sebenarnya dengan uji lokal `docs/slo.md` (±2.1k r/s @2 replika, p95 36 ms).

---

## 9. Pemetaan bukti → Tabel Analisis Risiko

| Titik kritis (audit) | Bukti produksi (bagian) | Dampak terukur | Mitigasi yang terpasang & terbukti |
|---|---|---|---|
| Gateway SPOF (R2/R3) | §3 | 0 request gagal saat gateway `Exited` | 2 gateway di mesin berbeda + CloudFront origin group |
| Sinkron: poem → wordcloud/kbbi (R7/R8) | §4 | 503 < 1 dtk (bukan menggantung), pulih ±15–20 dtk | Replika poem (2 server), timeout 3 dtk, error JSON, cache stale |
| Async & broker (R6) | §5 | 0 pesan hilang, DLQ 0, analisis tetap jalan saat node mati | Quorum queue 3 node + outbox + DLQ + retry jitter |
| Data: MySQL (R4) | §6 | seluruh endpoint data down saat pause; breaker search 503 cepat; RTO ±4 dtk | Breaker search (closed/open/half-open); gap: poem/auth tanpa breaker, MySQL tanpa replika |
| Konsistensi tulis (idempotency) | §7 | 240/240 unik, 480 replay, 107× 409 | Idempotency key + Redis TTL 24 jam, scope per user |
| Observability | (rehearsal lokal 24 Nov) | `/health` 200 saat dependency mati | Alert Prometheus (DLQ, outbox, breaker, 5xx) — perlu screenshot Grafana |

---

## 10. Batasan pengukuran

1. **Klien & origin berjauhan geografis** — latensi absolut didominasi RTT WAN;
   perbandingan hanya antar-probe dalam satu uji.
2. **Satu uji per injeksi, bukan berulang** — angka RTO tunggal (contoh: RTO MySQL ±4 dtk
   dari satu sampel), bukan statistik.
3. **Mode split CloudFront** — POST tidak melalui origin group; failover tulis belum
   teruji di level origin.
4. **MySQL pause ≠ kehilangan server data** — uji gangguan sementara; kegagalan
   permanen (RPO/restore) belum diuji di sandbox.
5. **Sesi sandbox bersifat sementara** — semua resource terhapus saat timer 0:00;
   seluruh uji reproducible dengan `deploy.sh buat` (±11 menit) lalu urutan §3–§7.
6. **KBBI ringkas** — import berjalan latar belakang saat uji; bukan penghalang hasil.

---

## 11. Reproduksi (ringkas)

```bash
# Deploy (CloudShell atau laptop berkredensial lab, region us-east-1)
bash deploy/sandbox/deploy.sh buat          # ±11 menit, smoke 200/401
bash deploy/sandbox/deploy.sh info          # URL CF + IP (berubah tiap sesi)

# Token user 99
bash deploy/sandbox/deploy.sh jalankan app-1 \
  'cd /opt/poetify && docker compose exec -T poem python - 99 < scripts/mint_token.py'

# Uji 1: stop/start gateway-a (+ probe CF /api/search)
# Uji 2: stop/start poem via app.yml (--no-deps) (+ probe ganda CF & IP app-1)
# Uji 3: kill/start puisi-rabbit-1 (+ POST 3 puisi + cek status & DLQ)
# Uji 4: pause/unpause puisi-mysql (+ probe search & poems)
# Uji 5: k6 submit-duplikat & key-bentrok dari laptop (+ COUNT(*) DB)
```

Perintah lengkap per terminal: [`Demo-Kelas-AWS.md`](Demo-Kelas-AWS.md) dan
[`Stress-Test-AWS.md`](Stress-Test-AWS.md).

---

*Laporan ini melengkapi audit statis 24 Nov dengan bukti eksekusi produksi. Seluruh
angka berasal dari eksekusi 28 Nov 2026 01:30–01:50 WIB pada sesi sandbox yang aktif.*
