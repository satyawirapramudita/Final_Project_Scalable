# Stress Test di AWS — Panduan Lengkap

> **Untuk demo kelas di sandbox, pakai [`Demo-Kelas-AWS.md`](Demo-Kelas-AWS.md)** (satu perintah:
> `bash loadtest/kelas.sh`). Deploy sandbox sudah mengisi 1000 puisi asli, jadi jangan jalankan
> `publish_fake_events.py` di sana.

Dokumen ini melengkapi `docs/deploy-aws-tutorial.md` dan `docs/slo.md`. Isinya cara
menjalankan uji beban + uji kegagalan terhadap stack yang sudah di-deploy di AWS,
mengumpulkan bukti, dan mengisi tabel analisis risiko.

Skrip utama: `loadtest/aws_stress.js` (profil `smoke`, `ramp`, `spike`, `soak`,
`mixed`, `write`). Skrip lama tetap dipakai untuk kasus khusus:

| Skrip | Kegunaan |
|---|---|
| `loadtest/aws_stress.js` | Beban search / campuran / write-heavy dengan beberapa profil |
| `loadtest/submit.js` | Idempotency: normal, duplikat, key bentrok |
| `loadtest/wordcloud.js` | Beban word cloud per puisi |
| `loadtest/stress_test.py` | Hanya lokal: menaikkan replika container otomatis |

> `stress_test.py` **jangan** dipakai di AWS: ia menjalankan `docker compose` di
> mesin yang sama dengan stack. Untuk AWS, semua beban lewat `BASE_URL`.

---

## 1. Apa yang diukur

1. **Kapasitas**: titik jenuh (throughput berhenti naik) dan titik patah SLO.
2. **SLO**: p95 < 300 ms, p99 < 500 ms, error < 1% untuk search; sesuaikan untuk
   endpoint tulis.
3. **Ketahanan**: gateway, instance aplikasi, database, Redis, RabbitMQ, consumer.
4. **Integritas data**: tidak ada duplikat, outbox terkuras, DLQ kosong, tidak ada
   pesan hilang.

---

## 2. Prasyarat

| Hal | Cara memastikan |
|---|---|
| Stack sehat | `curl -s $BASE/health` → 200; semua container `healthy` |
| Data seed | `docker compose exec -T search python - --count 1000 --user 99 < scripts/publish_fake_events.py` |
| Token | Minimal 1; sebaiknya 5–20 token untuk cache yang realistis |
| Rate limit | Naikkan sementara `API_RATE_LIMIT` / `API_BURST` |
| Cache API | `/api/*` di CloudFront harus **CachingDisabled** |
| Backup | `bash deploy/backup-mysql.sh` sebelum uji destruktif |
| Monitoring | Prometheus + Grafana + CloudWatch aktif |
| Load generator | Idealnya EC2 di region yang sama; laptop hanya untuk uji fungsi |

### 2.1 Token banyak (disarankan)

Jalankan di host aplikasi (EC2):

```bash
cd ~/Final_Project_Scalable
for i in $(seq 1 20); do
  docker compose exec -T poem python -c \
    "from shared.security import create_token; print(create_token(user_id=99, username='load$i'))"
done > tokens.txt
```

Semua token memakai `user_id=99` (pemilik data seed), tetapi username berbeda
sehingga token berbeda. Cache pencarian gateway memuat token, jadi 20 token
membuat hit rate lebih realistis.

### 2.2 Naikkan rate limit sementara

Di host aplikasi, edit `.env`:

```dotenv
API_RATE_LIMIT=100000r/s
API_BURST=100000
```

Lalu recreate gateway (dan gateway-b bila ada):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --no-deps --force-recreate gateway gateway-b
```

Setelah uji selesai, kembalikan nilai asli dan recreate lagi. Di AWS, gateway
melihat IP ALB/CloudFront, jadi semua pengguna tampak satu IP; rate limit per IP
tidak bisa dipakai apa adanya untuk uji beban.

### 2.3 Seed data dan ambil ID

```bash
docker compose exec -T search python - --count 1000 --user 99 < scripts/publish_fake_events.py

TOKEN=$(head -1 tokens.txt)
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/poems?limit=5" \
  | jq -r '.puisi[].id' | paste -sd, -
