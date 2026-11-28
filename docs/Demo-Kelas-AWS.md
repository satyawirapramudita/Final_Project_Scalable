# Demo Kelas — Stress Test di AWS (7,5 menit beban, <10 menit total)

Stress test di sini berarti sengaja "menyerang" sistem sendiri dengan beban terukur, untuk melihat
**kapan mulai kewalahan, berapa banyak pengguna yang sanggup ditampung, dan berapa lama pulih**.
Ditutup dengan satu kegagalan: satu dari dua replika layanan poem dimatikan saat beban berjalan.

Target SLO: [`docs/slo.md`](slo.md). Uji lain: [`docs/Stress-Test-AWS.md`](Stress-Test-AWS.md).

| Bagian | Isi |
|---|---|
| [A](#a-sekali-saja-h-1) | Pasang alat (sekali saja, H-1) |
| [B](#b-hari-demo-tanpa-ai) | **Hari demo tanpa AI** (jalur utama) |
| [C](#c-alternatif-dengan-asisten-ai) | Alternatif dengan asisten AI |
| 1–6 | Isi uji, cara membaca hasil, kegagalan, angka gladi |

---

## A. Sekali saja (H-1)

Untuk laptop Windows. Buka **PowerShell**, jalankan satu per satu:

```powershell
winget install -e --id Git.Git
winget install -e --id Python.Python.3.13
winget install -e --id Amazon.AWSCLI
winget install -e --id GrafanaLabs.k6
```

Tutup PowerShell, buka **Git Bash** (dari Start menu), lalu pastikan keempatnya menjawab:

```bash
git --version; python --version; aws --version; k6 version
```

- `python: command not found` atau diarahkan ke Microsoft Store: pasang Python dari python.org dan
  centang **Add python.exe to PATH**.
- `k6` atau `aws` tidak ditemukan: tutup semua jendela Git Bash, buka lagi.

Lalu ambil kode:

```bash
cd ~
git clone https://github.com/satyawirapramudita/Final_Project_Scalable
```

Semua perintah di bawah dijalankan di **Git Bash**, dari satu laptop yang sama.

---

## B. Hari demo (tanpa AI)

Mulai **minimal 40 menit** sebelum giliran: deploy ±11 menit, kamus ±2 menit, uji <10 menit, sisanya
cadangan. Sesi sandbox baru = server baru, jadi langkah B1–B5 diulang tiap sesi.

### B1. Nyalakan lab dan salin kredensial

1. Halaman lab: **Start Lab**, tunggu lampu **AWS** hijau. Lihat timer: butuh lebih dari 1 jam.
2. Klik **AWS Details** → **AWS CLI: Show** → salin seluruh isinya.
3. Di Git Bash:
   ```bash
   mkdir -p ~/.aws && cat > ~/.aws/credentials
   ```
   Tempel (klik kanan → Paste), tekan **Enter**, lalu **Ctrl+D**.
4. Periksa:
   ```bash
   export AWS_DEFAULT_REGION=us-east-1
   aws sts get-caller-identity
   ```
   Harus menampilkan nomor akun, bukan error.

Kredensial itu memberi akses ke akun lab-mu: jangan dikirim di chat dan jangan di-commit.

### B2. Siapkan repo

```bash
cd ~/Final_Project_Scalable
git pull
rm -rf ~/.poetify-sandbox     # HANYA bila sesi lab ini baru (server sesi lama sudah terhapus)
```

### B3. Deploy (±11 menit)

```bash
bash deploy/sandbox/deploy.sh buat
```

- Pesan `mode group ditolak ... InvalidArgument` **normal**.
- Berhasil bila `Smoke test` menampilkan `200` (health dan CloudFront `/`) dan `401` (dua baris `/api`).
- Catat alamat `CloudFront : https://....cloudfront.net/`.
- Terputus di tengah jalan: **jangan** ulangi `buat` (server jadi dobel). Ikuti "Bila terputus" di
  [`deploy/sandbox/README.md`](../deploy/sandbox/README.md).

### B4. Tunggu kamus dan cek aplikasi

```bash
bash deploy/sandbox/deploy.sh kbbi-status      # ulangi sampai muncul SELESAI
```

Buka alamat CloudFront: daftar akun, kirim satu puisi, cari "senja", klik satu hasil.

### B5. (Opsional) Grafana di layar

Unduh `labsuser.pem` dari **AWS Details**, lalu di jendela Git Bash **kedua**:

```bash
cd ~/Final_Project_Scalable
bash deploy/sandbox/deploy.sh info                 # lihat IP publik app-1
ssh -i ~/Downloads/labsuser.pem -L 3000:localhost:3000 ec2-user@<IP publik app-1>
grep GRAFANA ~/.poetify-sandbox/secrets.env        # password (jalankan di jendela lain)
```

Buka http://localhost:3000, login `admin`. Lewati saja bila tidak lancar; uji tidak membutuhkannya.
(Grafana di sandbox belum pernah dicoba saat gladi.)

### B6. Jalankan stress test (giliranmu, <10 menit)

```bash
cd ~/Final_Project_Scalable
bash loadtest/kelas.sh
```

Hanya itu. Skrip menyiapkan token, menaikkan rate limit, menjalankan 2 replika poem di app-1,
menjalankan semua fase, mematikan dan menyalakan satu replika, menunggu antrean terkuras, mencetak ringkasan, lalu mengembalikan pengaturan.

- ±2 menit pertama hanya persiapan, dan seluruh skrip ±9 menit 55 detik. Jalankan sebelum giliran.
- Layar k6 diam selama beban berjalan; jelaskan fase memakai tabel di bagian 1. Baris `INJEKSI` dan
  `PULIHKAN` muncul sendiri.
- **Jalankan sekali saja per deploy.** Sisa data uji membuat run berikutnya lebih lambat.
- Jangan unduh atau streaming apa pun di laptop: beban dikirim dari laptop ini.

Hasil tersimpan di `loadtest/results/kelas-<waktu>/` (`ringkasan.txt` untuk ditunjukkan).

### B7. Setelah demo

1. Salin folder `loadtest/results/kelas-<waktu>/` dan screenshot Grafana **sebelum** sesi habis.
2. Hemat kredit: `bash deploy/sandbox/deploy.sh hapus` (±5 menit), atau akhiri sesi lab.

### Rekaman cadangan

Saat gladi, rekam layar selama B6 (Windows: **Win+Shift+R**, atau OBS) dan simpan videonya. Putar bila
deploy atau jaringan bermasalah di kelas, dan katakan bahwa itu rekaman gladi.

---

## C. Alternatif: dengan asisten AI

Butuh asisten yang **bisa menjalankan perintah di laptop** (mis. Claude Code), bukan chatbot di
browser. Alat di bagian A tetap diperlukan. Buka folder repo di asisten, **Start Lab**, lalu kirim:

> Ini kredensial AWS Sandbox saya: (tempel dari AWS Details → AWS CLI).
> Baca docs/Demo-Kelas-AWS.md dan deploy/sandbox/README.md. Deploy Poetify (jangan buat server
> ganda), tunggu KBBI selesai, beri tahu alamat CloudFront, lalu tunggu perintah saya sebelum
> menjalankan loadtest/kelas.sh. Setelah uji, jelaskan hasilnya.

Berikan kredensial hanya ke alat yang kamu percaya dan pastikan aturan kelas mengizinkan. Tetap
pelajari bagian B: bila asistennya gagal atau internetnya bermasalah, kamu yang menjalankan.

---

## 1. Fase uji

Beban: 50% search, 20% daftar puisi, 10% wordcloud, 10% kbbi, **10% submit puisi** (submit melewati
RabbitMQ ke nlp-worker, wordcloud, kbbi-linker, dan search).

| Waktu beban | Fase | Beban (VU) | Yang dijawab |
|---|---|---|---|
| 0:00–0:30 | `baseline` | 10 | Kondisi normal |
| 0:30–3:50 | `ramp-20` … `ramp-320` | 20, 40, 80, 160, 320 (±40 dtk per tahap) | Kapan mulai kewalahan, berapa yang sanggup |
| 3:50–4:10 | `jeda` | 10 | |
| 4:10–5:20 | `spike` | melonjak ke 300 | Tahan lonjakan mendadak? |
| 5:20–6:00 | `pulih` | 10 | Berapa lama pulih |
| 6:00–7:30 | `gagal` | 20; 1 dari 2 replika poem di app-1 mati ±45 dtk mulai ±6:20 | Tahan kehilangan satu replika? |

- **VU (virtual user)** mengirim request terus-menerus tanpa jeda, jauh lebih agresif daripada orang
  sungguhan; jangan disebut "320 pengguna asli".
- VU bergantian memakai **50 identitas uji** (token dibuat langsung, bukan akun terdaftar), jadi login
  dan registrasi tidak ikut diuji. Puisi contoh milik user 99.
- Tidak diuji: login, ubah/hapus puisi, matinya node RabbitMQ atau gateway.
- Rate limit gateway app-1 dinaikkan selama uji; tanpa itu yang terukur adalah limiter.
- Poem di app-1 dijalankan **2 replika** selama uji (deploy bawaan: 1); sebutkan ini saat demo.

---

## 2. Cara membaca hasil

- **Mulai kewalahan:** tahap ramp pertama dengan p95 > 1000 ms atau error > 1%. Ambangnya bukan SLO
  300 ms karena k6 di laptop ikut mengukur perjalanan ke `us-east-1`; sebutkan itu saat demo.
- **Pulih dari spike:** sejak fase `pulih` sampai p95 dan error kembali di bawah ambang. Angka ±10
  detik berarti pulih begitu beban turun (penurunannya sendiri 10 detik).
- **Satu replika poem dimatikan:** yang diharapkan 0 request gagal, karena replika kedua melayani.
  Bila ada yang gagal, sebutkan jumlahnya. Ini bukan bukti bahwa sistem tahan bila **semua** replika
  poem mati: daftar/submit puisi, wordcloud, dan kbbi bergantung pada poem (`auth_request`).
- **Integritas:** semua puisi yang tersimpan sudah `done`, outbox 0, antrean 0, DLQ 0.
- Bila CPU **kedua** server (app-1 dan data) rendah tetapi throughput mentok, yang mentok laptop atau
  jaringannya.
- **Lambat di 160 VU ke atas itu wajar** (sudah melewati titik jenuh). Endpoint yang paling lambat bisa
  berbeda antar-run: search, atau daftar/submit puisi. Jawaban singkat bila ditanya: "Server database
  hanya 2 vCPU dan dipakai bersama Redis dan RabbitMQ, jadi request mengantre: melambat, bukan gagal,
  dan pulih saat beban turun."

**Satu kalimat kesimpulan demo** (isi dengan angka run-mu sendiri):

> "Sistem masih sehat sampai … VU (… req/s) dan mulai kewalahan di … VU; setelah lonjakan 300 VU
> pulih dalam … detik. Saat satu dari dua replika poem dimatikan, … request gagal, dan tidak ada puisi
> yang hilang."

---

## 3. Angka gladi (28 Nov 2026, deploy baru)

| | Hasil |
|---|---|
| Masih sehat | 80 VU |
| Mulai kewalahan | 160 VU |
| Throughput tertinggi | 222 req/s |
| Pulih dari spike | ±15 s |
| 1 dari 2 replika poem mati: request gagal | 0 dari 5230 |
| Data tertinggal | tidak ada |
| Total waktu skrip | ±9 menit 55 detik |

Bottleneck: CPU server data (MySQL, Redis, dan RabbitMQ berbagi 2 vCPU); pada gladi ini yang paling
lambat search. Rincian: [`Laporan-Stress-Test-Sandbox.md`](Laporan-Stress-Test-Sandbox.md).
Satu kali uji; harapkan angka yang sedikit berbeda.

---

## 4. Jika ada yang tidak sesuai rencana

| Kejadian | Reaksi cepat |
|---|---|
| `k6 tidak ditemukan` | Buka ulang Git Bash; atau `K6=/path/ke/k6.exe bash loadtest/kelas.sh` |
| `ExpiredToken` / `UnauthorizedOperation` | Kredensial lama: ulangi B1 (sesi lab masih hidup?) |
| `/health tidak menjawab` | Sesi lab habis atau IP berubah; cek `bash deploy/sandbox/deploy.sh info` |
| Banyak 429 di tabel | Rate limit gagal dinaikkan; cek `/opt/poetify/.env` di app-1 |
| Skrip dihentikan (Ctrl+C) | Skrip memulihkan poem dan rate limit sendiri; pastikan dengan `bash deploy/sandbox/deploy.sh jalankan app-1 'docker ps'` |
| Antrean belum terkuras setelah 5 menit | Catat sebagai temuan (analisis tertinggal di belakang submit) |
| Waktu demo tidak cukup | Putar rekaman gladi |

**Aturan emas:** apa pun yang gagal, jadikan temuan, jangan disembunyikan.

---

## 5. Tambahan manual (bila ditanya)

**Mematikan poem sendiri.** Di luar uji hanya ada satu replika, jadi perintah ini membuat fitur yang
bergantung pada poem gagal. `docker compose start` gagal di server aplikasi ("missing dependency
rabbit-2"), jadi nyalakan dengan `up -d --no-deps`:

```bash
C='cd /opt/poetify && docker compose -f docker-compose.yml -f deploy/sandbox/app.yml'
bash deploy/sandbox/deploy.sh jalankan app-1 "$C stop poem"
bash deploy/sandbox/deploy.sh jalankan app-1 "$C up -d --no-deps poem"
```

**Failover gateway (±60 detik, di luar skrip).**

```bash
bash deploy/sandbox/deploy.sh jalankan app-1 'docker stop puisi-gateway'
# buka/cari di aplikasi lewat alamat CloudFront: pencarian tetap jalan (dilayani app-2)
bash deploy/sandbox/deploy.sh jalankan app-1 'docker start puisi-gateway'
```

Yang dibuktikan: SPOF gateway dimitigasi dengan 2 gateway di mesin berbeda + failover CloudFront.
Batasan: hanya GET/HEAD/OPTIONS; di sandbox, POST selalu ke app-1 (jadi login dan submit gagal selama
gateway-a mati).

---

## 6. Yang dipakai untuk Tabel Analisis Risiko

Setiap baris tabel risiko kini punya bukti:

| Titik kritis | Bukti (dari mana) | Dampak | Solusi terpasang |
|---|---|---|---|
| Gateway (SPOF) | Rehearsal lokal: kill → outage ±18 dtk; live AWS: origin group gateway-b | Total outage tanpa mitigasi | 2 gateway + CloudFront failover, autoheal |
| Sync path (poem) | **Demo live** (`kelas.sh`); gladi 28 Nov: 1 dari 2 replika mati, 0 request gagal | Data turunan tak terjangkau | Replika poem, timeout 3 dtk, cache stale, breaker search |
| Async (RabbitMQ) | Rehearsal: broker mati → outbox 5 pending → drain 0 | Tulis tetap sukses, async tertunda | Outbox + publisher confirms + quorum queue |
| Data (MySQL) | Rehearsal: kill → RTO ±6 dtk, 5xx window | API DB gagal | Backup harian + S3, pool ping=1 |
| Data (Redis) | Rehearsal: stop → idempotency fail-open → 2 baris | Duplikasi saat Redis mati | Breaker Redis; catat gap → Sentinel |
| Observability | Rehearsal: `/health` 200 saat MySQL mati; alert Prometheus aktif | Deteksi manual | Alert rules (DLQ, outbox, breaker, 5xx) |

Hasil demo live mengisi baris **Sync path** dengan angka AWS yang nyata
(request gagal saat satu replika mati), sisanya dari bukti rehearsal.
