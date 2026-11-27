# Laporan Audit Reliability & Failover — Platform Puisi Scalable (Deployment AWS)

| | |
|---|---|
| **Tanggal** | 24 November 2026 |
| **Objek audit** | Arsitektur deployment pada `docs/deploy-aws-tutorial.md`, diverifikasi terhadap konfigurasi aktual repo |
| **Referensi tugas** | *Hands On Reliability dan Failover* — Tahap 1 (audit SPOF) & Tahap 2 (rancangan pola perbaikan) |
| **Cakupan** | Audit statis berbasis kode/konfigurasi + rancangan solusi. Implementasi belum dilakukan |
| **Sumber bukti** | `docker-compose.yml`, `docker-compose.prod.yml`, `gateway/`, `shared/`, `services/`, `deploy/`, `observability/`, `db/init.sql`, `docs/slo.md`, `.env.example` |
| **Target keandalan acuan** | SLO 99% per 30 hari; error budget 432 menit/bulan (`docs/slo.md`) |

---

## 1. Ringkasan eksekutif

Audit menemukan bahwa **pola ketahanan tingkat aplikasi sudah kuat** (transactional outbox, DLQ + retry berjitter, idempotency key, circuit breaker search→MySQL dengan fallback *stale*, breaker Redis fail-open, 2 replika + autoheal, mikro-cache gateway `use_stale`). Kelemahan utama justru berada di **lapisan infrastruktur dan operasi**: satu instance EC2 untuk seluruh 20 kontainer, satu gateway, satu MySQL tanpa replika, satu RabbitMQ, tanpa alerting, tanpa resource limit, serta backup yang belum aman dan belum pernah diuji pulih.

| No | Temuan | Kategori | Dampak availability | Prioritas |
|---|---|---|---|---|
| R1 | EC2 tunggal (1 AZ, 1 EBS, 1 EIP) untuk semua beban | Host | Total outage saat host/AZ/EBS gagal | P0 (mitigasi) / P2 (multi-AZ) |
| R2 | Gateway 1 kontainer tanpa limit memori | Gateway | Total outage API saat OOM/crash | P0 |
| R3 | CloudFront hanya 1 origin `/api/*` | Gateway | Tidak ada failover origin | P0 |
| R4 | MySQL 1 node, tanpa replika/failover | Data | Total untuk semua endpoint data | P1 / P2 |
| R5 | Backup RPO 24 jam, di bucket publik, tanpa uji restore | Data | Kehilangan data s/d 24 jam; potensi kebocoran dump | P0 |
| R6 | RabbitMQ 1 node; DLQ tanpa alert/replay | Async | Pesan tertunda (aman berkat outbox); queue/DLQ hilang bila volume rusak | P0 / P2 |
| R7 | MySQL lambat → poem/auth tanpa breaker/stale | Sinkron | Degradasi sebagian + risiko saturasi pool | P1 |
| R8 | Replika tanpa health check aktif di LB | Sinkron | Jendela 502 hingga ±60 dtk per replika mati | P1 / P2 |
| R9 | Tanpa alerting (Prometheus tanpa rules, Grafana satu host) | Operasi | Deteksi kegagalan lambat | P0 |
| R10 | Rate limit per-IP di belakang CloudFront | Gateway | 429 massal satu kanal edge | P0 / P1 |
| R11 | Lambda Function URL `AuthType=NONE` | Biaya/security | Abuse → biaya membengkak | P1 |
| R12 | Redis tanpa `maxmemory` | Host | Risiko OOM host | P0 |
| R13 | Deploy `up --build` di host produksi | Operasi | Downtime rilis, tanpa rollback otomatis | P2 |
| R14 | Dokumen: "19 container" (aktual 20) | Dokumen | Kebingungan verifikasi | P2 |

**Kesimpulan singkat.** Aplikasi dirancang untuk *survive* kegagalan broker dan MySQL-slow (pola sudah terbukti di `docs/Laporan-Uji-Ketahanan.md`), tetapi **belum dirancang untuk survive kegagalan host, gateway tunggal, atau kehilangan MySQL secara permanen**. Urutan perbaikan yang disarankan: kontainmen resource (R2/R12) → redundansi gateway + failover origin (R3) → alerting (R9) → backup privat + snapshot + drill (R5) → tooling DLQ (R6) → replika/failover data (R4) → breaker generik (R7).

---

## 2. Metode & ruang lingkup

1. **Audit deskriptif terhadap tutorial** `docs/deploy-aws-tutorial.md`: topologi (Browser → CloudFront → S3/EC2/Lambda), §3 security group, §4–5 EC2/EIP, §9 S3, §10 CloudFront, §11 Lambda, §13 operasional.
2. **Verifikasi klaim terhadap konfigurasi nyata**: setiap pernyataan dampak dikaitkan ke lokasi kode/konfigurasi (`path:baris`) — mis. `gateway/templates/default.conf.template:13`, `shared/rabbit.py:159-209`, `docker-compose.yml:30-77`.
3. **Analisis skenario** sesuai 4 kluster wajib tugas: (a) gateway/OOM, (b) jalur sinkron, (c) asinkron/broker, (d) layer data — ditambah temuan lintas kluster (host, observability, biaya).
4. **Perancangan solusi** memetakan pola tugas (circuit breaker 3 status, threshold + fallback, retry DB, ≥2 replika + LB + health check, read replica/failover, idempotency konsumen, DLQ) ke setiap temuan.

**Batasan.** Audit ini statis: belum ada injeksi kegagalan yang dijalankan, sehingga angka durasi bersifat estimasi (ditandai "±"). Estimasi biaya bersifat indikatif. Implementasi perbaikan adalah pekerjaan Tahap 2 terpisah.