```

Hasilnya dipakai sebagai `IDS` untuk profil `mixed`.

---

## 3. Variabel environment

| Variabel | Default | Keterangan |
|---|---|---|
| `BASE_URL` | `http://localhost:8080` | ALB / CloudFront / EIP |
| `TOKEN` | — | Satu JWT |
| `TOKENS` | — | Banyak JWT, dipisah koma (prioritas di atas `TOKEN`) |
| `PROFILE` | `smoke` | `smoke`, `ramp`, `spike`, `soak`, `mixed`, `write` |
| `VUS` | `40` | VU untuk `soak`, `mixed`, `write` |
| `DURATION` | `5m` | Durasi `mixed` / `write` |
| `MAX_VUS` | `320` | VU puncak `ramp` |
| `STAGE_DURATION` | `30s` | Durasi tiap tahap `ramp` |
| `SPIKE_VUS` | `300` | VU puncak `spike` |
| `SOAK_DURATION` | `30m` | Durasi `soak` |
| `IDS` | kosong | ID puisi untuk `mixed` (wordcloud/kbbi) |
| `WRITE_RATIO` | `0.8` | Porsi POST pada profil `write` |
| `P95_MS` | `300` | Ambang p95 |
| `P99_MS` | `500` | Ambang p99 |
| `ERROR_RATE` | `0.01` | Ambang error (0.01 = 1%) |

---

## 4. Profil dan perintah

Semua contoh memakai Docker agar tidak perlu memasang k6. Dari akar repo:

```bash
BASE=https://<domain-atau-alb>
TOKENS=$(paste -sd, tokens.txt)

k6run() {
  local summary="${SUMMARY:-last.json}"
  docker run --rm -i \
    --user "$(id -u):$(id -g)" \
    -v "$PWD/loadtest:/scripts:ro,z" \
    -v "$PWD/loadtest/results:/out:z" \
    -e BASE_URL="$BASE" -e TOKENS="$TOKENS" "$@" \
    grafana/k6 run --quiet --summary-export "/out/$summary" /scripts/aws_stress.js
}
```

> Ganti nama hasil dengan `SUMMARY=ramp-160.json k6run -e PROFILE=ramp ...`
> agar tiap uji tidak saling menimpa.

> Di Fedora/RHEL, opsi `:z` penting untuk SELinux. Di macOS/Windows, ganti
> `$PWD` dengan path yang sesuai.

| Profil | Perintah | Tujuan |
|---|---|---|
| `smoke` | `k6run -e PROFILE=smoke` | Pastikan deploy hidup |
| `ramp` | `k6run -e PROFILE=ramp -e MAX_VUS=160 -e STAGE_DURATION=30s` | Cari titik jenuh & patah |
| `spike` | `k6run -e PROFILE=spike -e SPIKE_VUS=300` | Lonjakan mendadak |
| `soak` | `k6run -e PROFILE=soak -e VUS=50 -e SOAK_DURATION=30m` | Kebocoran memori/queue |
| `mixed` | `k6run -e PROFILE=mixed -e VUS=40 -e DURATION=5m -e IDS=12,13,14` | Beban campuran |
| `write` | `k6run -e PROFILE=write -e VUS=20 -e DURATION=3m -e WRITE_RATIO=0.8` | Write-heavy + outbox |

`--summary-export` menyimpan JSON ke `loadtest/results/last.json`. Ganti nama
file per uji agar tidak tertimpa.

### 4.1 Dari laptop vs load generator EC2

- **Laptop** cukup untuk uji fungsi. Latensi dari Indonesia ke `us-east-1`
  menambah ±200 ms; angka p95 tidak bisa dibandingkan dengan SLO.
- **EC2 load generator** di region yang sama memberi angka yang adil. Buat
  `t3.micro`/`t3.small`, pasang Docker, lalu jalankan perintah yang sama.
  Instance ini tidak perlu ikut ALB.

> Untuk angka kapasitas, tembak **ALB langsung** (bukan CloudFront) agar tidak
> tercampur cache edge. Untuk uji pengalaman pengguna end-to-end, baru pakai
> CloudFront.

