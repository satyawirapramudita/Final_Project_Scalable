# Deployment — Platform Publikasi Karya Sastra Puisi

**Proyek:** Final Project Scalable (PACS262521)
**Basis:** Monolithic-Stateful-AWS-Deployment (Tugas 1) + Hands-on 1 (S3, CloudFront, Lambda)
**Region AWS:** `us-east-1` (US East / N. Virginia) — wajib, sesuai Hands-on 1
**Status:** dokumen deployment — dijalankan **setelah development fungsional selesai (F8)**

---

## 1. Prinsip

1. **Deployment setelah development fungsional selesai.** Coding & iterasi dilakukan lokal via Docker Compose (cepat, gratis); AWS dipakai untuk demo/produksi (F8).
2. **Incremental, bukan big-bang.** Validasi jalur AWS lebih awal (smoke test S3/CloudFront/Lambda), lalu deploy penuh di akhir.
3. **Stateless di cloud.** Container bisa dimatikan/digandakan tanpa kehilangan state (JWT + store eksternal).
4. **Rahasia tidak masuk repo.** Semua kredensial via `.env` (dev) / Secrets Manager (produksi).
5. **Dua jalur.** Jalur A (EC2 + Docker Compose) = realistis untuk tugas. Jalur B (full cloud) = evolusi.

---

## 2. Prasyarat

- Akun AWS, region **us-east-1**.
- IAM: `LabRole` untuk Lambda; kredensial untuk S3/CloudFront/EC2.
- Repo ter-clone di EC2; image dibuild di EC2 (atau ECR).
- Tools: Docker + Compose, AWS CLI (opsional), k6/Locust (uji beban).

---

## 3. Arsitektur Deployment

```
Browser
 ├─ Aset statis & gambar ──▶ CloudFront ──▶ S3
 ├─ /fungsi*  ────────────▶ CloudFront ──▶ Lambda Function URL (generator gambar)
 └─ /api/*    ────────────▶ CloudFront ──▶ EC2 (gateway Nginx)
                                              └─▶ 7 layanan + MySQL + Redis + RabbitMQ
```

- **S3 + CloudFront + Lambda** = komponen cloud-native (dari Tugas 1, dipertahankan).
- **EC2** = seluruh stack backend via Docker Compose.

---

## 4. Jalur A — EC2 + Docker Compose (utama)

### 4.1 Amazon S3 (aset statis + gambar)

1. Buat bucket unik (contoh: `puisi-scalable-<tim>-<kode>`), region **us-east-1**.
2. Matikan **Block all public access**.
3. **Bucket policy** (public read):
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Sid": "PublicReadGetObject",
       "Effect": "Allow",
       "Principal": "*",
       "Action": "s3:GetObject",
       "Resource": "arn:aws:s3:::<NAMA_BUCKET>/*"
     }]
   }
   ```
4. Aktifkan **Static website hosting** → Index: `index.html`, Error: `error.html`.
5. Unggah frontend + gambar template:
   ```bash
   aws s3 sync frontend/ s3://<NAMA_BUCKET>/ --delete
   ```
6. Siapkan prefix untuk gambar puisi (`hasil-puisi/`), PNG word cloud, dan audio TTS.

### 4.2 AWS Lambda (generator gambar puisi)

1. Sumber ada di `lambda/generate-puisi-image/` (Node.js + Jimp).
2. Build zip:
   ```powershell
   Compress-Archive -Path index.mjs, package.json, node_modules -DestinationPath function.zip -Force
   ```
3. Buat fungsi: runtime **Node.js 24.x**, role **LabRole**, Memory ≥ 512 MB, Timeout ≥ 10 s.
4. Upload `function.zip`.
5. Aktifkan **Function URL** (Auth type: `NONE`). Salin URL.

### 4.3 Amazon CloudFront (CDN + multi-origin)

1. Buat distribution.
2. **Origin 1 — S3** (default behavior `*`):
   - Origin domain = S3 website endpoint (tanpa `http://`, tanpa `/`).
   - Protocol: HTTP only, port 80.
   - Viewer protocol policy: Redirect HTTP to HTTPS.
   - Cache policy: `CachingOptimized`.
3. **Origin 2 — Lambda** (behavior `/fungsi*`):
   - Origin domain = Function URL (tanpa `https://`, tanpa `/`).
   - Protocol: HTTPS only.
   - Cache policy: **CacheWithQueryString** (agar `?text=` tidak dipotong).
   - Origin request policy: **AllViewerExceptHostHeader** (wajib, mencegah 403).
4. **Origin 3 — EC2 gateway** (behavior `/api/*`):
   - Origin domain = Elastic IP / DNS EC2.
   - Protocol: HTTP only (EC2 di belakang SG).
   - Forward header `X-Request-ID`; cache dinonaktifkan (`CachingDisabled`) untuk `/api/*`.
5. Default root object: `index.html`.