---

## 3. Kondisi ketahanan saat ini

### 3.1 Pola yang sudah terbukti ada

| Pola | Implementasi & bukti | Catatan |
|---|---|---|
| Transactional outbox | `services/poem/app/outbox.py` — tulis event dalam transaksi yang sama, relay `FOR UPDATE SKIP LOCKED`, publisher confirms, metrik `poem_outbox_pending` | Event tidak hilang saat RabbitMQ mati |
| DLQ + retry berjitter | `shared/rabbit.py:159-209` (3 percobaan lalu `nack requeue=false` → `*.dlq`), `shared/backoff.py` | Poison message tidak mematikan konsumen |
| Idempotency key (klien) | `shared/idempotency.py` + header `Idempotency-Key` di poem; fail-open saat Redis mati | Retry submit tidak menduplikasi data |
| Idempotensi konsumen | search (`occurred_at` + tombstone), kbbi (`event_id`), wordcloud (`wordcloud_poem_state`); poem `INSERT ... ON DUPLICATE KEY UPDATE` (`services/poem/app/main.py:245-246`) | Aman terhadap at-least-once |
| Circuit breaker 3 status | `services/search/app/breaker.py` (Closed/Open/Half-Open; 5 kegagalan → buka 10 dtk; metrik `search_breaker_state`) | Untuk query MySQL seluruh endpoint search |
| Fallback saat DB sakit | search menyajikan *stale cache* (TTL 300 dtk) atau 503 cepat (`services/search/app/main.py:66-73,158-178`) | Degradasi halus |
| Breaker Redis fail-open | `shared/redis_breaker.py` — cache mati → lanjut ke MySQL | Cache bukan dependensi keras |
| ≥2 replika layanan stateless | `docker-compose.yml` (`AUTH/POEM/WORDCLOUD/SEARCH/KBBI/NLP`_REPLICAS default 2) | Termasuk konsumen worker |
| Self-healing 2 lapis | `restart: unless-stopped` (crash) + autoheal (unhealthy); store sengaja dikecualikan (`docker-compose.yml:437-441`) | Restart store saat beban tinggi berisiko |
| Timeout & error terstruktur | gateway `proxy_connect_timeout 3s`, `read/send 30s`, JSON 502/503/504 (`gateway/templates/default.conf.template:53-90`) | Mencegah koneksi menggantung |
| Mikro-cache + `use_stale` | wordcloud/kbbi 30 dtk, search 10 dtk per token; `error timeout updating http_502/503/504` | Menyerap lonjakan & menutup jendela error pendek |
| Pemulihan data | `deploy/backup-mysql.sh` (mysqldump `--single-transaction`), `scripts/reindex_search.py`, `scripts/reanalyze_poems.py` | Indeks turunan dapat dibangun ulang dari event |
| Hardening dasar | SG minimal (§3.2 tutorial), RabbitMQ UI hanya `127.0.0.1` (`docker-compose.yml:360-363`), rotasi log, swap 4 GB (`deploy/ec2-bootstrap.sh:19-38`) | |

### 3.2 Pemetaan ke pola rujukan tugas

| Pola rujukan (file tugas) | Status | Bukti / gap |
|---|---|---|
| Circuit breaker 3 status pada jalur sinkron | **Sebagian** | Ada untuk search→MySQL (`breaker.py`); `RedisBreaker` baru 2 status; poem/auth/wordcloud/kbbi belum → R7 |
| Ambang error + fallback | **Ada** | Threshold 5/10 dtk; fallback: stale cache (search), `use_stale` (gateway), JSON 503; perluas ke poem/auth → R7 |
| Retry koneksi database | **Sebagian** | Retry+jitter saat inisialisasi & pool ping; belum ada retry error transien (1213/2006) → R7 |
| ≥2 replika di belakang LB + health check aktif | **Sebagian** | Replika ✓; LB hanya DNS round-robin tanpa health check aktif → R8, R3 |
| Failover layer data (read replica) | **Belum** | MySQL 1 node → R4 |
| Idempotency key pada konsumen | **Ada** | search/kbbi/wordcloud/poem terbukti idempoten → §3.1 |
| DLQ untuk pesan gagal berulang | **Ada (topologi)** | `*.dlq` + retry 3×; belum ada alert & tooling replay → R6 |

### 3.3 Sifat arsitektur yang sudah membantu

- **Tidak ada pemanggilan HTTP antar-layanan** (terverifikasi: nol `httpx/requests` di `services/`) → rantai *cascading* klasik A→B→C tidak ada; amplifikasi hanya lewat dependensi bersama (MySQL/Redis/RabbitMQ) dan fan-in gateway.
- **Stateless JWT** → auth dapat digandakan tanpa sesi bersama.
- **Event-driven + tombstone** → konsumen tahan duplikat & urutan terbalik; indeks turunan selalu bisa dibangun ulang dari sumber (puisi) bila queue hilang.

---

## 4. Tabel Analisis Risiko

Kode dampak: **Total** = seluruh pengguna & fitur terdampak; **Sebagian** = fitur tertentu/degradasi; **Tunda** = pemrosesan tertunda, data tidak hilang (dengan mitigasi eksisting).

### 4.1 Titik kritis utama