```bash
# di load generator
sudo apt-get update && sudo apt-get install -y docker.io
sudo usermod -aG docker ubuntu   # logout/login setelah ini
git clone <repo> && cd Final_Project_Scalable
```

### 4.2 Uji cepat di lokal (sebelum deploy)

Kalau stack lokal masih jalan, skrip yang sama bisa diuji tanpa ALB. Bedanya,
container k6 harus masuk network Compose dan `BASE_URL` memakai nama service:

```bash
TOKEN=$(docker compose exec -T poem python -c \
  'from shared.security import create_token; print(create_token(user_id=99, username="loadtest"))')

docker run --rm -i --network puisi-scalable_edge \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/loadtest:/scripts:ro,z" \
  -e BASE_URL=http://gateway -e TOKEN="$TOKEN" \
  -e PROFILE=soak -e VUS=1 -e SOAK_DURATION=5s \
  grafana/k6 run /scripts/aws_stress.js
```

> Rate limit gateway lokal (20 r/s) akan menghasilkan banyak 429 kalau hanya
> satu VU. Untuk uji lokal yang bersih, naikkan `API_RATE_LIMIT` seperti di
> bagian 2.2, atau pakai `PROFILE=smoke` dengan satu VU dan terima 429 sebagai
> bukti rate limit bekerja.

---

## 5. Runbook skenario

Urutan yang disarankan: S1 → S2 → S3 → S4 → S5 → S6 → S7 → S8 → S9 → S10 → S11 →
S12 → S13 → S14 → S15 → S16. Uji destruktif (S8–S14) hanya setelah baseline
stabil dan backup selesai.

### S1 — Smoke

```bash
k6run -e PROFILE=smoke
```

**Lulus jika:** semua 200, error 0%, p95 < 300 ms. Jika gagal, jangan lanjut.

### S2 — Ramp (kapasitas)

```bash
k6run -e PROFILE=ramp -e MAX_VUS=160 -e STAGE_DURATION=30s
```

Catat per tahap: throughput, p50/p95/p99, error, CPU app/DB. Tentukan:

- **titik jenuh**: throughput tidak naik lagi;
- **titik patah**: tahap pertama yang melewati p95 300 ms / p99 500 ms / error 1%.

Profil `ramp` sengaja tanpa threshold agar uji tidak berhenti di tahap pertama
yang gagal. Jika ingin berhenti otomatis, tambahkan `--abort-on-error` atau
threshold sendiri.

### S3 — Spike

```bash
k6run -e PROFILE=spike -e SPIKE_VUS=300
```

**Amati:** 502/504, antrean RabbitMQ, outbox, CPU, OOM. **Lulus jika:** pulih
dalam < 60 detik setelah spike turun, antrean terkuras, tidak ada container mati.

### S4 — Soak

```bash
k6run -e PROFILE=soak -e VUS=50 -e SOAK_DURATION=30m
```

Pantau tiap 5 menit: memori container, `Threads_connected`, panjang antrean,
outbox, DLQ. **Lulus jika:** tidak ada tren memori naik terus, error < 1%,
outbox/DLQ tetap 0.

### S5 — Mixed

```bash
k6run -e PROFILE=mixed -e VUS=40 -e DURATION=5m -e IDS=12,13,14
```

Beban: 60% search, 20% daftar puisi, 10% wordcloud, 10% kbbi. **Lulus jika:**
semua endpoint < SLO masing-masing, error < 1%, MySQL tidak kehabisan koneksi.

### S6 — Write-heavy

```bash
k6run -e PROFILE=write -e VUS=20 -e DURATION=3m -e WRITE_RATIO=0.8
```

**Lulus jika:** POST 201, error < 1%, outbox terkuras setelah broker sehat,
tidak ada duplikat. Cek:

```sql
SELECT COUNT(*) FROM poem_db.outbox WHERE published_at IS NULL;
SELECT COUNT(*) FROM poem_db.puisi WHERE judul LIKE 'Stress AWS %';
```

### S7 — Idempotency (skrip lama)

```bash
docker run --rm -i --user "$(id -u):$(id -g)" \
  -v "$PWD/loadtest:/scripts:ro,z" \
  -e BASE_URL="$BASE" -e TOKEN="$(head -1 tokens.txt)" \
  -e SCENARIO=submit-duplikat -e VUS=5 -e DURATION=2m \
  grafana/k6 run /scripts/submit.js
```

