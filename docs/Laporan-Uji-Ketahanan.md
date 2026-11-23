# Laporan Uji Ketahanan: Idempotency Key, Jitter Retry, Connection Pool

**Tanggal**: 19 November 2026
**Lingkungan**: lokal, Docker Compose penuh (15 container) di host SELinux enforcing
**Cakupan**: implementasi 3 fitur ketahanan submit → unit test → pentest targeted → stress test k6

---

## 1. Ringkasan eksekutif

| Aspek | Status |
|---|---|
| Implementasi 3 fitur | ✅ selesai, 19 file diubah + 5 file baru |
| Unit test | ✅ 166/166 PASS (auth 103, poem 33, kbbi-linker 24, gabungan 2 urutan) |
| Pentest targeted | ✅ 17/17 PASS (`scripts/pentest_targeted.py`) |
| Stress test k6 | ✅ 4 skenario lulus SLO; 1 anomali terselidiki sampai akar (bukan bug fitur) |
| Infra startup | ✅ 5 akar masalah ditemukan & diperbaiki dengan bukti (bukan tebakan) |
| Commit | ⏳ perubahan belum di-commit; 1 verifikasi DB COUNT menunggu konfirmasi |

---

## 2. Fitur yang diimplementasikan

### 2.1 Idempotency key (`shared/idempotency.py` + poem service)
- Header `Idempotency-Key` pada `POST /api/poems`, `PUT/DELETE /api/poems/{id}`.
- Disimpan di Redis (`SET NX`, TTL 24 jam via `IDEMPOTENCY_TTL`), key ter-scope per user:
  `idem:poem:{user_id}:{key}`.
- Semantik: key + payload sama → **replay** respons pertama (header `Idempotent-Replay: true`);
  key + payload beda → **409 Conflict**; request pertama masih diproses → 409 (in-flight);
  request gagal (404/403/500) → key **dilepas** agar retry sukses.
- Fail-open: saat Redis bermasalah (circuit breaker), dedup dilewati — request tetap dilayani.
- Metrik: `idempotency_total{service,result=new|replay|conflict}`.
- Frontend otomatis mengirim key (`crypto.randomUUID()`, dipertahankan lintas retry).

### 2.2 Connection pool (DBUtils PooledDB)
- `services/poem/app/db.py` dan `services/auth/app/db.py`: pool lazy + double-checked
  locking, `ping=1` (koneksi mati otomatis dibuat ulang).
- Env: `DB_POOL_MIN` (default 2), `DB_POOL_MAX` (default 10) — sudah di compose & `.env.example`.
- `close()` pada koneksi pool mengembalikan ke pool, jadi seluruh call site tetap kompatibel.
- Metrik: `db_pool_connections{service,state=idle|in_use}` (gauge didefinisikan sekali di
  `shared/metrics.py` agar tidak bentrok antar proses uji).

### 2.3 Jitter on retry (`shared/backoff.py`)
- `full_jitter` (backoff eksponensial, untuk consumer RabbitMQ) dan `equal_jitter`
  (delay tetap, rata-rata dipertahankan).
- Diterapkan di: `shared/rabbit.py` (3 titik: koneksi, backoff handler, reconnect) dan
  loop `init_schema` di 5 service (poem, auth, search, wordcloud, kbbi-linker).
- Frontend `app.js`: retry maks 3 attempt (network error & 502/503/504), backoff + full
  jitter; GET selalu di-retry; submit di-retry dengan key yang sama; auth tidak pernah
  di-retry otomatis.

### 2.4 Pendukung
- `loadtest/submit.js` (k6): skenario `submit-normal`, `submit-duplikat`, `key-bentrok`.
- `scripts/pentest_targeted.py`: 17 check pentest targeted.
- Panel Grafana baru: "Idempotency: replay dan konflik", "Pool database: idle vs terpakai".
- README: dokumentasi header, kebijakan retry, env var.
- `services/poem/tests/` (28 test baru, conftest stub MySQL/Redis/RabbitMQ).

---

## 3. Hasil pentest targeted (17/17 PASS)

| # | Check | Severity | Hasil |
|---|---|---|---|
| 1 | Tanpa token → 401 | HIGH | PASS |
| 2 | Token signature rusak → 401 | HIGH | PASS |
| 3 | Token secret salah → 401 | HIGH | PASS |
| 4 | Replay key sama → 201 + header replay, id konsisten | HIGH | PASS |
| 5 | Key sama payload beda → 409 | HIGH | PASS |
| 6 | 20 submit paralel key sama → tepat 1 id | CRITICAL | PASS (19× 409 in-flight, 1× 201) |
| 7 | Key lintas user tidak bentrok / tidak replay | CRITICAL | PASS |
| 8 | PUT & DELETE replay | MEDIUM | PASS |
| 9 | Key raksasa (10KB), CRLF, path traversal, %00, emoji | LOW | PASS (400 / edge-refused / client-refused, nol 5xx) |
| 10 | Key dilepas saat request gagal validasi | MEDIUM | PASS |
| 11 | Puisi privat user lain → 404 (anti-enumerasi) | HIGH | PASS |
| 12 | SQL injection di search → tidak ada kebocoran error | HIGH | PASS |
| 13 | Injection tersimpan di judul → verbatim, tidak dieksekusi | MEDIUM | PASS |
| 14 | `/internal/*` dari luar → 404 | MEDIUM | PASS |
| 15 | `/metrics` via gateway → SPA HTML, bukan metrik | INFO | PASS (bukan kebocoran) |
| 16 | Body 5MB → 413 dalam 0.0s (`client_max_body_size 1m`) | MEDIUM | PASS |
| 17 | Rate limit gateway → 429 pada burst 80 | MEDIUM | PASS (41× 429) |