| # | Titik kritis | Skenario kegagalan | Dampak availability | Mitigasi eksisting | Gap → solusi | Prio |
|---|---|---|---|---|---|---|
| R1 | **EC2 tunggal** — 1 instance, 1 AZ (`us-east-1`), 1 EBS 30 GiB, 1 EIP; 20 kontainer di dalamnya | Reboot host, gangguan AZ, kerusakan EBS, kesalahan operasional | **Total outage**; RTO perkiraan 60–120 mnt (rebuild + restore) tanpa snapshot; RPO s/d 24 jam | `restart: unless-stopped` + autoheal memulihkan kontainer setelah reboot; EIP tetap; stop/start hemat biaya | P0: snapshot EBS terjadwal (DLM) + AMI + runbook rebuild. P2: ALB + 2 instance multi-AZ | P0/P2 |
| R2 | **Gateway 1 kontainer, tanpa limit memori** (`docker-compose.yml:30-77`) | OOM / nginx macet / bug konfigurasi | **Total outage API** sampai kontainer hidup lagi (detik–menit). Tanpa limit, OOM-killer dapat memilih kontainer lain (MySQL/Redis) → dampak lebih lama | healthcheck + autoheal; error JSON; sysctl TIME_WAIT; `restart` policy bereaksi pada OOM-kill | P0: `mem_limit`/`pids_limit` semua layanan + Redis `maxmemory`; gateway kedua (port 80/81) + origin group (R3) | P0 |
| R3 | **CloudFront 1 origin** `/api/*` → `EIP:80` (§10.2 tutorial) | Origin 5xx/mati | **Total outage API** (halaman dari S3 tetap tampil, semua panggilan API gagal) | Retry bawaan CloudFront ke origin yang sama | P0: origin group (primary `EIP:80`, secondary `EIP:81` = gateway-B), kriteria failover 500/502/503/504 + kegagalan koneksi | P0 |
| R4 | **MySQL 1 node, tanpa replika/failover** (`docker-compose.yml:312-333`) | Primary mati mendadak; EBS rusak | **Total** untuk data: auth/poem/wordcloud/kbbi/search gagal; hanya baca yang ter-cache bertahan sementara (search *stale* ≤300 dtk selagi Redis hidup; mikro-cache wc/kbbi 30 dtk + `use_stale`). Tulis = 5xx. EBS rusak → restore dump (RPO ≤24 jam, R5) | healthcheck; pool ping; `depends_on service_healthy`; backup harian; reindex/reanalyze | P1: replika async + skrip promosi + runbook (RTO ±5–15 mnt). P2: RDS Multi-AZ (failover otomatis, RTO ±1–2 mnt) | P1/P2 |
| R5 | **Backup lemah** — RPO 24 jam; dump lokal di EBS yang sama; upload ke bucket **publik** (`GetObject <BUCKET>/*`, §9.3) bersama aset frontend; belum pernah diuji restore | EBS rusak, salah hapus, atau dump terunduh pihak lain | Kehilangan data s/d 24 jam; kehilangan total bila EBS rusak sebelum upload; potensi kebocoran data (hash password, isi puisi privat) | `mysqldump --single-transaction`; retensi lokal 7 hari; bucket versioning disarankan | P0: bucket privat terpisah (SSE + versioning + lifecycle) atau `Deny` prefix `backups/`; snapshot EBS DLM; drill restore terdokumentasi + catat RTO nyata | P0 |
| R6 | **RabbitMQ 1 node** (`docker-compose.yml:351-373`); **DLQ tanpa alert/replay** | Broker mati/volume rusak; poison message menumpuk di DLQ | **Tunda, data tidak hilang** (outbox menahan & mengirim ulang; konsumen idempoten). Volume rusak ⇒ queue + DLQ hilang ⇒ reindex/reanalyze manual. DLQ menumpuk tanpa ada yang tahu | Outbox, publisher confirms, DLQ, retry+jitter, `prefetch`; metrik queue sudah di-scrape (`observability/prometheus/prometheus.yml:40-46`) | P0: alert DLQ>0 & outbox pending; `scripts/dlq_inspect.py` (peek/replay — aman, konsumen idempoten). P1: backup definisi broker + runbook. P2: cluster quorum 3 node | P0/P2 |
| R7 | **Jalur sinkron → MySQL lambat** — poem/auth/wordcloud/kbbi tanpa breaker/stale (hanya search yang terlindungi) | MySQL macet tapi masih menerima koneksi | **Sebagian**: setiap request menunggu `read_timeout`, pool (maks 10/replika) cepat habis, antrean menumpuk sampai timeout gateway 30 dtk; muka pengguna: latensi naik lalu 5xx. Baca berulang diserap mikro-cache gateway | Search: breaker + stale + `PoolTimeout` 2 dtk; Redis breaker fail-open; pool ping; uji ketahanan terbukti nol 5xx saat uji | P1: generalisasi breaker ke `shared/` + pakai di poem/auth (fallback: stale Redis untuk GET); retry error transien (1213/2006) + jitter | P1 |

### 4.2 Temuan tambahan

