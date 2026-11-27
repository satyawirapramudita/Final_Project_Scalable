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

## Langkah (±40–60 menit, kebanyakan menunggu)

1. Halaman lab: **Start Lab**, tunggu lampu **AWS** hijau, klik **AWS** (console terbuka di `us-east-1`).
2. Buka **CloudShell** (ikon `>_` di bilah atas console).
3. Jalankan:
   ```bash
   git clone -b <branch> https://github.com/satyawirapramudita/Final_Project_Scalable
   cd Final_Project_Scalable && bash deploy/sandbox/deploy.sh buat
   ```
   `<branch>` = `main` bila sudah di-merge, atau nama branch fitur. Variabel `BRANCH` mengatur branch
   yang di-clone di server (bawaan `main`): `BRANCH=<branch> bash deploy/sandbox/deploy.sh buat`.
4. Di akhir, skrip mencetak alamat **CloudFront** (`https://dxxxx.cloudfront.net/`) dan IP ketiga server.

Yang dikerjakan `buat`, berurutan (masing-masing juga bisa dijalankan sendiri):

| Perintah | Isi | Waktu |
|---|---|---|
| `server` | Security Group (antaranggota grup: semua port; dari internet: 22, 80), 3 server dengan bootstrap otomatis | ±3 menit |
| `tunggu` | menunggu bootstrap lewat SSM: data (pull image), app-1/app-2 (build 6 image di 2 vCPU) | ±15–25 menit |
| `cdn` | bucket S3 + frontend, distribusi CloudFront dengan origin group | ±10–15 menit |
| `seed` | 1000 puisi contoh untuk pencarian (user 99) | ±1 menit |
| `uji` | smoke test ke app-1, app-2, dan CloudFront | detik |

Rahasia (password DB, JWT, cookie Erlang, password Grafana) dibuat acak per sesi di
`~/.poetify-sandbox/secrets.env` (CloudShell).

**Perpanjang sesi:** klik **Start Lab** lagi sebelum timer 0:00 (terutama saat uji beban), atau semua
sumber daya hilang.

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
- Jalankan k6 dari **laptop**, bukan dari server: server hanya 2 vCPU.
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

- Dataset KBBI tidak ikut (tidak ada di repo); tautan KBBI kosong.
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

**Status:** server, bootstrap, dan peran diuji secara lokal (peran data + aplikasi sebagai proyek
Compose terpisah yang terhubung lewat jaringan, uji ujung ke ujung 9/9 lulus). Deploy penuh di sandbox
belum dijalankan.
