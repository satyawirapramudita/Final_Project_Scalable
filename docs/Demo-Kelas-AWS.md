# Demo Kelas 5 Menit — Stress Test + Failover di AWS

Panduan untuk sesi demo singkat (±5 menit presentasi) **setelah deployment ke AWS**.
Kasusnya sama dengan uji lokal: **saat layanan poem mati, wordcloud/kbbi ikut 503,
tetapi search tetap hidup** — bukti cascading failure + pemulihan.

Referensi lengkap:

- Detail uji beban: [`docs/Stress-Test-AWS.md`](Stress-Test-AWS.md)
- Langkah deployment: [`docs/deploy-aws-tutorial.md`](deploy-aws-tutorial.md)
- Target SLO: [`docs/slo.md`](slo.md)

---

## 1. Kenapa skenario ini yang dipilih

| Alasan | Penjelasan |
|---|---|
| Cepat | Injeksi 1 perintah, pulih 1 perintah, total < 90 detik |
| Terlihat jelas | Probe tiap detik menampilkan `200 → 503 → 200` |
| Nyambung dengan tugas | Langsung jadi baris **Tabel Analisis Risiko** (cascading failure) |
| Aman | Tidak menyentuh MySQL/Redis/RabbitMQ; tidak ada risiko data |
| Ada pembanding | Search tetap 200 pada beban yang sama |

Hasil uji komponen lain (gateway, MySQL, Redis, RabbitMQ) **tidak perlu diulang
live** — cukup tampilkan angka dari sesi uji lokal di slide/laporan.

---

## 2. Checklist H-1 (sehari sebelum / sebelum masuk kelas)

- [ ] Deployment selesai: `curl -s http://<ALB>/health` → `{"status":"ok","service":"gateway"}`
- [ ] Register → login → submit → search → wordcloud → kbbi semua jalan
- [ ] Data seed ada (mis. `publish_fake_events.py --count 1000`) dan ada puisi dianalisis
- [ ] `.env` sudah diatur (lihat 2.1), gateway sudah direcreate
- [ ] Backup MySQL: `BACKUP_S3=s3://<bucket>/backups bash deploy/backup-mysql.sh`
- [ ] Token demo dibuat (lihat 2.2) dan disimpan di server
- [ ] Grafana/Prometheus bisa diakses via SSH tunnel (untuk screenshot)
- [ ] Semua container `healthy`: `docker compose -f ... ps`
- [ ] Waktu demo sudah diuji sekali penuh (rehearsal) — catat angkanya

### 2.1 Atur `.env` untuk demo

```dotenv
# Lonjakan beban datang dari IP ALB/CloudFront; rate limit default 20r/s
# akan menolak hampir semua request k6 dengan 429.
API_RATE_LIMIT=100000r/s
API_BURST=100000
```

Lalu recreate gateway (keduanya):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --no-deps --force-recreate gateway gateway-b
```

> Jangan lupa mengembalikan nilai ini setelah demo (lihat bagian 6).
> Tanpa langkah ini, hasil k6 akan didominasi 429 — seperti yang terjadi
> saat rehearsal lokal (42.892 dari 42.913 request → 429).

### 2.2 Buat token demo

Di EC2:

```bash
cd ~/Final_Project_Scalable
JWT_SECRET=$(grep '^JWT_SECRET=' .env | cut -d= -f2-)
PYTHONPATH=. python scripts/mint_token.py 99 tester > ~/demo_token.txt
cat ~/demo_token.txt   # mulai dengan eyJhbGci...
```

Token ini untuk `user_id=99` — pastikan user 99 punya data (seed/atau buat akun demo).

### 2.3 Cari 1 ID puisi untuk wordcloud

```bash
TOKEN=$(cat ~/demo_token.txt)
BASE=http://localhost   # di EC2 (port 80), atau pakai ALB dari luar
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $TOKEN" "$BASE/api/wordcloud/1"
```

Harus **200**. Kalau 404, ambil ID lain:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T mysql \
  sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e \
  "SELECT id FROM poem_db.puisi WHERE status=\"done\" LIMIT 3;"'
```

---

## 3. Siapkan 3 terminal (atau 3 pane tmux)

| Terminal | Isi | Dijalankan dari |
|---|---|---|
| **A — beban** | k6 soak 90 detik ke `BASE_URL` | EC2 (paling akurat) atau laptop |
| **B — probe** | wordcloud tiap ~1 detik | EC2 atau laptop |
| **C — injeksi** | `stop poem` → 30 dtk → `start poem` | **EC2 (SSH)** — wajib |

