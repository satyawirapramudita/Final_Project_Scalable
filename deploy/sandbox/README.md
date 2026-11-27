# Deploy ke AWS Academy Sandbox (Learner Lab)

Satu skrip membuat seluruh Poetify di sandbox: **3 server `t3.small` + S3 + CloudFront**.

```
Browser ─HTTPS─▶ CloudFront ─┬─ /*      ─▶ S3 (frontend)
                             └─ /api/*  ─▶ origin group: app-1, pindah ke app-2 bila 5xx/gagal tersambung
                                              │                        │
                              app-1: gateway-a + 1 replika     app-2: gateway-b + 1 replika
                                     + Prometheus/Grafana              tiap layanan
                                              └──────── IP privat ─────┘
                                                          │
                                   data: MySQL, Redis, RabbitMQ 3 node (5672/5673/5674)
```

## Kenapa bentuknya begini

Aturan sandbox (Readme lab, diverifikasi 27 Nov 2026):

| Aturan | Akibat |
|---|---|
| EC2 hanya `t2`/`t3` sampai `small` (2 GiB). `t3.medium`/`t3.large` ditolak `UnauthorizedOperation`, **juga lewat Elastic Beanstalk** walau Readme menyebut `large` | Stack (±2,2 GiB) tidak muat di satu server → dipecah 3 |
| Saat timer sesi 0:00, **semua sumber daya dihapus permanen** | Deploy harus bisa diulang dari nol tiap sesi → satu skrip |
| Maksimal 9 instance, region `us-east-1`/`us-west-2` | 3 server cukup |
| Tidak bisa membuat IAM role; tersedia `LabRole`/`LabInstanceProfile` dan key `vockey` | Server memakai `LabInstanceProfile` (untuk SSM) dan `vockey` |

Dua server aplikasi memberi dua replika per layanan dan menaruh gateway-a dan gateway-b di **mesin
berbeda**, jadi failover CloudFront melindungi dari matinya satu server. Server data adalah titik
kegagalan tunggal (MySQL memang satu server); RabbitMQ 3 node di server itu melindungi dari crash node,
bukan dari matinya server data.

## Panduan cepat (bisa diikuti tanpa mengenal proyek)

Yang dibutuhkan: akun AWS Academy sendiri dengan lab **Sandbox**, dan browser. Tidak perlu memasang
apa pun, dan tidak perlu kredensial orang lain. Siapkan ±30 menit sebelum dipakai.

1. Buka halaman lab, klik **Start Lab**, tunggu lampu **AWS** hijau, lalu klik **AWS**
   (console terbuka; pastikan region `us-east-1`). Lihat sisa waktu di timer.
2. Buka **CloudShell** (ikon `>_` di bilah atas console).
3. Tempel perintah ini:
   ```bash
   rm -rf ~/.poetify-sandbox Final_Project_Scalable   # sisa sesi lama (hanya di awal sesi baru)
   git clone -b feat/sandbox-deploy https://github.com/satyawirapramudita/Final_Project_Scalable
   cd Final_Project_Scalable
   BRANCH=feat/sandbox-deploy bash deploy/sandbox/deploy.sh buat
   ```
4. Biarkan tab terbuka sampai selesai. Pesan `mode group ditolak ... InvalidArgument` **normal**:
   skrip lanjut sendiri memakai mode `split`.
5. Berhasil bila bagian `Smoke test` menampilkan `200` untuk app-1, app-2, dan CloudFront `/`, serta
   `401` untuk dua baris `/api/...`. Alamat aplikasi tercetak di baris `CloudFront : https://....cloudfront.net/`
   (alamatnya baru setiap kali deploy). Tampilkan lagi kapan saja: `bash deploy/sandbox/deploy.sh info`.
6. Buka alamat itu, daftar akun, kirim satu puisi, lalu cari di halaman pencarian. Puisi contoh berisi
   kata acak dan tanpa gambar (fitur gambar mati di sandbox); itu normal.