| # | Titik kritis | Skenario | Dampak | Gap → solusi | Prio |
|---|---|---|---|---|---|
| R8 | Replika tanpa health check aktif di LB — gateway pakai DNS round-robin (`resolver ... valid=10s`) | 1 dari 2 replika mati/macet | **Sebagian**: hingga ±60 dtk (healthcheck 10 dtk ×5 retry + autoheal 10 dtk + restart) sekitar 50% request ke layanan itu menerima 502 | P1: perketat healthcheck; P2: ALB/Traefik dengan health check asli + `next_upstream` | P1/P2 |
| R9 | **Tanpa alerting**: Prometheus tanpa `rule_files`/Alertmanager; Grafana di host yang sama | Kegagalan senyap (DLQ menumpuk, outbox pending, breaker open) | Deteksi bergantung keluhan pengguna → downtime memanjang; host mati = monitoring mati | P0: Grafana alert rules (target down, rasio 5xx, `search_breaker_state=1`, `poem_outbox_pending>0`, DLQ>0) → email/webhook; CloudWatch alarm status-check; canary uptime eksternal ke `/health` via CloudFront | P0 |
| R10 | Rate limit per-IP di belakang CloudFront (`$binary_remote_addr`; `default.conf.template:13`) | Trafik semua pengguna datang dari sedikit IP edge CloudFront | **Sebagian**: satu penyalahguna dapat memicu 429 massal bagi pengguna yang berbagi edge; workaround demo `200r/s` melemahkan proteksi | P0: persempit SG ke prefix list CloudFront (bukan opsional jangka panjang) lalu gunakan header identitas dari CloudFront (mis. `CloudFront-Viewer-Address`, tambahkan ke origin request policy) — jangan pakai XFF yang dapat dipalsukan. P1: rate-based rule AWS WAF di CloudFront | P0/P1 |
| R11 | Lambda Function URL `AuthType=NONE` (§11.3 tutorial) | URL bocor/dipanggil massal | Biaya tak terkendali (menggerus kredit; budget alert terlambat menolong); tidak mematikan layanan utama | P1: reserved concurrency kecil + auth via CloudFront OAC/IAM (tutorial sudah menyarankan), atau API key | P1 |
| R12 | Redis tanpa `maxmemory` (`docker-compose.yml:340`) | Cache/AOF tumbuh tak terbatas | Risiko OOM host → memicu R2 lebih luas | P0: `--maxmemory 256mb --maxmemory-policy allkeys-lru` (TTL tetap ada; idempotency fail-open sudah didokumentasikan) | P0 |
| R13 | Deploy `docker compose up -d --build` di host produksi (§13 tutorial) | Build 5–10 mnt + recreate | Downtime singkat saat rilis; kompetisi CPU/RAM host; rollback manual | P2: build di CI → push registry → host hanya `pull`; rolling per layanan berurutan mengikuti `depends_on` | P2 |
| R14 | Dokumen: tutorial menulis "19 container" (§6) | Hitungan aktual = **20** (12 app + gateway + 3 store + 4 infra) | Kebingungan saat verifikasi | P2: perbaiki angka di tutorial | P2 |

---

## 5. Analisis skenario per kluster (sesuai deskripsi tugas)

### 5.1 Kluster 1 — Interface/Gateway: “apa yang terjadi jika gateway OOM?”

**Saat ini.** Gateway adalah satu kontainer nginx yang memegang seluruh trafik `/api/*` + file frontend (`docker-compose.yml:30-77`). Semua kontainer berjalan tanpa `mem_limit`, jadi tidak ada kontainmen: saat memori host (t3.large 8 GB + swap 4 GB) habis, kernel OOM-killer memilih korban — bisa nginx, bisa MySQL/Redis (dampak lebih buruk karena penyimpan data). Jika gateway yang terbunuh: `restart: unless-stopped` menghidupkannya dalam hitungan detik, tetapi **selama itu API mati total** karena tidak ada instance kedua; CloudFront hanya bisa melaporkan 502/504. Jika yang terbunuh autoheal/container lain, efek domino mengikuti dependensi.

**Perbaikan.** (1) `mem_limit` + `pids_limit` per kontainer (lindungi host; OOM terkurung di satu layanan). (2) Gateway kedua `gateway-b` pada port 81 + CloudFront **origin group** dengan kriteria failover 5xx → saat gateway-A mati, CloudFront otomatis melayani dari gateway-B (<1 dtk dari sisi pengguna, tanpa perubahan klien). (3) SG: port 81 hanya dari prefix list CloudFront. (4) Redis `maxmemory` agar cache tidak menjadi penyebab OOM. (5) Untuk jangka panjang, ALB dengan health check aktif di depan ≥2 instance.

### 5.2 Kluster 2 — Jalur sinkron: “apakah layanan lambat memicu penumpukan dan cascading?”

**Saat ini.** Tidak ada panggilan HTTP antar-layanan (bukti §3.3), jadi cascading tidak menjalar lewat panggilan berantai. Risiko nyata ada di dua titik berbagi:
- **MySQL bersama**: pencarian punya breaker + stale + `PoolTimeout` 2 dtk sehingga gagal cepat; tetapi poem/auth/wordcloud/kbbi **tidak** punya breaker — saat MySQL macet, pool (maks 10 per replika) terisi request yang menunggu `read_timeout`, uvicorn memegang thread, dan latensi naik berantai ke pengguna hingga gateway menyerah di 30 dtk. Batas atas keparahan: `worker_connections 1024` + timeout 30 dtk menahan penumpukan agar tidak tumbuh tanpa batas, dan mikro-cache menyerap baca berulang (wordcloud/kbbi 30 dtk; search 10 dtk/pengguna).
- **Rantai `auth_request`**: wordcloud/kbbi memverifikasi akses ke poem dulu (`default.conf.template:111-121,165-182,203-221`) dan sengaja **fail-closed** (503 JSON) bila poem tidak bisa memeriksa — keputusan keamanan yang benar, dengan konsekuensi: poem lambat = wordcloud/kbbi ikut menolak.

**Perbaikan.** Generalisasi `CircuitBreaker` (sudah 3 status) ke `shared/breaker.py`, pasang di poem/auth (ambang 5 kegagalan, reset 10 dtk), fallback GET dari stale Redis, retry error transien MySQL (1213/2006/2013) + jitter. R8 (health check replika) dan R10 (rate limit di belakang CloudFront) juga menguatkan kluster ini.

### 5.3 Kluster 3 — Asinkron/broker: “consumer crash di tengah pemrosesan?”

