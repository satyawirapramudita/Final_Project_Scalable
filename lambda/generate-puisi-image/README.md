# Lambda: `generate-puisi-image`

Microservice stateless Node.js (ESM) yang merender kutipan puisi ke atas gambar template statis menggunakan **Jimp v0.22.12** (bitmap font, tanpa dependency font sistem).

---

## Cara Kerja

```
CloudFront /generate-puisi* → Lambda Function URL
```

| Query Param | Wajib | Keterangan |
|-------------|-------|-----------|
| `template`  | ✅   | Nama file template di S3, contoh `template1.jpg` |
| `judul`     | ✅   | Judul puisi (max 150 karakter) |
| `penulis`   | ✅   | Nama penulis (dari session, dikirim frontend) |
| `bait`      | ✅   | Kutipan pendek (max 500 karakter) |
| `save`      | ❌   | `"true"` → simpan ke S3 + return JSON; absen/`"false"` → return JPEG base64 |

### Mode `save=false` (Preview)
- Tidak menulis apa pun ke S3.
- Return: `Content-Type: image/jpeg`, body base64-encoded.
- Dipakai frontend untuk `<img src="data:image/jpeg;base64,...">`

### Mode `save=true` (Submit Final)
- Upload hasil ke `s3://${S3_BUCKET}/hasil-puisi/puisi-{timestamp}-{random}.jpg`
- Return: `{ ok: true, filename: "hasil-puisi/xxxx.jpg" }`
- Frontend mengirimkan `filename` ini ke backend EC2 sebagai `gambar_file`.

---

## Setup Manual di AWS Console / CLI

### 1. Buat Lambda Function

```bash
# Buat zip deployment package (jalankan dari folder ini)
npm install
zip -r function.zip index.mjs package.json node_modules/

# Buat fungsi (ganti ACCOUNT_ID dan ROLE_ARN)
aws lambda create-function \
  --function-name generate-puisi-image \
  --runtime nodejs20.x \
  --handler index.handler \
  --zip-file fileb://function.zip \
  --role arn:aws:iam::ACCOUNT_ID:role/lambda-puisi-role \
  --memory-size 256 \
  --timeout 15 \
  --environment "Variables={S3_BUCKET=nama-bucket-kamu,S3_REGION=ap-southeast-1}"
```

### 2. Konfigurasi Wajib

| Parameter | Nilai |
|-----------|-------|
| Runtime   | Node.js 20.x |
| Memory    | **≥ 256 MB** (Jimp membutuhkan heap cukup untuk decode/encode JPEG) |
| Timeout   | **≥ 10 detik** (S3 GetObject + render + S3 PutObject) |
| Handler   | `index.handler` |

### 3. Environment Variables Lambda

| Variabel   | Contoh | Keterangan |
|------------|--------|-----------|
| `S3_BUCKET` | `puisi-static-assets` | Bucket S3 tempat template & hasil gambar |
| `S3_REGION` | `ap-southeast-1` | Region bucket |

### 4. IAM Execution Role

Buat/update role Lambda dengan policy berikut (inline policy atau attach):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "GetTemplate",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::puisi-static-assets/template*"
    },
    {
      "Sid": "PutHasil",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::puisi-static-assets/hasil-puisi/*"
    }
  ]
}
```

> **Catatan keamanan:** Scope izin S3 ke prefix spesifik (`template*` dan `hasil-puisi/*`), bukan `*`. Ini penting agar Lambda tidak bisa menimpa file statis lain di bucket.

### 5. Lambda Function URL

```bash
aws lambda create-function-url-config \
  --function-name generate-puisi-image \
  --auth-type NONE
  # Akses publik dikontrol dari CloudFront, bukan dari sini
  # Tambahkan CORS jika CloudFront tidak menjadi proxy:
  # --cors '{"AllowOrigins":["*"],"AllowMethods":["GET"]}'
```

### 6. Template Statis di S3

Upload minimal 3 file template ke root bucket (bukan prefix `hasil-puisi/`):

```bash
aws s3 cp template1.jpg s3://puisi-static-assets/template1.jpg
aws s3 cp template2.jpg s3://puisi-static-assets/template2.jpg
aws s3 cp template3.jpg s3://puisi-static-assets/template3.jpg
```

Dimensi yang disarankan: **1200×675 px** (rasio 16:9), format JPEG atau PNG.

---

## CloudFront Behavior

Tambah behavior baru di distribusi CloudFront yang sudah ada:

| Field | Nilai |
|-------|-------|
| Path pattern | `/generate-puisi*` |
| Origin | Lambda Function URL (buat origin baru) |
| Cache policy (preview, `save=false`) | `CacheWithQueryString` — aman, karena output deterministik untuk input yang sama |
| Cache policy (save, `save=true`) | **Disarankan: CacheDisabled** |

### Trade-off Cache Policy

- **CacheWithQueryString** untuk semua request: efisien untuk preview (gambar sama untuk parameter yang sama akan di-cache), **tapi berisiko** untuk `save=true` — jika URL yang sama di-cache, Lambda tidak dipanggil ulang dan gambar baru tidak diupload. Solusi: frontend harus menyertakan timestamp/nonce di URL saat `save=true`, atau gunakan policy terpisah.
- **CacheDisabled** khusus untuk `save=true`: paling aman, selalu memanggil Lambda. Overhead: setiap preview juga tidak di-cache jika menggunakan satu behavior untuk keduanya.
- **Solusi terbaik (yang diimplementasikan di frontend):** gunakan behavior berbeda atau tambahkan `&_t={Date.now()}` hanya saat `save=true` sehingga CloudFront tidak pernah melayaninya dari cache.

---

## Struktur Folder

```
lambda/generate-puisi-image/
├── index.mjs       ← handler utama (ESM)
├── package.json
└── README.md       ← file ini
```