Ulangi dengan `SCENARIO=key-bentrok`. **Lulus jika:** duplikat mengembalikan
`id_puisi` yang sama (`Idempotent-Replay: true`), key bentrok → 409, dan jumlah
baris di DB tidak bertambah untuk duplikat.

### S8 — Gateway failover

Sesuaikan dengan arsitektur:

- **ALB dengan 2 target host:** matikan gateway di salah satu host.
- **CloudFront origin group (2 container di 1 host):** matikan `puisi-gateway`.

```bash
# terminal 1: beban
k6run -e PROFILE=soak -e VUS=20 -e SOAK_DURATION=5m

# terminal 2: injeksi
ssh -i key.pem ubuntu@<host> 'docker stop puisi-gateway'
date -Is
```

Amati ALB `HealthyHostCount` / `UnHealthyHostCount`, error 5xx, dan waktu sampai
200 lagi. Pulihkan:

```bash
ssh -i key.pem ubuntu@<host> 'docker start puisi-gateway'
```

**Lulus jika:** tidak ada outage total; 5xx hanya sesaat; RTO < 60 detik.

### S9 — Instance aplikasi mati

```bash
aws ec2 stop-instances --instance-ids <APP1_ID>
```

Amati ALB mengeluarkan target, lalu start lagi:

```bash
aws ec2 start-instances --instance-ids <APP1_ID>
```

**Lulus jika:** target lain melayani, error terbatas, RTO < 3 menit, data tidak
hilang. Catat juga waktu `HealthyHostCount` kembali normal.

### S10 — Database mati / reboot

Jika memakai RDS:

```bash
aws rds reboot-db-instance --db-instance-identifier <DB_ID>
```

Jika MySQL masih self-managed:

```bash
ssh -i key.pem ubuntu@<data-host> 'docker kill puisi-mysql'
```

Amati: `search_breaker_state`, 5xx, request menggantung, waktu sampai 200 lagi.
**Lulus jika:** breaker search membuka (fail-fast), tidak ada data hilang, RTO
tercatat. Untuk RDS, catat juga apakah read replica bisa dipromosikan (jika ada).

### S11 — Redis mati

```bash
ssh -i key.pem ubuntu@<data-host> 'docker stop puisi-redis'
```

Saat Redis mati, uji idempotency:

```bash
KEY=$(uuidgen)
for i in 1 2; do
  curl -s -X POST "$BASE/api/poems" \
    -H "Authorization: Bearer $(head -1 tokens.txt)" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $KEY" \
    -d '{"judul":"Uji Redis","isi":"baris","kategori":"Alam"}' \
    -o /dev/null -w '%{http_code}\n'
done
```

Cek jumlah baris `Uji Redis`. **Temuan yang diharapkan:** idempotency fail-open →
2 baris. Ini bukti untuk tabel risiko. Pulihkan Redis dan verifikasi `PONG`.

### S12 — RabbitMQ mati

```bash
ssh -i key.pem ubuntu@<data-host> 'docker stop puisi-rabbit-1'
```

> Jika stack masih single-node, container-nya `puisi-rabbitmq`, bukan `puisi-rabbit-1`.

Saat broker mati, submit 10–20 puisi. Cek outbox:

```sql
SELECT COUNT(*) FROM poem_db.outbox WHERE published_at IS NULL;
```

Hidupkan lagi, tunggu drain, lalu cek `search_poem` dan `term_frequency`.
**Lulus jika:** POST tetap 201, tidak ada pesan hilang, outbox menjadi 0.

### S13 — Consumer crash / DLQ

```bash
ssh -i key.pem ubuntu@<app-host> 'docker kill $(docker ps -q --filter name=nlp-worker | head -1)'
```

Cek antrean:

```bash
docker compose exec -T rabbit-1 rabbitmqctl list_queues \
  name messages messages_unacknowledged consumers
```

Uji poison message (opsional): publish body bukan JSON ke `nlp.q`, lalu cek
`nlp.q.dlq`. Lihat isi DLQ dan replay setelah penyebab diperbaiki:

```bash
docker compose exec -T poem python - < scripts/replay_dlq.py --list
docker compose exec -T poem python - < scripts/replay_dlq.py --queue search.q --replay
```

**Lulus jika:** tidak ada pesan hilang, DLQ terisolasi, replay aman (idempoten).

### S14 — Network partition

Blokir sementara akses ke database dari satu host (jangan blokir SSH):

```bash
ssh -i key.pem ubuntu@<app-host> \
  'sudo iptables -I OUTPUT -d <RDS_IP> -j DROP; sleep 120; sudo iptables -D OUTPUT -d <RDS_IP> -j DROP'
```

Atau di single host: `docker network disconnect puisi-scalable_data $(docker compose ps -q poem | head -1)`.
Amati error, breaker, dan pemulihan. **Lulus jika:** tidak ada request menggantung
selamanya; setelah koneksi pulih, service kembali sehat tanpa restart manual.

### S15 — Healthcheck palsu dan alert

Saat database mati:

```bash
curl -s -o /dev/null -w 'health=%{http_code}\n' "$BASE/health"
curl -s -o /dev/null -w 'search=%{http_code}\n' \
  -H "Authorization: Bearer $(head -1 tokens.txt)" "$BASE/api/search?q=senja"
```

Cek alert Prometheus:

```bash
curl -s http://<prometheus>:9090/api/v1/alerts | jq '.data.alerts | length'
```

**Temuan yang diharapkan:** `/health` tetap 200 walau dependency mati; alert
harus menyala. Catat waktu deteksi (menit) sebagai bukti.

### S16 — Recovery dan integritas

Setelah semua uji:

```sql
SELECT COUNT(*) FROM poem_db.puisi;
SELECT COUNT(*) FROM poem_db.outbox WHERE published_at IS NULL;
SELECT COUNT(*) FROM search_db.search_poem;
SELECT judul, COUNT(*) FROM poem_db.puisi GROUP BY judul HAVING COUNT(*) > 1;
```

```bash
docker compose exec -T rabbit-1 rabbitmqctl list_queues name messages consumers
```

**Lulus jika:** outbox 0, DLQ 0, tidak ada duplikat tak terduga, jumlah data
konsisten dengan sebelum uji (kecuali data uji yang memang ditambahkan).

---

## 6. Metrik dan query

### 6.1 Dari k6

`--summary-export` menghasilkan JSON. Yang dibaca:

- `http_reqs.rate` → throughput;
- `http_req_duration` → avg/med/p95/p99;
- `http_req_failed.rate` → error;
- `resp_2xx`, `resp_4xx`, `resp_5xx`, `resp_conn_error`;
- `gateway_cache_hit`.

### 6.2 Prometheus

```promql
# error ratio 5xx
sum(rate(http_requests_total{status=~"5.."}[1m]))
  / sum(rate(http_requests_total[1m]))

# p95 per service
histogram_quantile(0.95,
  sum by (service, le) (rate(http_request_duration_seconds_bucket[1m])))

# circuit breaker search (0=closed, 1=open, 2=half-open)
search_breaker_state

# outbox
poem_outbox_pending
poem_outbox_oldest_pending_seconds

# DLQ dan antrean
max by (queue) (rabbitmq_detailed_queue_messages{queue=~".*\\.dlq"})
max by (queue) (rabbitmq_detailed_queue_messages{queue!~".*\\.dlq"})

# target hidup
up{job=~"auth|poem|wordcloud|search|kbbi|nlp-worker|gateway|rabbitmq"}
```

### 6.3 CloudWatch (ALB)

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/ApplicationELB \
  --metric-name TargetResponseTime \
  --dimensions Name=LoadBalancer,Value=<alb-suffix> \
  --start-time "$(date -u -d '15 minutes ago' +%FT%TZ)" \
  --end-time "$(date -u +%FT%TZ)" \
  --period 60 --statistics Average,p95
```

Metrik lain: `RequestCount`, `HTTPCode_Target_5XX_Count`,
`HTTPCode_ELB_5XX_Count`, `HealthyHostCount`, `UnHealthyHostCount`,
`TargetResponseTime`.

### 6.4 Database, Redis, RabbitMQ

```bash
# MySQL
docker compose exec -T mysql sh -c \
  'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SHOW STATUS LIKE '\''Threads_connected'\''; SHOW STATUS LIKE '\''Max_used_connections'\'';"'