**Saat ini (baik).** Pesan tidak hilang: ack hanya setelah handler sukses; koneksi putus → pesan requeue (at-least-once, `shared/rabbit.py`). Konsumen dirancang idempoten (event_id / occurred_at / tombstone), jadi redelivery aman; payload rusak → DLQ; gagal 3× → DLQ; consumer search bahkan menahan pesan hingga 45 dtk saat MySQL pemadaman singkat (`services/search/app/consumer.py:84-100`). Di sisi produsen, outbox poem menahan event saat broker mati dan mengirim otomatis saat pulih.

**Gap yang ditemukan.**
1. **Kehilangan senyap di nlp-worker**: `messaging.publish()` menelan exception dan mengembalikan `None` (`services/nlp-worker/app/messaging.py:30-35`), sementara pemanggilnya mengabaikan nilai balik dan tetap meng-ack (`services/nlp-worker/app/main.py:44-52`). Jika broker sempat putus tepat di antara konsumsi dan publish, `poem.analyzed` **hilang tanpa retry** — wordcloud/kbbi/search tidak pernah menerima analisis puisi itu sampai `scripts/reanalyze_poems.py` dijalankan manual. Perbaikan: biarkan exception naik (agar mekanisme retry/DLQ aktif) atau cek nilai balik dan raise; tambah metrik kegagalan publish.
2. **RabbitMQ satu node**: durability bergantung volume; queue+DLQ hilang bila volume rusak. Mitigasi: backup definisi (`rabbitmqctl export_definitions`), runbook pemulihan (reindex/reanalyze), dan di target: cluster quorum 3 node.
3. **DLQ tanpa alert & replay**: DLQ ada tapi tidak ada yang memantau; tambahkan alert `rabbitmq_queue_messages{queue=~".*\\.dlq"} > 0` + skrip `dlq_inspect.py` (replay aman karena konsumen idempoten; poem memakai upsert tag).

Selebihnya kluster ini **sudah memenuhi pola rujukan tugas** (DLQ + idempotensi konsumen); yang kurang adalah observabilitas dan opsi pemulihan broker.

### 5.4 Kluster 4 — Layer data: “bagaimana ketersediaan jika primary mati mendadak?”

**Saat ini.** MySQL tunggal untuk 5 basis data logis (`db/init.sql`). Jika proses mati: kontainer di-restart (detik–menit; recovery InnoDB), semua tulis gagal, baca hanya yang ter-cache (search *stale* ≤300 dtk selagi Redis hidup; wc/kbbi mikro-cache 30 dtk + `use_stale`). **Tidak ada replika ⇒ tidak ada failover**; RPO bergantung backup harian (≤24 jam) dan RTO bergantung tersedianya dump yang bisa dipulihkan. Dump lokal ditulis ke `backups/` pada EBS yang sama (bukan domain kegagalan terpisah, `deploy/backup-mysql.sh:21-25`) lalu diunggah ke bucket yang juga **publik-baca** untuk aset frontend (R5) — masalah kerahasiaan sekaligus ketahanan.

**Perbaikan.** P1: replika async satu arah + skrip promosi + runbook + uji kill-primary (target RTO ±5–15 mnt, RPO detik). P2: RDS MySQL Multi-AZ (failover otomatis ±1–2 mnt, backup otomatis + PITR) dengan endpoints diganti melalui `.env`. Bersamaan: breaker generik (R7) membuat layanan gagal cepat + menyajikan stale saat DB sakit, memperkecil blast radius.

### 5.5 Lintas kluster — host, observability, biaya

- **Host (R1)** adalah *umbrella SPOF*: redundancy internal (R2/R3) tidak menolong saat instance/AZ mati. P0 minimal: snapshot EBS terjadwal + AMI + runbook (RTO turun ke ±10–20 mnt); target: ALB + 2 instance.
- **Observability (R9)**: Prometheus tanpa rules/Alertmanager; Grafana satu host dengan aplikasi. Tambahkan alert Grafana (provisioned) + CloudWatch alarm + canary eksternal; ini juga "pengawas" DLQ & outbox.
- **Biaya (R11)**: Function URL publik bisa menguras kredit; reserved concurrency + budget guard ($20/$50 sudah ada) wajib dipertahankan.

---

## 6. Diagram arsitektur yang diperbaiki

### 6.1 Kondisi saat ini (hasil tutorial)

```
Browser ──HTTPS──▶ CloudFront ─┬─ default ─▶ S3 (frontend; bucket publik-baca, dipakai juga untuk backup)
                               ├─ /api/*  ─▶ [Origin tunggal] ──HTTP:80──▶ EC2 (EIP)
                               └─ /fungsi* ▶ Lambda (Function URL, AuthType=NONE)

EC2 t3.large — 1 AZ, EBS 30 GiB gp3 (+swap 4 GB), VPC default
┌──────────────────────────────────────────────────────────────────┐
│ gateway ×1 (nginx :80, tanpa mem_limit)                          │
│   └─ DNS round-robin (tanpa health check aktif di LB)            │
│ auth ×2   poem ×2   wordcloud ×2   search ×2   kbbi ×2   nlp ×2  │
│ mysql ×1   redis ×1 (tanpa maxmemory)   rabbitmq ×1              │
│ prometheus ×1   grafana ×1   nginx-exporter ×1   autoheal ×1     │
│ (tanpa alerting; autoheal tidak memantau store)                  │
└──────────────────────────────────────────────────────────────────┘
```

### 6.2 Tahap P0 — perbaikan cepat, tetap 1 EC2 (biaya ~$0 tambahan)