7. Kamus (KBBI) diimpor di latar belakang: `bash deploy/sandbox/deploy.sh kbbi-status` sampai muncul
   `SELESAI`.

**Bila terputus di tengah jalan** (CloudShell tertutup atau error): **jangan** jalankan `buat` lagi,
karena akan membuat 3 server tambahan. Masuk ke folder proyek, lalu jalankan langkah yang belum
selesai satu per satu, selalu dengan `BRANCH=feat/sandbox-deploy`:

```bash
cd ~/Final_Project_Scalable && export BRANCH=feat/sandbox-deploy
bash deploy/sandbox/deploy.sh tunggu     # lalu: kbbi, cdn, seed, uji, info
```

Bila gagal di langkah `server` (server belum lengkap): `bash deploy/sandbox/deploy.sh hapus`, lalu ulangi
dari langkah 3.

Variabel `BRANCH` menentukan branch yang di-clone **di server** (bawaan `main`). Setelah branch ini
di-merge ke `main`, `-b ...` dan `BRANCH=...` tidak diperlukan lagi.

**Perpanjang sesi:** klik **Start Lab** lagi sebelum timer 0:00 (terutama saat uji beban), atau semua
sumber daya hilang. Rahasia (password DB, JWT, cookie Erlang, password Grafana) dibuat acak per sesi di
`~/.poetify-sandbox/secrets.env`.

Yang dikerjakan `buat`, berurutan (masing-masing juga bisa dijalankan sendiri):

