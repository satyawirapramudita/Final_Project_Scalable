# Tutorial Deploy AWS — dari nol sampai running

Panduan ini menghasilkan stack lengkap di AWS:

```
Browser ──HTTPS──▶ CloudFront ──┬── default  ──▶ S3 (frontend statis)
                                ├── /api/*   ──▶ EC2 (gateway nginx → 6 layanan + MySQL/Redis/RabbitMQ)
                                └── /fungsi* ──▶ Lambda (generator gambar) [opsional]
```

Perkiraan waktu: **±90 menit** (tanpa Lambda) / **±2 jam** (dengan Lambda).
Perkiraan biaya: **±$3–5 untuk 1–2 hari demo** dengan `t3.large`; ±$65/bulan bila dibiarkan menyala.

> **Memakai AWS Academy Sandbox (Learner Lab)?** Tutorial ini tidak berlaku di sana: sandbox menolak
> `t3.medium`/`t3.large` dan menghapus semuanya saat sesi habis. Pakai
> [`deploy/sandbox/README.md`](../deploy/sandbox/README.md) (3 × `t3.small`, satu skrip).
>
> Semua perintah dijalankan di **laptop** kecuali yang diberi tanda “(di EC2)”.
> Ganti nilai berhuruf kapital seperti `<BUCKET>`, `<EIP>`, `<CF-DOMAIN>` dengan milik Anda.

---

## 0. Yang perlu disiapkan

| Kebutuhan | Keterangan |
|---|---|
| Akun AWS | Region **us-east-1** (wajib sesuai dokumen tugas) |
| AWS CLI v2 | Opsional, tapi mempercepat (S3/CloudFront/Lambda) |
| Repo | `https://github.com/satyawirapramudita/Final_Project_Scalable.git` |
| Kredit | Pasang **AWS Budgets** dulu (langkah 1) |

---

## 1. Pasang budget alert (jangan dilewati)

Konsol: **Billing → Budgets → Create budget → Use a template → Monthly cost budget**

- Budget amount: **$20**
- Email: email Anda
- Ulangi untuk **$50** bila ingin peringatan kedua.

---

## 2. Buat secret produksi — **di server**, bukan di checkout dev

`deploy/gen-secrets.sh` dijalankan **di EC2 sesudah clone** (langkah 6). Skrip ini menolak
menimpa `.env` yang sudah ada — itu pengaman, bukan error:

- Di checkout **dev** (stack lokal sudah pernah `up`), `.env` berisi password yang cocok dengan
  **volume MySQL yang sudah ada**. Menimpanya membuat auth/poem/search gagal
  `Access denied for user` (dan `db/02-env-passwords.sh` tidak jalan pada volume lama).
- Di **EC2 clone baru**, `.env` belum ada, sehingga skrip berjalan normal.

Kalau Anda melihat pesan ini di laptop:

```
ERROR: .env sudah ada. Jalankan 'bash deploy/gen-secrets.sh --force' untuk menimpa.
```

**Jangan pakai `--force`.** Itu artinya Anda menjalankannya di checkout dev; lanjutkan saja
ke langkah 3. (Kalau `--force` benar-benar diperlukan, `.env` lama otomatis disimpan sebagai
`.env.bak.<timestamp>`.)

Yang dibuat skrip: `.env` (mode 600) dengan password acak untuk MySQL root, 5 user DB,
RabbitMQ, `JWT_SECRET` (48 karakter), dan Grafana.

> Password user DB **tidak perlu** disamakan manual dengan `db/init.sql`.
> File `db/02-env-passwords.sh` menjalankan `ALTER USER` memakai nilai dari `.env`
> saat volume MySQL pertama kali dibuat.

---

## 3. Key pair + Security Group

### 3.1 Key pair
Konsol: **EC2 → Key pairs → Create key pair** → nama `puisi-key` → tipe **RSA** → format **.pem** → simpan.

```bash
chmod 400 ~/Downloads/puisi-key.pem
```