`BASE` yang dipakai:

- Demo via **CloudFront/ALB**: `BASE=https://<CF-DOMAIN>` (paling realistis; juga
  membuktikan origin group failover bila dipakai).
- Demo langsung ke **ALB**: `BASE=http://<ALB-DNS>`.
- Di dalam EC2: `BASE=http://localhost` (port 80 dari `docker-compose.prod.yml`).

Untuk demo kelas, **jalankan k6 di EC2** supaya latensinya jujur (tidak tercampur
RTT dari Indonesia, yang bisa ±200 ms dan membuat p95 tidak bisa dibandingkan SLO).

---

## 4. Timeline 5 menit

| Waktu | Aksi | Yang diperlihatkan ke kelas |
|---|---|---|
| 0:00–0:20 | Baseline: `docker compose ps`, probe 200, search 200 | Stack sehat |
| 0:20–0:50 | Mulai k6 (Terminal A) + probe (Terminal B) | Beban berjalan, angka mengalir |
| 0:50–1:20 | `docker compose stop poem` (Terminal C) | Probe berubah `503`, search tetap `200` |
| 1:20–1:50 | `docker compose start poem` | Probe kembali `200` — catat waktu pulih |
| 1:50–2:10 | k6 selesai → tampilkan ringkasan | Throughput, p95, error search |
| 2:10–5:00 | Presentasi tabel + kesimpulan | Tabel risiko + RTO |

---

## 5. Perintah persiterminalan

### Terminal A — beban search (mulai di 0:20)

```bash
cd ~/Final_Project_Scalable
TOKEN=$(cat ~/demo_token.txt)
BASE=http://localhost    # atau ALB/CloudFront bila dari luar

docker run --rm -i \
  -v "$PWD/loadtest:/scripts:ro,z" \
  -e BASE_URL="$BASE" -e TOKEN="$TOKEN" \
  -e PROFILE=soak -e VUS=20 -e SOAK_DURATION=90s \
  grafana/k6 run /scripts/aws_stress.js
```

Jika image `grafana/k6` belum ada: `docker pull grafana/k6`.

Catatan: di SELinux aktif (Ubuntu tidak), flag `:z` tetap aman dipakai.

### Terminal B — probe wordcloud (mulai di 0:20)

```bash
TOKEN=$(cat ~/demo_token.txt)
BASE=http://localhost

timeout 150 bash -c '
while true; do
  ts=$(date +%H:%M:%S)
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 8 \
    -H "Authorization: Bearer '"$TOKEN"'" \
    "$'"$BASE"'/api/wordcloud/1")
  echo "$ts wordcloud=$code"
  sleep 1
done' | tee ~/wordcloud-demo.log
```

Saat poem mati, tiap probe menunggu connect-timeout gateway (3 dtk), jadi jeda
antara baris 503 bisa ~4–5 detik — itu normal dan justru memperlihatkan timeout.

### Terminal C — injeksi (mulai di 0:50)

```bash
cd ~/Final_Project_Scalable
date +%H:%M:%S                      # catat waktu injeksi
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop poem
sleep 30
date +%H:%M:%S                      # catat waktu pulihkan
docker compose -f docker-compose.yml -f docker-compose.prod.yml start poem
```

---

## 6. Hasil yang diharapkan (template tabel demo)

Ganti angka dengan hasil live. Contoh dari rehearsal lokal:

| Skenario | Beban | Hasil | Dampak | Mitigasi |
|---|---|---|---|---|
| Baseline | 20 VU search, 90 dtk | p95 … ms, error 0% | — | — |
| Poem mati ±30 dtk | beban sama + probe | search tetap 200; wordcloud/kbbi 503 ±32 dtk | Data turunan tak terjangkau; poem = hard dependency (`auth_request`) | Replika poem (2, sudah ada), timeout gateway 3 dtk, cache stale, breaker/fallback |

**Satu kalimat kesimpulan demo:**

> "Saat poem mati, wordcloud dan kbbi otomatis 503 dalam < 1 detik karena
> pemeriksaan akses `auth_request` di gateway, sementara search tetap 200 di
> beban yang sama. Setelah poem dinyalakan lagi, layanan pulih dalam ±X detik
> tanpa restart manual. Ini menunjukkan poem adalah titik kritis untuk data
> turunan, dan mitigasinya adalah replika + health check + cache stale."