```
Browser ──HTTPS──▶ CloudFront ─┬─ /* ──────▶ S3 frontend (versioning ON)
                  (opsi WAF     │
                   rate rule)   ├─ /api/* ─▶ [Origin group: failover 5xx/timeout]
                                │             ├─ primary   ▸ EIP:80  gateway-A ─┐
                                │             └─ secondary ▸ EIP:81  gateway-B ─┤
                                │                                              ▼
                                │      DNS + autoheal + mem_limit/pids_limit semua kontainer
                                │      auth×2 poem×2 wc×2 search×2 kbbi×2 nlp×2 (breaker generik utk DB)
                                │                       │
                                │   MySQL(1) ──dump──▶ S3 backup privat (SSE, versioning)
                                │            └─ snapshot EBS harian (DLM)
                                │   Redis(1) maxmemory   ·   RabbitMQ(1) + alert DLQ + skrip replay
                                └─ /fungsi* ─▶ Lambda (CloudFront OAC/IAM + reserved concurrency)

Alerting: Grafana rules (breaker, outbox pending, DLQ>0, 5xx, target down)
          + CloudWatch alarm + canary uptime eksternal ke /health
```

### 6.3 Tahap P2 — target produksi (multi-AZ, failover terkelola)

```
Browser ──▶ CloudFront (WAF) ──▶ ALB (health check aktif, 2 AZ)
              │                    ├─ EC2/ECS app ×N (stateless; gateway ×2)
              │                    └─ ...
              ├─▶ S3 frontend                          ┌ RDS MySQL Multi-AZ (failover otomatis, PITR)
              └─▶ Lambda (OAC)                          ├ ElastiCache Redis (replica + failover otomatis)
                                                        ├ RabbitMQ cluster 3 node (quorum queue)
                                                        └ Backup: snapshot + PITR → bucket privat
Observability terpisah dari host aplikasi (Alertmanager → email/Slack; synthetics canary)
```

---

## 7. Rincian solusi teknis

Format: **akar masalah → solusi → langkah implementasi → cara verifikasi**. Pola rujukan tugas ditandai `[pola]`.

### R1 — Host tunggal
- **Akar**: seluruh beban pada 1 instance/AZ/EBS; tanpa citra siap pakai & snapshot terjadwal.
- **P0**: (a) buat AMI `puisi-app-golden` setelah deploy pertama; (b) jadwalkan snapshot EBS harian via Data Lifecycle Manager (retensi 7); (c) runbook rebuild: launch dari AMI → attach EIP → `docker compose up -d` → restore dump terakhir → reindex/reanalyze. `[failover layer data — pemulihan]`
- **P2**: CloudFront → ALB (listener 80, target group health check `/health`) → 2 instance di 2 AZ; instance dari launch template; sesi tidak masalah karena JWT stateless.
- **Verifikasi**: ukur RTO nyata dari drill rebuild; catat di laporan.

### R2 — Gateway tunggal & ketiadaan limit memori
- **Akar**: 1 kontainer HTTP entry-point; tidak ada pemisahan sumber daya antar-kontainer.
- **P0**: tambah `mem_limit`, `mem_reservation`, `pids_limit` per layanan (mis. gateway 128m/256m; mysql 1.5g; rabbitmq 512m; prometheus 512m; layanan app 256–384m — kalibrasi saat uji beban); Redis `--maxmemory 256mb --maxmemory-policy allkeys-lru`. `[operasional OOM]`
- **P0**: layanan `gateway-b` (image sama, `ports: "81:80"`, volume sama, env sama, label autoheal) + origin group CloudFront (R3).
- **Verifikasi**: `docker inspect` limit terpasang; injeksi memori (`stress`) di dalam kontainer tidak menumbangkan host; `docker stop gateway-a` → trafik via `:81` tetap 200.

### R3 — CloudFront satu origin
- **Akar**: `/api/*` menunjuk satu origin; tidak ada failover.
- **P0**: console CloudFront → Origin group `api-origins`: primary `<EIP>:80`, secondary `<EIP>:81`; failover criteria 500/502/503/504; SG port 81 hanya dari prefix list CloudFront. `[≥2 replika + LB + health check]`
- **Verifikasi**: `docker stop gateway-a`; `curl` beruntun ke `https://<CF>/health` tetap 200; metrik error origin di CloudFront tidak terlihat pengguna.

### R4 — MySQL tanpa failover
- **Akar**: satu instance menyimpan semua data; tidak ada replika; alat promosi tidak ada.
- **P1**: `mysql-replica` (compose profile `ha`) dengan replikasi async (GTID); `scripts/mysql_promote.sh` (STOP SLAVE → RESET SLAVE ALL → verifikasi read-only off), runbook ganti `MYSQL_HOST` via `.env` + `up -d --force-recreate auth poem wordcloud search kbbi`. Ukur RTO/RPO pada drill. Pertimbangkan mengarahkan baca search ke replika (offload). `[read replica/failover layer data]`
- **P2**: RDS MySQL Multi-AZ; migrasi via dump → RDS; security group RDS hanya dari SG EC2.
- **Verifikasi**: `docker stop mysql` → promosikan replika < 15 mnt; data terakhir terverifikasi; catat.

### R5 — Backup & restore
- **Akar**: dump lokal satu EBS dengan DB; tujuan upload adalah bucket publik-baca aset; RPO 24 jam; restore tak teruji.
- **P0**: bucket `puisi-backup-<acak>` terpisah (Block Public Access ON, SSE-S3, versioning, lifecycle 30/90 hari); ubah cron `BACKUP_S3=s3://puisi-backup-.../db`; DLM snapshot harian; **drill restore** ke container MySQL sementara + dokumentasikan langkah & durasi; pertahankan `--single-transaction`; aktifkan binlog MySQL (opsi PITR). `[failover layer data]`
- **Verifikasi**: restore dump ke DB baru, jalankan `scripts/e2e_smoke.py` terhadap stack sementara, catat RTO; audit bucket: tidak ada objek dump yang publik.