### 3.2 Security Group
Konsol: **EC2 → Security Groups → Create security group** → nama `puisi-sg`, VPC default.

Inbound rules:

| Port | Source | Untuk |
|---|---|---|
| 22 (SSH) | **My IP** | akses admin |
| 80 (HTTP) | `0.0.0.0/0` | origin CloudFront, gateway-a (persempit setelah CF dibuat, lihat catatan) |
| 81 (HTTP) | prefix list CloudFront | origin kedua CloudFront, gateway-b (langkah 10.4b) |

Jangan membuka 3306, 6379, 5672, 15672, 9090, 3001.

> **Mempersempit ke CloudFront (opsional, disarankan setelah demo jalan):**
> ```bash
> aws ec2 describe-managed-prefix-lists \
>   --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
>   --query 'PrefixLists[0].PrefixListId' --output text
> ```
> Ganti rule port 80 menjadi source **Prefix list** tersebut.

---

## 4. Launch EC2

Konsol: **EC2 → Launch instance**

| Field | Nilai |
|---|---|
| Name | `puisi-app` |
| AMI | **Ubuntu Server 24.04 LTS** |
| Instance type | **t3.large** (8 GB) — `t3.medium` cukup untuk demo trafik rendah. Stack lengkap (2 gateway, 2 replika per layanan, klaster RabbitMQ 3 node) memakai ±2,2 GiB saat idle |
| Key pair | `puisi-key` |
| Network | VPC default, **Auto-assign public IP: Enable** |
| Security group | `puisi-sg` |
| Storage | **30 GiB gp3** |

**Advanced details → User data** — tempel isi `deploy/ec2-bootstrap.sh`:

```bash
cat deploy/ec2-bootstrap.sh
```

User data memasang Docker + Compose v2, menambah swap 4 GB, dan membatasi rotasi log Docker.

Tunggu ±2 menit, lalu:

```bash
ssh -i ~/Downloads/puisi-key.pem ubuntu@<EC2-PUBLIC-IP> \
  'tail -5 /var/log/cloud-init-output.log; docker --version; docker compose version'
```

Harus muncul `bootstrap selesai`, Docker, dan Compose v2 (≥ 2.24).

> Jika login SSH ditolak “permission denied (publickey)”, tunggu 1 menit lagi;
> cloud-init masih berjalan.

---

## 5. Elastic IP

Konsol: **EC2 → Elastic IPs → Allocate** → **Associate** ke `puisi-app`.
Catat `<EIP>`. Elastic IP gratis selama terpasang.

---

## 6. Deploy aplikasi (di EC2)

```bash
ssh -i ~/Downloads/puisi-key.pem ubuntu@<EIP>

git clone https://github.com/satyawirapramudita/Final_Project_Scalable.git
cd Final_Project_Scalable

# Clone baru belum punya .env. Kalau ada (mis. hasil `cp .env.example .env`):
#   mv .env .env.bak
bash deploy/gen-secrets.sh

nano .env
```

Yang perlu diubah di `.env` untuk demo:

```dotenv
# biarkan kosong dulu; diisi setelah CloudFront dibuat (langkah 10)
ASSET_BASE_URL=

# CloudFront meneruskan semua request dari sedikit IP edge, sehingga rate limit
# per-IP di gateway menjadi terlalu ketat. Untuk demo lewat CloudFront:
API_RATE_LIMIT=200r/s
API_BURST=400

# 1 worker per container sudah cukup untuk t3.large (2 vCPU)
SEARCH_WORKERS=1
```

Build & jalankan:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
```

Build pertama ±5–10 menit. Target akhir: **19 container** `Up`/`healthy`
(12 container aplikasi karena 2 replika + gateway + MySQL + Redis + RabbitMQ +
Prometheus + Grafana + nginx-exporter + autoheal).

---

## 7. Verifikasi

```bash
curl -s http://localhost/health            # {"status":"ok","service":"gateway"}
curl -s -o /dev/null -w '%{http_code}\n' http://<EIP>/health

