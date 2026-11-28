# Laporan Stress Test — AWS Academy Sandbox

| | |
|---|---|
| **Tanggal uji** | 28 November 2026, 13:26–13:35 WIB, pada deploy baru |
| **Objek uji** | Deploy `deploy/sandbox/deploy.sh`: 3 server `t3.small` + S3 + CloudFront (mode split), `us-east-1` |
| **Alat** | `bash loadtest/kelas.sh` (k6 1.3.0, `loadtest/aws_stress.js` profil `kelas`) |
| **Beban dikirim dari** | Satu laptop di Indonesia, langsung ke IP publik app-1 (tanpa CloudFront) |
| **Cara menjalankan** | [`Demo-Kelas-AWS.md`](Demo-Kelas-AWS.md) |

Laporan ini soal **beban dan kapasitas** (skenario yang dipakai saat demo kelas). Uji ketahanan lain
(failover gateway, node RabbitMQ, MySQL, idempotency):
[`Laporan-Uji-Produksi-Sandbox.md`](Laporan-Uji-Produksi-Sandbox.md).

## 1. Ringkasan

Pertanyaan uji: kapan sistem mulai kewalahan, berapa pengguna yang sanggup ditampung, dan berapa lama
pulih. Hasil satu kali uji pada deploy baru:

| Pertanyaan | Hasil |
|---|---|
| Masih sehat sampai | **80 VU** |
| Mulai kewalahan di | **160 VU** (p95 ±2 detik) |
| Throughput tertinggi | **217 request/detik** |
| Ramp sampai 320 VU dan spike 300 VU | **tidak ada request gagal**, tetapi lambat (p95 ±4,5 detik) |
| Pulih setelah spike | **±10 detik**, yaitu selama beban diturunkan |
| Poem dimatikan ±30 detik | error ±48 detik; **search tidak gagal sama sekali** |
| Data tertinggal | **tidak ada** (semua puisi dianalisis, outbox, antrean, dan DLQ kosong) |

"Sehat" berarti p95 <= 1000 ms dan error <= 1% pada tahap itu. Ambang ini bukan SLO 300 ms
(`slo.md`): latensi diukur dari laptop, dan pada beban ringan pun sudah ±280 ms karena perjalanan ke
`us-east-1`.

## 2. Skenario

Beban: 50% search, 20% daftar puisi, 10% wordcloud, 10% kbbi, 10% submit puisi, oleh VU yang mengirim
request terus-menerus tanpa jeda, bergantian memakai 50 identitas uji. Data awal: 1000 puisi asli.
Rate limit gateway app-1 dinaikkan selama uji dan dikembalikan sesudahnya.

Fase (7,5 menit beban; seluruh skrip 9 menit 17 detik): baseline 10 VU → ramp 20, 40, 80, 160, 320 VU → jeda → spike 300 VU
→ pulih 10 VU → 20 VU sambil poem di app-1 dimatikan ±30 detik.

## 3. Hasil per fase

| Fase | Request | req/s | p95 ms | p99 ms | Error |
|---|---:|---:|---:|---:|---:|
| baseline (10 VU) | 1112 | 36 | 287 | 313 | 0,0% |
| ramp-20 | 2245 | 72 | 285 | 314 | 0,0% |
| ramp-40 | 4264 | 138 | 346 | 554 | 0,0% |
| ramp-80 | 6724 | 217 | 712 | 956 | 0,0% |
| ramp-160 | 6338 | 186 | 2114 | 3066 | 0,0% |
| ramp-320 | 5827 | 162 | 4429 | 5509 | 0,0% |
| jeda | 335 | 30 | 538 | 794 | 0,0% |
| spike (300 VU) | 12683 | 165 | 4562 | 5667 | 0,0% |
| pulih (10 VU, termasuk penurunan) | 2302 | 56 | 2719 | 3900 | 0,0% |
| gagal (poem dimatikan) | 4836 | 60 | 312 | 3245 | 25,8% |

Tidak ada respons 429 di fase mana pun, jadi yang terukur bukan rate limiter.

| Endpoint (seluruh uji) | Request | p95 ms | Error |
|---|---:|---:|---:|
| `GET /api/search` | 28261 | 483 | 0,0% |
| `GET /api/poems` | 11227 | 4772 | 4,6% |
| `POST /api/poems` | 5727 | 4395 | 4,6% |
| `GET /api/wordcloud/{id}` | 5687 | 2375 | 3,9% |
| `GET /api/kbbi/{id}` | 5591 | 2328 | 4,3% |

Error pada empat endpoint terakhir berasal dari fase poem dimatikan.

Integritas: 5462 puisi tersimpan, belum dianalisis 0, outbox tertunda 0, pesan antrean 0, DLQ 0;
antrean terkuras ±15 detik setelah beban berhenti.

## 4. Temuan

1. **Bottleneck pertama: layanan poem.** p95 yang diukur server (Prometheus) selama uji: poem 4,4 detik,
   search 0,41 detik, wordcloud, kbbi, dan auth ±0,01 detik. Wordcloud dan kbbi lambat dari sisi
   pengguna karena gateway bertanya ke poem lebih dulu.
2. **`GET /api/poems` tidak berpaginasi:** parameter `limit` diabaikan dan semua puisi pengguna
   dikirim. Diduga ikut memperlambat poem; belum diuji terpisah.
3. **Beban berlebih membuat sistem lambat, bukan gagal:** tidak ada error selama ramp dan spike.
4. **Poem adalah titik kritis:** saat mati, daftar/submit puisi, wordcloud, dan kbbi ikut gagal; search
   tetap melayani (0 gagal dari 2457 request).

## 5. Batasan

- Satu laptop sebagai pembangkit beban; latensi mutlak tidak sebanding dengan uji lokal di `slo.md`.
- VU bukan pengguna sungguhan (tanpa jeda berpikir).
- Satu kali uji pada data awal 1000 puisi; harapkan angka yang bervariasi, dan jangan dianggap
  berlaku untuk data yang jauh lebih banyak.
- "Lama error" mencakup jeda pengiriman perintah lewat SSM dan waktu start poem.
- Tidak diuji: registrasi/login, ubah/hapus puisi, matinya node RabbitMQ, MySQL, atau gateway,
  idempotency di bawah beban, dan pembuatan gambar (mati di sandbox).
- Data mentah (`loadtest/results/`) tidak di-commit.