### R6 — Broker tunggal & DLQ tanpa pengawasan/replay
- **P0**: alert Grafana `rabbitmq_queue_messages{queue=~".*\\.dlq"} > 0` dan `poem_outbox_pending > 0` (metrik sudah tersedia); skrip `scripts/dlq_inspect.py` (list/peek/replay via pika, dengan konfirmasi; aman karena konsumen idempoten); perbaikan nlp-worker (§5.3): jangan telan kegagalan publish. `[DLQ]`
- **P1**: cron `rabbitmqctl export_definitions` → S3 privat; runbook pemulihan (redeclare topologi otomatis saat start; reindex/reanalyze bila queue hilang).
- **P2**: cluster 3 node + quorum queue (replikasi pesan antar-node).
- **Verifikasi**: suntik pesan rusak → muncul di DLQ → alert menyala → replay mengembalikan pemrosesan.

### R7 — Breaker/stale hanya di search
- **Akar**: pool mysql poem/auth menahan request saat DB lambat; tidak ada threshold/fallback.
- **P1**: pindahkan `CircuitBreaker` ke `shared/breaker.py`; pasang di poem/auth (dan wordcloud/kbbi) dengan `counts_as_failure` yang tepat (jangan hitung `PoolTimeout`); fallback: GET disajikan dari stale Redis (poem sudah punya cache detail/list; tambahkan jalur stale) atau 503 cepat JSON; retry error transien MySQL (1213, 1205, 2006, 2013) dengan `full_jitter`. `[circuit breaker 3 status; ambang + fallback; retry DB]`
- **Verifikasi**: `docker pause mysql` 60 dtk → endpoint search menyajikan stale; poem/auth membalas cepat (bukan menggantung); `SIGSTOP` pada MySQL lalu ukur rasio 5xx; breaker kembali Closed otomatis setelah pulih.

### R8 — Replika tanpa health check aktif
- **Akar**: DNS round-robin + autoheal memberi jendela gagal ±60 dtk.
- **P1 (murah)**: pastikan semua layanan ber-`healthcheck` tervalidasi (ada), turunkan `interval` bila mau (trade-off beban), dan dokumentasikan jendela. **P2**: ALB/Traefik manajemen kontainer dengan health check aktif + retry ke replika lain (`next_upstream`). `[LB + health check]`
- **Verifikasi**: `docker kill --signal=SIGSTOP <replica>` → hitung rasio 502 pada rentang jendela; target < 0,5% request.

### R9 — Alerting & deteksi
- **Akar**: tidak ada rule/alertmanager; observability satu host.
- **P0**: file alert rules Grafana (provisioned): target down (`up==0`), rasio 5xx > 1% (5 mnt), `search_breaker_state > 0` (5 mnt), `poem_outbox_pending > 0` (10 mnt) & `poem_outbox_oldest_pending_seconds` besar, DLQ>0, `search` p95 > 300 ms (10 mnt); kontak: email/webhook. CloudWatch: alarm status-check EC2 + billing. Canary eksternal (CloudWatch Synthetics/UptimeRobot) memeriksa `https://<CF>/health` tiap menit. `[deteksi kegagalan]`
- **Verifikasi**: stop MySQL → alert "breaker/5xx" menyala dalam ≤5 mnt; stop rabbitmq → DLQ/outbox alert; stop EC2 (uji terjadwal) → canary mengirim notifikasi.

### R10 — Rate limit & origin di belakang CloudFront
- **Akar**: kunci limit = IP edge CloudFront; origin masih terbuka `0.0.0.0/0` (opsional dipersempit menurut tutorial).
- **P0**: persempit SG 80/81 ke prefix list `com.amazonaws.global.cloudfront.origin-facing`; jika tetap Ingress-Limit di nginx, kunci memakai header identitas CloudFront (mis. `CloudFront-Viewer-Address`, pastikan ditambahkan ke origin request policy) — bukan XFF yang mudah dipalsukan. **P1**: rate-based rule AWS WAF di CloudFront (menggantikan limit per-edge). `[proteksi fan-in]`
- **Verifikasi**: `curl` langsung ke EIP dari luar → timeout/refused; serangan burst dari satu klien → hanya klien itu yang 429 (uji dengan dua token/IP).

### R11 — Lambda publik
- **Akar**: Function URL `AuthType=NONE`.
- **P1**: reserved concurrency (mis. 5) sebagai sekring biaya; tambahkan origin CloudFront dengan OAC + IAM `lambda:InvokeFunctionUrl`; hapus akses Function URL publik atau rotasi URL; pertahankan budget alert. `[kontrol biaya]`
- **Verifikasi**: panggil URL lama → 403; hanya lewat CloudFront yang berhasil.

### R12 — Redis tanpa batas memori
- Lihat R2 (bagian Redis). **Verifikasi**: `maxmemory` tampil di `INFO memory`; isi cache hingga penuh → eviksi LRU, layanan tetap sehat.

### R13 — Proses rilis
- **P2**: build di CI (GitHub Actions) → push image ke ECR/registry publik → host `docker compose pull && up -d --force-recreate <svc>` per layanan; migrasi skema idempoten (sudah pola `init_schema`/ALTER saat start); simpan N-1 tag untuk rollback cepat. `[operasional]`
- **Verifikasi**: rilis tanpa 5xx (pantau 5 mnt), rollback satu perintah saat simulasi gagal.