# smoke test alur inti
BASE=http://<EIP>
curl -s -X POST $BASE/api/auth/register -H 'Content-Type: application/json' \
  -d '{"username":"demo1","nama":"Demo Satu","password":"Rahasia123"}'
TOKEN=$(curl -s -X POST $BASE/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"demo1","password":"Rahasia123"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')

curl -s -X POST $BASE/api/poems \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $(cat /proc/sys/kernel/random/uuid)" \
  -d '{"judul":"Demo Deploy","isi":"Baris puisi untuk uji deploy.","kategori":"alam"}' | python3 -m json.tool

curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/search?q=puisi" | python3 -m json.tool | head -20
```

Kalau `submit` mengembalikan `{"ok":true,"id_puisi":...}`, alur inti sudah hidup.

---

## 8. Observability (SSH tunnel)

Prometheus/Grafana sengaja hanya bind ke `127.0.0.1` di server.

```bash
ssh -i ~/Downloads/puisi-key.pem -N \
  -L 3001:127.0.0.1:3001 -L 9090:127.0.0.1:9090 -L 15672:127.0.0.1:15672 \
  ubuntu@<EIP>
```

- Grafana: <http://localhost:3001> (admin / `GRAFANA_ADMIN_PASSWORD` di `.env`)
- Prometheus: <http://localhost:9090>
- RabbitMQ UI: <http://localhost:15672> (user `puisi` / `RABBITMQ_PASSWORD`)

Cek cepat di Prometheus: `up` (semua target 1), `search_breaker_state`, `poem_outbox_pending`.

---

## 9. S3 untuk frontend

### 9.1 Buat bucket
Konsol: **S3 → Create bucket**

| Field | Nilai |
|---|---|
| Name | `puisi-scalable-<nama>-<acak>` (unik global) — catat sebagai `<BUCKET>` |
| Region | **us-east-1** |
| Block Public Access | **matikan** (bucket ini menyajikan file publik) |
| Bucket Versioning | bebas (disarankan Enable) |

### 9.2 Static website hosting
**Properties → Static website hosting → Enable**
- Index document: `index.html`
- Error document: `index.html`

### 9.3 Bucket policy (public read)
**Permissions → Bucket policy**:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "PublicReadGetObject",
    "Effect": "Allow",
    "Principal": "*",
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::<BUCKET>/*"
  }]
}
```

### 9.4 Upload frontend

```bash
cd Final_Project_Scalable
rm -rf /tmp/frontend-deploy && cp -r frontend /tmp/frontend-deploy

# (nanti, setelah CloudFront jadi) isi URL generator di salinan ini:
#   window.PUISI_GENERATOR_URL = "https://<CF-DOMAIN>";
# tanpa URL, fitur gambar otomatis nonaktif dan puisi tetap bisa dikirim.

aws s3 sync /tmp/frontend-deploy/ s3://<BUCKET>/
```

> **Jangan pakai `--delete`**: bucket yang sama juga menyimpan `template*.jpg`
> dan hasil gambar (`hasil-puisi/`).

Uji: `http://<BUCKET>.s3-website-us-east-1.amazonaws.com` harus menampilkan aplikasi
(tombol login berfungsi, tetapi panggilan API belum jalan sampai langkah 10).

---

## 10. CloudFront (klik-per-klik)

Konsol: **CloudFront → Create distribution**

### 10.1 Origin 1 — S3 (frontend)
- **Origin domain**: `<BUCKET>.s3-website-us-east-1.amazonaws.com`
- **Protocol**: **HTTP only**
- **Name**: `s3-frontend`

### 10.2 Origin 2 — EC2 (API)
- **Origin domain**: `<EIP>` (atau Public IPv4 DNS instance)
- **Protocol**: **HTTP only**, HTTP port **80**
- **Name**: `ec2-api`

### 10.3 Default cache behavior
- **Path pattern**: Default (`*`)
- **Origin**: `s3-frontend`
- **Viewer protocol policy**: **Redirect HTTP to HTTPS**
- **Allowed HTTP methods**: GET, HEAD
- **Cache policy**: `CachingOptimized`
- **Compress objects automatically**: Yes

### 10.4 Behavior `/api/*` (buat setelah distribusi dibuat)
**Behaviors → Create behavior**

| Field | Nilai |
|---|---|
| Path pattern | `/api/*` |
| Origin | `ec2-api` |
| Viewer protocol policy | Redirect HTTP to HTTPS |
| Allowed HTTP methods | GET, HEAD, OPTIONS, PUT, PATCH, POST, DELETE |
| Cache policy | **CachingDisabled** |
| Origin request policy | **AllViewerExceptHostHeader** |

### 10.4b Origin failover ke gateway-b (Hands-On 3, titik kritis 1)
EC2 menjalankan dua gateway identik: gateway-a di port 80 dan gateway-b di
port 81 (`docker-compose.prod.yml`). Buka port 81 di Security Group dengan
source prefix list CloudFront yang sama dengan port 80 (langkah 3.2).

1. **Origins → Create origin**: domain `<EIP>`, **HTTP only**, HTTP port **81**,
   name `ec2-api-b`.
2. Edit origin `ec2-api` (gateway-a): **Connection attempts** `1`,
   **Connection timeout** `3` detik. Default (3 × 10 dtk) membuat CloudFront
   menunggu 30 dtk sebelum pindah.
3. **Origins → Create origin group**: name `ec2-api-group`, origin
   `ec2-api` (primary) lalu `ec2-api-b` (secondary), failover criteria
   **500, 502, 503, 504**.
4. Edit behavior `/api/*`: ganti **Origin** menjadi `ec2-api-group`.

Uji: `docker stop puisi-gateway` di EC2, lalu
`curl -s -o /dev/null -w '%{http_code}\n' "https://<CF-DOMAIN>/api/search?q=a" -H "Authorization: Bearer $TOKEN"`
harus tetap 200. Jalankan lagi `docker start puisi-gateway` setelahnya.

> **Batasan CloudFront:** failover hanya berlaku untuk request **GET, HEAD, dan
> OPTIONS** ([dokumentasi AWS](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/high_availability_origin_failover.html)).
> POST/PUT/DELETE tetap dikirim ke gateway-a saja; bila gateway-a mati, request
> tulis gagal 502/504. Frontend mengulang request tulis puisi dengan
> Idempotency-Key yang sama, sehingga pengulangan setelah gateway-a pulih (autoheal
> me-restart container unhealthy) tidak membuat data ganda. Kedua gateway juga
> berada di satu EC2: ini melindungi dari kegagalan proses/container Nginx,
> bukan kegagalan instance.

### 10.5 (Opsional) Behavior `/fungsi*` untuk Lambda
Lihat langkah 11. Bila Lambda belum siap, lewati dulu.

### 10.6 Simpan & tunggu
Status distribusi berubah menjadi **Deployed** (±5 menit). Catat `<CF-DOMAIN>`
(contoh `d111abc222.cloudfront.net`).

### 10.7 Uji end-to-end
```bash
curl -s https://<CF-DOMAIN>/health
curl -s -o /dev/null -w '%{http_code}\n' https://<CF-DOMAIN>/          # 200 (index.html)
curl -s -H "Authorization: Bearer $TOKEN" "https://<CF-DOMAIN>/api/search?q=puisi"
```

---

## 11. Lambda generator gambar (opsional)

Bisa dilewati: aplikasi tetap berfungsi tanpa gambar.

### 11.1 IAM role
**IAM → Roles → Create role → AWS service → Lambda** → nama `puisi-lambda-role`.
Tambahkan inline policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "GetTemplate",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::<BUCKET>/template*"
    },
    {
      "Sid": "PutHasil",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::<BUCKET>/hasil-puisi/*"
    }
  ]
}
```

### 11.2 Build & upload
```bash
cd lambda/generate-puisi-image
npm install
zip -r function.zip index.mjs package.json node_modules
```
Konsol: **Lambda → Create function** → name `generate-puisi-image`,
runtime **Node.js 20.x**, architecture x86_64, role `puisi-lambda-role`.
Upload `function.zip`. Set:
- **Memory**: 512 MB (minimum 256)
- **Timeout**: 15 detik
- **Environment**: `S3_BUCKET=<BUCKET>`, `S3_REGION=us-east-1`

### 11.3 Function URL
**Configuration → Function URL → Create** → Auth type **NONE** → catat `<FN-URL>`.

> Auth NONE berarti siapa pun yang tahu URL bisa memanggilnya. Untuk tugas ini
> dapat diterima; untuk produksi gunakan CloudFront + OAC/IAM.

### 11.4 Template gambar
Upload 4 template (UI menyediakan pilihan 1–4), ukuran 1200×675:

```bash
aws s3 cp template1.jpg s3://<BUCKET>/template1.jpg
aws s3 cp template2.jpg s3://<BUCKET>/template2.jpg
aws s3 cp template3.jpg s3://<BUCKET>/template3.jpg
aws s3 cp template4.jpg s3://<BUCKET>/template4.jpg
```

Uji langsung:
```bash
curl -s -o /tmp/hasil.jpg -w '%{http_code} %{content_type}\n' \
  "<FN-URL>/fungsi?template=template1.jpg&judul=Demo&penulis=Tim&bait=Baris%20uji"
```

### 11.5 CloudFront behavior
Tambahkan origin `lambda-image` (domain `<FN-URL>` tanpa `https://`, protocol HTTPS only),
lalu behavior `/fungsi*` → origin `lambda-image`, cache policy **CachingDisabled**,
origin request policy **AllViewerExceptHostHeader**.

### 11.6 Aktifkan di frontend
```bash
rm -rf /tmp/frontend-deploy && cp -r frontend /tmp/frontend-deploy
python3 - <<'PY'
from pathlib import Path
cf = "https://<CF-DOMAIN>"
p = Path("/tmp/frontend-deploy/index.html")
t = p.read_text()
lama = 'window.PUISI_GENERATOR_URL = window.PUISI_GENERATOR_URL || "";'
baru = f'window.PUISI_GENERATOR_URL = "{cf}";'
assert lama in t, "placeholder tidak ditemukan"
p.write_text(t.replace(lama, baru))
PY
aws s3 sync /tmp/frontend-deploy/ s3://<BUCKET>/
aws cloudfront create-invalidation --distribution-id <DIST-ID> --paths '/*'
```

### 11.7 Sambungkan aset gambar (di EC2)
```bash
nano .env          # ASSET_BASE_URL=https://<CF-DOMAIN>
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate poem
```
Setelah ini, `gambar_url` pada respons puisi menunjuk ke CloudFront.

---

## 12. Uji beban dari laptop

Gateway di EC2 membatasi 20–200 r/s, jadi naikkan sementara untuk uji:

```bash
# (di EC2) naikkan rate limit lalu kembalikan setelah uji
sudo API_RATE_LIMIT=5000r/s API_BURST=1000 \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate gateway
```

```bash
# (di laptop) siapkan token & jalankan k6 ke CloudFront
BASE=https://<CF-DOMAIN>
TOKEN=<token-dari-langkah-7>
docker run --rm -i -e BASE_URL=$BASE -e TOKEN=$TOKEN \
  -e VUS=20 -e DURATION=30s grafana/k6 run - < loadtest/search.js
```

Perhatikan: lewat CloudFront, angka `cache hit` akan didominasi cache edge CloudFront —
untuk kapasitas asli layanan, ukur langsung ke `<EIP>` atau pakai hasil uji lokal
(rata-rata **2,1k r/s search @2 replika**, p95 36 ms pada 40 VU).

---

## 13. Operasional

### Backup harian
```bash
# (di EC2, sebagai ubuntu)
BACKUP_S3=s3://<BUCKET>/backups bash deploy/backup-mysql.sh
crontab -e
# 15 2 * * * cd /home/ubuntu/Final_Project_Scalable && BACKUP_S3=s3://<BUCKET>/backups bash deploy/backup-mysql.sh >> /var/log/puisi-backup.log 2>&1
```

### Update aplikasi
```bash
cd Final_Project_Scalable
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

### Hentikan saat tidak dipakai (hemat kredit)
```bash
# (di laptop)
aws ec2 stop-instances --instance-ids <INSTANCE-ID>
aws ec2 start-instances --instance-ids <INSTANCE-ID>
```
Data tetap ada di EBS. Elastic IP tetap terpasang.

### Mengganti password volume lama
Bila `.env` diubah setelah volume MySQL dibuat, jalankan ulang skrip ALTER USER:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T mysql \
  sh /docker-entrypoint-initdb.d/02-env-passwords.sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate auth poem wordcloud kbbi-linker search
```

---

## 14. Troubleshooting

| Gejala | Penyebab umum | Solusi |
|---|---|---|
| CloudFront 502/504 | SG tidak mengizinkan CloudFront, EC2 mati, gateway down | cek `curl http://<EIP>/health`, rule port 80 |
| CloudFront 403 pada `/` | bucket tidak publik / static hosting mati | ulangi 9.2 & 9.3 |
| Semua orang kena 429 | rate limit per edge CloudFront | naikkan `API_RATE_LIMIT` (langkah 6) atau persempit demo ke EIP |
| `Access denied for user` | volume MySQL lama, password `.env` berubah | jalankan bagian “Mengganti password volume lama” |
| Container `unhealthy` | RAM kurang / dependensi belum siap | `docker compose ... logs <svc> --tail=100`; pertimbangkan t3.large |
| `POST /api/poems` 400 | `judul`/`isi`/`kategori` kosong atau judul >150 | sesuaikan payload |
| `POST /api/auth/register` 400 | username <3/karakter aneh, password <6 | gunakan `demo1` / `Rahasia123` |
| Gambar 404 | `ASSET_BASE_URL` salah atau objek tidak publik | cek URL di browser, ulangi 11.7 |
| Lambda 400 “Parameter wajib” | salah satu `template/judul/penulis/bait` kosong | lengkapi query |
| Lambda 404 template | file `templateN.jpg` belum diunggah | ulangi 11.4 |
| Halaman lama masih tampil | cache CloudFront | `aws cloudfront create-invalidation --distribution-id <ID> --paths '/*'` |
| `docker compose` menolak `deploy.replicas` | Compose terlalu lama | `sudo apt install -y docker-compose-v2` |

---

## 15. Checklist sebelum demo

- [ ] Budget alert aktif ($20/$50)
- [ ] `https://<CF-DOMAIN>/` menampilkan aplikasi
- [ ] `/health` via CloudFront 200
- [ ] Register → login → submit → search → wordcloud → KBBI
- [ ] Submit dua kali dengan `Idempotency-Key` sama → `id_puisi` sama (`Idempotent-Replay: true`)
- [ ] Edit & hapus puisi berfungsi
- [ ] Grafana (tunnel) menampilkan trafik; `search_breaker_state` ada
- [ ] Backup manual berhasil
- [ ] (Opsional) generator gambar menampilkan preview
- [ ] Screenshot dashboard + hasil uji beban untuk laporan

---

## 16. Uji beban dan failover

Setelah aplikasi hidup, jalankan uji beban dan uji kegagalan memakai
`loadtest/aws_stress.js`. Panduan lengkap, termasuk skenario, perintah, metrik,
dan template hasil ada di [`docs/Stress-Test-AWS.md`](Stress-Test-AWS.md).

Untuk **stress test demo kelas** (di AWS Academy Sandbox: ramp, spike, pulih)
pakai `bash loadtest/kelas.sh`, panduannya di [`docs/Demo-Kelas-AWS.md`](Demo-Kelas-AWS.md).