### 4.4 Amazon EC2 (stack backend)

1. Launch instance **t3.small (2 GB) minimum, t3.medium (4 GB) disarankan**.
2. Security Group inbound: **22** (SSH), **80/443** (via CloudFront/ALB). MySQL/Redis/RabbitMQ **tidak** dibuka.
3. Tambah swap 2–4 GB:
   ```bash
   sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
   sudo mkswap /swapfile && sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```
4. Instal Docker & Compose plugin, clone repo, isi `.env`.
5. Jalankan:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
   ```
6. Pasang Elastic IP agar domain stabil.

### 4.5 Konfigurasi & Secret

- `.env` di EC2 (tidak di-commit): kredensial MySQL, JWT secret, RabbitMQ, Redis.
- `ASSET_BASE_URL` = domain CloudFront (untuk `gambar_url`).
- Produksi: pindahkan secret ke **AWS Secrets Manager** / **SSM Parameter Store**.

### 4.6 Verifikasi

| Uji | Target |
|---|---|
| Akses S3 langsung | `http://<bucket>.s3-website.us-east-1.amazonaws.com` |
| Via CloudFront | `https://<distribution>.cloudfront.net/index.html` |
| Generator gambar | `https://<distribution>.cloudfront.net/fungsi?text=Halo` |
| API gateway | `https://<distribution>.cloudfront.net/api/...` |
| Health tiap layanan | `/health` semua container hijau |
| Edge cache | `curl -I` → `X-Cache: Miss` lalu `Hit` |

---

## 5. Jalur B — Full Cloud AWS (evolusi)

Pemetaan lengkap ada di dokumen rencana §13. Ringkasnya:

| Komponen | Layanan AWS |
|---|---|
| Gateway | ALB (+ WAF) |
| 7 layanan + worker | ECS Fargate |
| MySQL | RDS for MySQL / Aurora |
| Redis | ElastiCache for Redis |
| RabbitMQ | Amazon MQ for RabbitMQ |
| Statis + gambar | S3 + CloudFront |
| Generator gambar | Lambda |
| Secret | Secrets Manager |
| Observability | CloudWatch + Amazon Managed Prometheus/Grafana |
| Registry/CI-CD | ECR + GitHub Actions → ECS |
| DNS/TLS | Route 53 + ACM |

---

## 6. Observability

- **Prometheus** scrape `/metrics` semua layanan; **Grafana** dashboard (latensi, error, queue depth, cache hit).
- Log **JSON** ke stdout container → CloudWatch Logs (atau Loki).
- `correlation_id` (dari `X-Request-ID` gateway) untuk pelacakan lintas layanan.
- RabbitMQ management untuk queue depth & DLQ.
- Alarm dasar: DLQ ≠ 0, queue naik, error rate tinggi, container restart berulang.

---

## 7. Backup, Recovery, dan Rollback

- **Backup:** snapshot EBS + `mysqldump` harian (Jalur A); RDS automated backup + AWS Backup (Jalur B).
- **Recovery:** restore snapshot / dump; volume Docker bernama (`mysql_data`, `redis_data`, `rabbitmq_data`).
- **Rollback:** simpan image sebelumnya; `docker compose up -d --build` dengan tag versi lama; CloudFront invalidation untuk aset statis.

---

## 8. Checklist Go-Live

- [ ] Bucket S3 + policy + static hosting selesai
- [ ] Lambda ter-deploy + Function URL aktif
- [ ] CloudFront multi-origin (`*`, `/fungsi*`, `/api/*`) selesai
- [ ] EC2: Security Group, swap, Docker, `.env`, `compose up` hijau
- [ ] Health check semua layanan lolos
- [ ] Uji beban (k6/Locust) sesuai SLO
- [ ] Backup terjadwal aktif
- [ ] Alarm & dashboard Grafana berjalan
- [ ] `.env`/secret tidak terekspos; repo bersih dari kredensial
- [ ] Panel Grafana & hasil uji disimpan sebagai bukti laporan

---

## 9. Estimasi Biaya (indikatif)

| Item | Perkiraan |
|---|---|
| EC2 t3.medium (jam) | ± $0,04/jam (stop saat tidak dipakai) |
| S3 + CloudFront | Sangat rendah (skala demo) |
| Lambda | Sesuai pemakaian (biasanya gratis-tier) |
| EBS | ± $0,10/GB/bulan |
| Jalur B (RDS/MQ/ElastiCache/ECS) | Jauh lebih tinggi — hanya untuk produksi |

---

## 10. Rujukan

1. Hands-on 1 — Implementasi Aplikasi Scalable dengan Amazon S3, CloudFront, dan AWS Lambda (us-east-1).
2. `docs/event-contracts.md` — kontrak event.
3. `Rencana-Pengembangan-Final-Project-Scalable.md` §12 (EC2) & §13 (full cloud).
4. Repo Tugas 1 — `lambda/`, `lambda-canvas-cdn/`.