# Redis
docker compose exec -T redis redis-cli INFO memory | head
docker compose exec -T redis redis-cli DBSIZE

# RabbitMQ
docker compose exec -T rabbit-1 rabbitmqctl list_queues name messages messages_unacknowledged consumers
```

---

## 7. Template tabel hasil

| Skenario | Beban | Throughput | p95 | p99 | Error | 5xx | RTO | Catatan |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| S1 smoke | 1 VU | | | | | | — | |
| S2 ramp | 1→160 VU | | | | | | — | titik patah: … |
| S3 spike | 300 VU | | | | | | — | |
| S4 soak | 50 VU/30m | | | | | | — | |
| S5 mixed | 40 VU | | | | | | — | |
| S6 write | 20 VU | | | | | | — | |
| S8 gateway | 20 VU | | | | | | | |
| S9 instance | 20 VU | | | | | | | |
| S10 DB | 20 VU | | | | | | | |
| S11 Redis | 5 VU | | | | | | | duplikat: ya/tidak |
| S12 RabbitMQ | 20 VU | | | | | | | outbox: … |
| S13 consumer | 20 VU | | | | | | | DLQ: … |

Simpan juga screenshot Grafana/CloudWatch dan file JSON k6 sebagai bukti.

---

## 8. Troubleshooting

| Gejala | Penyebab umum | Solusi |
|---|---|---|
| Banyak 429 | Rate limit gateway masih aktif | Naikkan `API_RATE_LIMIT`/`API_BURST`, recreate gateway |
| `status=0` / connection refused | ALB/CloudFront belum siap, SG salah | Cek target health, SG port 80 |
| 404 di wordcloud/kbbi | `IDS` tidak valid | Ambil ID dari `/api/poems` |
| Cache hit 100% | Satu token + query populer | Pakai `TOKENS` banyak, variasikan query |
| p95 besar dari laptop | Latensi WAN | Jalankan k6 dari EC2 di region yang sama |
| 502 dari ALB | Target unhealthy / gateway mati | Cek `docker compose ps`, target group |
| 401 | Token kedaluwarsa | Buat token baru; cek `JWT_SECRET` |
| MySQL `Too many connections` | Pool total > `max_connections` | Kurangi worker/pool atau naikkan `max_connections` |
| k6 tidak ada | Belum ada Docker | `docker pull grafana/k6` atau pasang k6 binary |

---

## 9. Catatan khusus AWS Learner Lab

- **Region**: `us-east-1` (sesuai tugas). Kredensial dari Vocareum.
- **Instance**: maksimal 9 instance, total 32 vCPU; `t3.micro`/`t3.small` cukup
  untuk load generator.
- **IAM**: tidak bisa membuat role; pakai `LabRole`/`LabInstanceProfile`.
- **RDS**: Multi-AZ tidak tersedia; uji S10 mengukur RTO manual, bukan failover
  otomatis. Read replica bisa diuji jika kuota mengizinkan.
- **ElastiCache**: tidak tersedia; Redis tetap self-managed di EC2.
- **CloudFront/ALB**: tersedia; `/api/*` harus `CachingDisabled`.
- **Sesi lab**: resource bisa berhenti saat sesi berakhir. Jalankan uji dalam satu
  sesi; hentikan instance setelah selesai.
- **Biaya**: hentikan EC2/RDS/ALB setelah uji; simpan snapshot/backup bila perlu.

---

## 10. Checklist sebelum selesai

- [ ] Backup database dibuat sebelum uji destruktif.
- [ ] Rate limit dikembalikan ke nilai produksi.
- [ ] Semua container `healthy`; outbox 0; DLQ 0.
- [ ] Data uji dibersihkan atau didokumentasikan.
- [ ] Hasil k6 JSON + screenshot Grafana/CloudWatch disimpan.
- [ ] Tabel analisis risiko diisi dengan angka nyata (bukan asumsi).
- [ ] Resource AWS dihentikan/dihapus sesuai kebutuhan biaya.