---

## 7. Bonus 60 detik (jika waktu cukup): failover gateway-b

Deployment `docker-compose.prod.yml` menjalankan dua gateway: `gateway` (port 80)
dan `gateway-b` (port 81), dihubungkan CloudFront origin group (lihat
`deploy-aws-tutorial.md` bagian 10.4b). Demo singkatnya:

```bash
# beban tetap jalan (Terminal A), lalu:
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop gateway
# amati: CloudFront otomatis memakai gateway-b; /api/search tetap 200
docker compose -f docker-compose.yml -f docker-compose.prod.yml start gateway
```

Yang dibuktikan: **SPOF gateway** (titik kritis 1) sudah dimitigasi dengan 2
instans + failover di sisi CloudFront. Batasan: origin failover CloudFront hanya
berlaku untuk GET/HEAD/OPTIONS — untuk POST, andalkan retry + Idempotency-Key
(di frontend sudah otomatis).

---

## 8. Jika ada yang tidak sesuai rencana (cadangan)

| Kejadian | Reaksi cepat |
|---|---|
| k6 menunjukkan banyak 429 | Rate limit belum dinaikkan; sampaikan ini sebagai temuan + tunjukkan log gateway `limit_req` |
| wordcloud tidak 503 saat poem mati | Cek `docker compose ps poem`; pastikan SEMUA replika mati (`stop poem` menghentikan keduanya) |
| wordcloud 404 (bukan 503) | ID puisi salah/tidak ada; pakai ID dari bagian 2.3 |
| k6 gagal start (image tidak ada) | `docker pull grafana/k6`; atau jalankan dari laptop ke ALB |
| Recovery lambat (> 30 dtk) | Tunggu; autoheal + healthcheck butuh beberapa detik; catat RTO apa adanya |
| Demo via CloudFront dan 502 | Cek Security Group port 80/81; cek `curl http://<EIP>/health` |

**Aturan emas:** apa pun yang gagal, jadikan temuan. Laporan tugas ini justru
paling kuat bila kegagalan terukur, bukan disembunyikan.

---

## 9. Setelah demo (wajib)

```bash
# 1) Kembalikan rate limit produksi
#    (edit .env: API_RATE_LIMIT=200r/s, API_BURST=400 bila lewat CloudFront)
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --no-deps --force-recreate gateway gateway-b

# 2) Verifikasi sehat
curl -s http://localhost/health
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps

# 3) Simpan bukti
cat ~/wordcloud-demo.log          # salin ke laporan
# screenshot k6 summary + Grafana (SSH tunnel: -L 3001:127.0.0.1:3001)
```

Untuk menghemat kredit Learner Lab: hentikan EC2 (dan RDS bila dipakai) setelah
sesi, atau matikan instance saat tidak dipakai. Data aman di EBS/volume.

---

## 10. Yang dipakai untuk Tabel Analisis Risiko

Setiap baris tabel risiko kini punya bukti:

| Titik kritis | Bukti (dari mana) | Dampak | Solusi terpasang |
|---|---|---|---|
| Gateway (SPOF) | Rehearsal lokal: kill → outage ±18 dtk; live AWS: origin group gateway-b | Total outage tanpa mitigasi | 2 gateway + CloudFront failover, autoheal |
| Sync path (poem) | **Demo live**: wordcloud/kbbi 503 ±32 dtk, search tetap 200 | Data turunan tak terjangkau | Replika poem, timeout 3 dtk, cache stale, breaker search |
| Async (RabbitMQ) | Rehearsal: broker mati → outbox 5 pending → drain 0 | Tulis tetap sukses, async tertunda | Outbox + publisher confirms + quorum queue |
| Data (MySQL) | Rehearsal: kill → RTO ±6 dtk, 5xx window | API DB gagal | Backup harian + S3, pool ping=1 |
| Data (Redis) | Rehearsal: stop → idempotency fail-open → 2 baris | Duplikasi saat Redis mati | Breaker Redis; catat gap → Sentinel |
| Observability | Rehearsal: `/health` 200 saat MySQL mati; alert Prometheus aktif | Deteksi manual | Alert rules (DLQ, outbox, breaker, 5xx) |

Hasil demo live mengisi baris **Sync path** dengan angka AWS yang nyata
(downtime, RTO, perilaku search), sisanya dari bukti rehearsal.