| Perintah | Isi | Waktu |
|---|---|---|
| `server` | Security Group (antaranggota grup: semua port; dari internet: 22, 80), 3 server dengan bootstrap otomatis | ±3 menit |
| `tunggu` | menunggu bootstrap lewat SSM: data (pull image), app-1/app-2 (build 6 image di 2 vCPU) | ±3 menit (teramati) |
| `kbbi` | app-1 mengunduh dataset KBBI V (21 MB, [GitHub, commit dipaku](https://github.com/aryakdaniswara/kbbi-dataset-kbbi-v)) dan meringkasnya (kata tunggal, 1 definisi per kata), lalu mengimpornya ke `kbbi_db` (versi `kbbi-v-27b7dc6-ringkas`) **di latar belakang**; aplikasi sudah bisa dipakai. Pantau: `kbbi-status`. Dataset utuh: `KBBI_UTUH=1` | ringkas ±1,5 menit (teramati); utuh belum diukur |
| `cdn` | bucket S3 + frontend, distribusi CloudFront dengan origin group | beberapa menit (bisa sampai 15) |
| `seed` | 1000 puisi contoh (user 99) lewat jalur submit asli, jadi setiap hasil pencarian bisa dibuka; selesai setelah semua dianalisis dan antrean kosong. Jumlah lain: `SEED_COUNT=200`. Cadangan bila gagal: `seed-palsu` (hanya indeks search, hasil tidak bisa dibuka) | ±1 menit (teramati) |
| `uji` | smoke test ke app-1, app-2, dan CloudFront | detik |

## Perintah lain

```bash
bash deploy/sandbox/deploy.sh info                         # alamat, IP publik/privat
bash deploy/sandbox/deploy.sh jalankan app-1 'docker ps'   # perintah di server (data | app-1 | app-2) lewat SSM
bash deploy/sandbox/deploy.sh jalankan app-1 'tail -50 /var/log/poetify-bootstrap.log'
bash deploy/sandbox/deploy.sh hapus                        # hapus CloudFront, server, bucket, SG (±15 menit)
```

Grafana/Prometheus (di app-1, port hanya `127.0.0.1`): unduh `labsuser.pem` dari **AWS Details**, lalu
`ssh -i labsuser.pem -L 3000:localhost:3000 -L 9090:localhost:9090 ec2-user@<IP publik app-1>` dan buka
http://localhost:3000.

## Uji beban dan demo failover

- **Target:** alamat CloudFront (perilaku nyata pengguna) atau langsung `http://<IP publik app-1>`
  (tanpa CloudFront). Rate limit gateway sudah dinaikkan (`API_RATE_LIMIT=500r/s`) karena CloudFront
  meneruskan dari sedikit IP edge.
- Jalankan k6 dari **laptop**, bukan dari server: server hanya 2 vCPU. Perlu
  [k6](https://grafana.com/docs/k6/latest/set-up/install-k6/) dan clone repo ini di laptop.
- Pencarian butuh token. Buat di CloudShell (token user 99, pemilik 1000 puisi contoh):
  ```bash
  bash deploy/sandbox/deploy.sh jalankan app-1 'cd /opt/poetify && docker compose exec -T poem python - 99 < scripts/mint_token.py'
  ```
  Lalu di laptop, dari folder repo:
  ```bash
  k6 run -e BASE_URL=https://<alamat>.cloudfront.net -e TOKEN=<token> loadtest/search.js
  ```
- **Matikan gateway-a:** `bash deploy/sandbox/deploy.sh jalankan app-1 'docker stop puisi-gateway'`
  → request GET lewat CloudFront tetap 200 (dilayani app-2). Pulihkan dengan `docker start puisi-gateway`.
- **Matikan satu node RabbitMQ:** `bash deploy/sandbox/deploy.sh jalankan data 'docker kill puisi-rabbit-1'`
  → puisi baru tetap dianalisis. Pulihkan dengan `docker start puisi-rabbit-1`.
- **Matikan server app-2** (EC2 → Stop) → aplikasi tetap jalan lewat app-1.

Failover origin group CloudFront hanya untuk GET, HEAD, OPTIONS. Bila CloudFront menolak origin group
pada behavior yang mengizinkan POST, skrip otomatis memakai mode `split`: jalur baca
(`/api/search*`, `/api/wordcloud/*`, `/api/kbbi/*`) lewat origin group, sisanya ke app-1. Mode yang
dipakai tercetak di `info`.

## Batasan

- Dataset KBBI tidak disimpan di repo; langkah `kbbi` mengunduhnya langsung ke server saat deploy.
- Secara bawaan KBBI diringkas: ±71 ribu kata, hanya definisi pertama; frasa, peribahasa, dan kata
  ulang tidak ada. Untuk dataset utuh: `KBBI_UTUH=1 bash deploy/sandbox/deploy.sh buat`.
- Lambda generator gambar tidak dibuat; fitur gambar otomatis mati, puisi tetap bisa dikirim.
- Angka uji beban dari server 2 vCPU tidak sebanding dengan hasil lokal (16 thread) di `docs/slo.md`.

## Pemecahan masalah

| Gejala | Periksa |
|---|---|
| `tunggu` lama di satu server | `jalankan <server> 'tail -50 /var/log/poetify-bootstrap.log'` |
| app-1/app-2 tidak sehat | `jalankan app-1 'cd /opt/poetify && docker compose ps && docker compose logs --tail 30 poem'` |
| Layanan tidak terhubung ke data | port 3306/5672–5674/6379 di data: `jalankan data 'docker ps'`; Security Group mengizinkan antaranggota grup |
| CloudFront 502/504 | `curl http://<IP app-1>/health`; origin memakai DNS publik server (berubah bila server di-Stop lalu Start) |
| Sesi habis di tengah jalan | semua terhapus; mulai lagi dari langkah 1 (`~/.poetify-sandbox` di CloudShell boleh dihapus) |

**Status:** deploy penuh dijalankan di sandbox pada 27 Nov 2026 **dari laptop (Git Bash)**, selesai
±10 menit: semua container sehat, uji ujung ke ujung 9/9 lulus lewat CloudFront dan app-2, pencarian
tetap sampai ke layanan saat gateway-a dimatikan, dan KBBI ringkas (71.737 entri) selesai diimpor.
Seed 1000 puisi lewat jalur submit asli
dikirim dan dianalisis dalam 33 detik, tanpa pesan di DLQ. Jalur **CloudShell** pada panduan cepat belum dicoba. Uji beban k6 belum dijalankan di sandbox.
