# Laporan Stress Test — AWS Academy Sandbox

| | |
|---|---|
| **Tanggal uji** | 28 November 2026, 20:27–20:35 WIB, pada deploy baru |
| **Objek uji** | Deploy `deploy/sandbox/deploy.sh`: 3 server `t3.small` + S3 + CloudFront (mode split), `us-east-1`; satu replika per layanan per server aplikasi (bawaan) |
| **Alat** | `bash loadtest/kelas.sh` (k6 1.3.0, `loadtest/aws_stress.js` profil `kelas`) |
| **Beban dikirim dari** | Satu laptop di Indonesia, langsung ke IP publik app-1 (tanpa CloudFront) |
| **Cara menjalankan** | [`Demo-Kelas-AWS.md`](Demo-Kelas-AWS.md) |

Laporan ini soal **beban dan kapasitas** (skenario yang dipakai saat demo kelas). Uji ketahanan
(failover gateway, poem mati, node RabbitMQ, MySQL, idempotency):
[`Laporan-Uji-Produksi-Sandbox.md`](Laporan-Uji-Produksi-Sandbox.md).

## 1. Ringkasan

Pertanyaan uji: kapan sistem mulai kewalahan, berapa pengguna yang sanggup ditampung, dan berapa lama
pulih. Hasil satu kali uji pada deploy baru:

| Pertanyaan | Hasil |
|---|---|
| Masih sehat sampai | **80 VU** |
| Mulai kewalahan di | **160 VU** (p95 ±2 detik) |
| Throughput tertinggi | **217 request/detik** |
| Ramp sampai 320 VU dan spike 300 VU | **tidak ada request gagal**, tetapi lambat (p95 ±4 detik) |
| Pulih setelah spike | **±10 detik**, yaitu selama beban diturunkan |
| Data tertinggal | **tidak ada** (semua puisi dianalisis, outbox, antrean, dan DLQ kosong) |

"Sehat" berarti p95 <= 1000 ms dan error <= 1% pada tahap itu. Ambang ini bukan SLO 300 ms
(`slo.md`): latensi diukur dari laptop, dan pada beban ringan pun sudah ±280 ms karena perjalanan ke
`us-east-1`.

## 2. Skenario

Beban: 50% search, 20% daftar puisi, 10% wordcloud, 10% kbbi, 10% submit puisi, oleh VU yang mengirim
request terus-menerus tanpa jeda, bergantian memakai 50 identitas uji. Data awal: 1000 puisi asli.
Rate limit gateway app-1 dinaikkan selama uji dan dikembalikan sesudahnya.

Fase (6 menit beban; seluruh skrip ±8 menit): baseline 10 VU → ramp 20, 40, 80, 160, 320 VU → jeda →
spike 300 VU → pulih 10 VU.

## 3. Hasil per fase

| Fase | Request | req/s | p95 ms | p99 ms | Error |
|---|---:|---:|---:|---:|---:|
| baseline (10 VU) | 1117 | 37 | 284 | 296 | 0,0% |
| ramp-20 | 2254 | 73 | 282 | 306 | 0,0% |
| ramp-40 | 4371 | 141 | 315 | 442 | 0,0% |
| ramp-80 | 6958 | 217 | 652 | 980 | 0,0% |
| ramp-160 | 7051 | 196 | 1966 | 3104 | 0,0% |
| ramp-320 | 6181 | 167 | 4023 | 5116 | 0,0% |
| jeda | 284 | 26 | 666 | 2116 | 0,0% |
| spike (300 VU) | 13142 | 168 | 4317 | 5469 | 0,0% |
| pulih (10 VU, termasuk penurunan) | 2478 | 60 | 2621 | 4032 | 0,0% |

Tidak ada respons 429 di fase mana pun, jadi yang terukur bukan rate limiter.

| Endpoint (seluruh uji) | Request | p95 ms | Error |
|---|---:|---:|---:|
| `GET /api/search` | 26743 | 664 | 0,0% |
| `GET /api/poems` | 10742 | 4621 | 0,0% |
| `POST /api/poems` | 5282 | 4182 | 0,0% |
| `GET /api/wordcloud/{id}` | 5390 | 2263 | 0,0% |
| `GET /api/kbbi/{id}` | 5269 | 2266 | 0,0% |

Integritas: 5282 puisi tersimpan, belum dianalisis 0, outbox tertunda 0, pesan antrean 0, DLQ 0;
antrean terkuras ±15 detik setelah beban berhenti.

## 4. Temuan

1. **Bottleneck pertama: layanan poem.** p95 yang diukur server (Prometheus) selama uji: poem 4,3 detik,
   search 0,84 detik, wordcloud, kbbi, dan auth ±0,01 detik. Wordcloud dan kbbi lambat dari sisi
   pengguna karena gateway bertanya ke poem lebih dulu. Cuplikan CPU di awal tahap 160 VU: poem ±66%,
   search ±41%, MySQL ±83% (dari 2 vCPU di server data).
2. **Antrean berpindah, kapasitas tetap.** Pada run lain dengan **dua** replika poem di app-1, poem
   lebih cepat (daftar puisi p95 ±3,6 detik) tetapi search ikut lambat (p95 3,4–4,4 detik), sedangkan
   throughput tertinggi hampir sama (218–223 request/detik). Dengan poem lebih cepat, lebih banyak
   query mencapai server data (2 vCPU, dipakai bersama MySQL, Redis, dan tiga node RabbitMQ), dan
   search yang mengantre. Batas sesungguhnya adalah CPU server data; menambah replika poem saja tidak
   menaikkan kapasitas.
3. **`GET /api/poems` tidak berpaginasi:** parameter `limit` diabaikan dan semua puisi pengguna
   dikirim. Diduga ikut memperlambat poem; belum diuji terpisah.
4. **Beban berlebih membuat sistem lambat, bukan gagal:** tidak ada error selama ramp dan spike.
5. **Cache pencarian hampir tidak terpakai saat banyak submit:** setiap puisi baru membatalkan seluruh
   cache layanan search (paling cepat tiap 2 detik). Memperjarangnya (`CACHE_BUMP_INTERVAL`) belum diuji.
6. **Health check search dibuat tidak ikut antre.** Pada satu run dengan dua replika poem, autoheal
   menganggap search tidak sehat di 320 VU dan merestartnya (±10 detik error 502). `/health` search
   kini `async` agar tidak menunggu di antrean thread yang sama dengan pencarian. Ini langkah
   pencegahan: uji sebelum/sesudah tidak menunjukkan perbedaan waktu health check yang jelas.

## 5. Batasan

- Satu laptop sebagai pembangkit beban; latensi mutlak tidak sebanding dengan uji lokal di `slo.md`.
- VU bukan pengguna sungguhan (tanpa jeda berpikir).
- Satu kali uji pada data awal 1000 puisi; harapkan angka yang bervariasi, dan jangan dianggap
  berlaku untuk data yang jauh lebih banyak.
- Stress test ini tidak mematikan layanan. Kegagalan layanan diuji terpisah di
  [`Laporan-Uji-Produksi-Sandbox.md`](Laporan-Uji-Produksi-Sandbox.md).
- Tidak diuji: registrasi/login, ubah/hapus puisi, idempotency di bawah beban, dan pembuatan gambar
  (mati di sandbox).
- Data mentah (`loadtest/results/`) tidak di-commit.