**Catatan**: key seperti `../../etc/passwd` diterima 201 — aman, karena key hidup di Redis
(bukan path filesystem) dan ter-scope per user.

---

## 4. Hasil stress test k6

Token & seed: user 99 dengan 1000 puisi. K6 dijalankan langsung ke service (tanpa gateway),
rate limit gateway dinaikkan sementara ke 5000r/s.

| Skenario | VU | Durasi | Throughput | p95 | SLO | Error |
|---|---|---|---|---|---|---|
| search (baseline) | 20 | 30s | 441 r/s | **86 ms** | <300 ms | 0% |
| submit-normal | 10 | 30s | 126 r/s | **111 ms** | <500 ms | 0.44%* |
| submit-duplikat (run 2, script diperbaiki) | 10 | 30s | 200 r/s | **96 ms** | <500 ms | **0.00%** |
| key-bentrok | 10 | 30s | 148 r/s | 174 ms | — | 409 konsisten 2230× |

**Skenario duplikat (bukti idempotency di bawah beban)**:
2176 iterasi × 3 request dengan key sama → 2176 puisi baru + 4352 replay, `id_puisi`
konsisten 2176/2176 (100%), nol duplikasi.

\* 17/3792 kegagalan level koneksi (k6 status 0, reset TCP sporadik) — bukan respons 5xx:
Prometheus menunjukkan nol 5xx dari poem selama 2 jam, dan counter breaker Redis
(`poem_cache_total{error,bypass}`) **nol** → dedup tidak pernah fail-open.

### Anomali yang diselidiki (systematic-debugging)

| Hipotesis | Bukti | Vonis |
|---|---|---|
| Breaker Redis terbuka → fail-open → duplikat | counter error/bypass = 0 | Tersangkal |
| INSERT ganda di DB | menunggu `COUNT(*)` (bagian 7) | ⏳ |
| Kegagalan koneksi TCP level-edge + salah hitung di script k6 | 18 kegagalan koneksi = 17 (normal) + 1 (dup); pola 3 error dup cocok persis; nol 5xx | ✅ Terbukti |

Tindak lanjut script: `loadtest/submit.js` kini memisahkan `edge_failures` dari kegagalan
idempotency; hasil ulang: 0 anomali.

---

## 5. Akar masalah infra yang diperbaiki (semua berbasis bukti log)

| # | Gejala | Akar masalah | Perbaikan |
|---|---|---|---|
| 1 | Prometheus exit 2: `open prometheus.yml: permission denied` | SELinux enforcing memblokir bind-mount membaca config host | flag `z` pada 7 bind-mount di `docker-compose.yml` |
| 2 | Autoheal exit 127: `autoheal: not found` | tag `willfarrell/autoheal:1.2.0` = build 2021 cacat (tag resmi hanya latest/1.1.0/v0.7.0) | pin per-digest build resmi terkini (amd64) |
| 3 | Autoheal exit 1: `unix socket is currently not available` | SELinux memblokir akses `docker.sock` dari container | `security_opt: [label:disable]` |
| 4 | Grafana gagal: port 3000 terpakai (`nawacakra-website-web-1`) | port hardcoded | env `GRAFANA_PORT` (default 3001 lokal) |
| 5 | auth/poem exit: `Access denied for user` | `db/init.sql` hardcode password dev ≠ `.env` acak | `.env` disamakan saat itu; **kini permanen**: `db/02-env-passwords.sh` menjalankan `ALTER USER` dari nilai `.env` saat volume MySQL baru dibuat |

Semua kelima masalah bersifat **pre-existing** (terletak saat stack pertama kali di-up penuh
sejak commit observability), bukan regresi dari fitur baru.

---

## 6. Bekas & tindak lanjut (tercatat di `tasks/todo.md`)

1. **[PENTING sebelum demo]** Kembalikan rate limit gateway ke default
   (`API_RATE_LIMIT=20r/s`, `API_BURST=40`) + `docker compose up -d gateway` agar check
   429 dapat didemokan.
2. **[Verifikasi menunggu]** `COUNT(*)` puisi "Uji Duplikat%" harus = 4164
   (1988 run pertama + 2176 run ulang) — bukti nol duplikasi di DB.
3. **[Infra — selesai]** Password user DB kini berasal dari `.env` lewat
   `db/02-env-passwords.sh` (dijalankan MySQL setelah `db/init.sql` pada volume baru).
   Untuk volume lama: `docker compose exec -T mysql sh /docker-entrypoint-initdb.d/02-env-passwords.sh`.
4. **[Opsional]** Reset koneksi sporadik (~18× di 14 ribu request) — tunjuk keep-alive /
   `--limit-concurrency` uvicorn bila muncul lagi di beban lebih tinggi.
5. **[Repo]** Semua perubahan belum di-commit (fitur + fix infra + script uji).
6. **[Keputusan pemilik]** `services/auth/tests/` tetap di-ignore (lokal saja), sesuai
   keputusan — perbaikan 3 test 422→400 hanya ada di mesin ini.

## 7. Perintah verifikasi tersisa

```bash
# Bukti nol duplikasi di DB (harapkan 4164)
sudo docker compose exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" \
  -e "SELECT COUNT(*) FROM poem_db.puisi WHERE judul LIKE \"Uji Duplikat%\";"'

# Poem tanpa exception selama seluruh uji
sudo docker compose logs poem --since 2h 2>&1 | grep -c "ERROR"

# (opsional, sebelum demo) bersihkan data uji
sudo docker compose exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" \
  -e "DELETE FROM poem_db.puisi_tag; DELETE FROM poem_db.puisi; DELETE FROM auth_db.users;"'
```