### R14 — Perbaikan dokumen
- Perbarui angka kontainer (19 → 20) dan tambahkan tautan runbook baru di §13/§15 tutorial.

---

## 8. Dampak terhadap SLO & error budget

SLO 99%/30 hari ⇒ anggaran **432 menit** gangguan terlihat pengguna per bulan (`docs/slo.md:21-23`). Estimasi per kejadian (asumsi bulan penuh trafik demo):

| Skenario | Durasi perkiraan | % anggaran terpakai | Catatan |
|---|---|---|---|
| Gateway OOM → restart otomatis (single) | ±10–60 dtk | 0,04–0,2% | Total untuk API selama jendela |
| Replika macet → jendela DNS/autoheal | ±60 dtk (≈50% request) | ±0,07–0,12% (efektif) | R8 |
| MySQL restart (crash recovery) | ±1–5 mnt | 0,2–1,2% | Tulis gagal; baca sebagian dari cache |
| Kehilangan EBS tanpa snapshot | Jam–hari (restore + reindex) | **>100% (habis)** | R1/R5; drill menurunkan RTO |
| Broker mati singkat | 0% 5xx | 0% | Event tertunda (outbox); SLO kebenaran pemrosesan yang terpantau: DLQ=0 & antrean terkuras |
| Pelanggaran berulang rate limit salah kunci | Insidental sepanjang demo | 0–1% tergantung | R10 |

Kesimpulan: untuk demo, mitigasi P0 (origin group, limit, alert, backup, DLQ tooling) memindahkan sistem dari "satu kesalahan kecil bisa melampaui anggaran bulanan" menjadi "gangguan terkurung dan terdeteksi".

---

## 9. Prioritas & rencana tindak lanjut

**P0 (lindungi demo & data, biaya ±$0):** R2+R12 limit resource; R3 gateway kedua + origin group; R9 alerting + canary; R5 bucket backup privat + snapshot DLM + drill; R6 alert DLQ + `dlq_inspect.py` + fix publish nlp-worker.
**P1 (menengah):** R4 replika MySQL + promosi; R7 breaker generik poem/auth + stale; R8 penguatan jendela replika; R10 WAF/kunci limit; R11 reserved concurrency + OAC.
**P2 (target produksi, untuk laporan/roadmap):** R1 ALB + multi-AZ + EKS/ECS; R4 RDS Multi-AZ; R6 cluster quorum; R9 observability terpisah; R13 CI/CD + registry.

Langkah berikutnya yang disarankan: kerjakan P0 dalam satu iterasi implementasi (perubahan compose/template/observability + skrip + uji kegagalan sebagai bukti), lalu perbarui laporan ini pada bagian "hasil verifikasi" — mengikuti gaya `docs/Laporan-Uji-Ketahanan.md`.

---

## Lampiran A — Catatan kecil & koreksi dokumen

1. Tutorial §6 menyebut "19 container"; konfigurasi aktual = **20** (6 layanan × 2 replika + gateway + mysql + redis + rabbitmq + prometheus + grafana + nginx-exporter + autoheal).
2. §3.2 mempersempit SG port 80 ke prefix list CloudFront sebagai "opsional, disarankan setelah demo" — untuk jangka panjang ini **wajib** (R10): selama terbuka, siapa pun bisa memanggil origin langsung dan melewati perlindungan CloudFront.
3. `deploy/backup-mysql.sh` menulis dump ke `backups/` di dalam direktori repo pada EBS yang sama — pastikan upload S3 berjalan dan bucket tujuan bukan bucket publik aset.
4. Contoh cron di skrip backup masih memakai `s3://puisi-backups/db` (nama ilustratif) — samakan dengan bucket privat sebenarnya saat implementasi.

## Lampiran B — Petunjuk verifikasi kode (ringkas)

| Klaim | Lokasi |
|---|---|
| Gateway tunggal, tanpa limit, sysctl, healthcheck | `docker-compose.yml:30-77` |
| 2 replika per layanan | `docker-compose.yml:89-90,126-127,167-168,206-207,249-250,290-291` |
| MySQL satu node + volume | `docker-compose.yml:312-333` |
| Redis tanpa maxmemory | `docker-compose.yml:335-349` |
| RabbitMQ satu node, UI lokal | `docker-compose.yml:351-373` |
| autoheal hanya layanan stateless | `docker-compose.yml:437-465` |
| DNS tanpa health check + timeout + error JSON | `gateway/templates/default.conf.template:33-56,75-90` |
| Rantai auth_request (fail-closed) | `gateway/templates/default.conf.template:111-121,165-182,203-221` |
| Breaker 3 status search | `services/search/app/breaker.py` + `services/search/app/main.py:158-178` |
| Stale cache search 300 dtk | `services/search/app/config.py:40-43` + `main.py:66-73` |
| Retry/DLQ konsumen + jitter | `shared/rabbit.py:159-209`, `shared/backoff.py` |
| Outbox poem | `services/poem/app/outbox.py` |
| Idempotency fail-open | `shared/idempotency.py` |
| Publish nlp-worker menelan error | `services/nlp-worker/app/messaging.py:30-35` + `main.py:44-52` |
| Tanpa alert rules | `observability/prometheus/prometheus.yml` (tanpa `rule_files`), `docker-compose.yml:378-399` |
| Backup | `deploy/backup-mysql.sh:30-41` |
| SLO & error budget | `docs/slo.md:10-23,151-161` |
| Lambda NONE, bucket publik, backup ke bucket sama | `docs/deploy-aws-tutorial.md:231-245,357-363,438-443` |
