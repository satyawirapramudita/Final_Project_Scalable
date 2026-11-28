# Laporan Stress Test — AWS Academy Sandbox

| | |
|---|---|
| **Tanggal uji** | 28 November 2026, 15:35–15:45 WIB, pada deploy baru |
| **Objek uji** | Deploy `deploy/sandbox/deploy.sh`: 3 server `t3.small` + S3 + CloudFront (mode split), `us-east-1`; selama uji poem di app-1 dijalankan **2 replika** (deploy bawaan: 1) |
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
| Throughput tertinggi | **222 request/detik** |
| Ramp sampai 320 VU dan spike 300 VU | **tidak ada request gagal**, tetapi lambat (p95 ±4–5 detik) |
| Pulih setelah spike | **±15 detik**, termasuk 10 detik penurunan beban |
| 1 dari 2 replika poem dimatikan ±45 detik | **tidak ada request gagal** (0 dari 5230 pada fase itu) |
| Data tertinggal | **tidak ada** (semua puisi dianalisis, outbox, antrean, dan DLQ kosong) |

"Sehat" berarti p95 <= 1000 ms dan error <= 1% pada tahap itu. Ambang ini bukan SLO 300 ms
(`slo.md`): latensi diukur dari laptop, dan pada beban ringan pun sudah ±300 ms karena perjalanan ke
`us-east-1`.

## 2. Skenario

Beban: 50% search, 20% daftar puisi, 10% wordcloud, 10% kbbi, 10% submit puisi, oleh VU yang mengirim
request terus-menerus tanpa jeda, bergantian memakai 50 identitas uji. Data awal: 1000 puisi asli.
Rate limit gateway app-1 dinaikkan selama uji dan dikembalikan sesudahnya.

Fase (7,5 menit beban; seluruh skrip ±9 menit 55 detik): baseline 10 VU → ramp 20, 40, 80, 160, 320 VU → jeda → spike 300 VU
→ pulih 10 VU → 20 VU sambil satu dari dua replika poem di app-1 dimatikan (`docker stop`, penghentian normal,
bukan crash). Replika kedua dijalankan skrip sebelum uji dan dikembalikan ke 1 sesudahnya, jadi **semua fase**
diukur dengan 2 replika poem di app-1.

## 3. Hasil per fase

| Fase | Request | req/s | p95 ms | p99 ms | Error |
|---|---:|---:|---:|---:|---:|
| baseline (10 VU) | 1061 | 35 | 298 | 318 | 0,0% |
| ramp-20 | 2136 | 71 | 305 | 325 | 0,0% |
| ramp-40 | 4150 | 134 | 329 | 430 | 0,0% |
| ramp-80 | 7114 | 222 | 555 | 818 | 0,0% |
| ramp-160 | 7062 | 214 | 1775 | 2345 | 0,0% |
| ramp-320 | 6707 | 186 | 4931 | 6169 | 0,0% |
| jeda | 311 | 28 | 588 | 649 | 0,0% |
| spike (300 VU) | 14717 | 191 | 4277 | 5875 | 0,0% |
| pulih (10 VU, termasuk penurunan) | 2271 | 55 | 3165 | 4479 | 0,0% |
| gagal (1 dari 2 replika poem dimatikan) | 5230 | 65 | 384 | 650 | 0,0% |

Tidak ada respons 429 di fase mana pun, jadi yang terukur bukan rate limiter.

| Endpoint (seluruh uji) | Request | p95 ms | Error |
|---|---:|---:|---:|
| `GET /api/search` | 30202 | 3495 | 0,0% |
| `GET /api/poems` | 12021 | 3606 | 0,0% |
| `POST /api/poems` | 6097 | 3118 | 0,0% |
| `GET /api/wordcloud/{id}` | 6019 | 2060 | 0,0% |
| `GET /api/kbbi/{id}` | 6167 | 2157 | 0,0% |

Integritas: 6097 puisi tersimpan, belum dianalisis 0, outbox tertunda 0, pesan antrean 0, DLQ 0;
antrean terkuras ±15 detik setelah beban berhenti.

## 4. Temuan

1. **Bottleneck: CPU server data** (2 vCPU, dipakai bersama MySQL, Redis, dan tiga node RabbitMQ).
   Pada uji ini layanan paling lambat adalah search: p95 di server (Prometheus) ±4,4 detik, poem ±1,0 detik,
   wordcloud, kbbi, dan auth ±0,01 detik (angka poem hanya mewakili sebagian lalu lintas karena Prometheus
   mengambil sampel dari satu replika per kali). Penyebabnya diperiksa dengan uji singkat terpisah
   (160 VU, 45 detik, satu replika poem, data ±7.000+ puisi, tanpa wordcloud/kbbi):

   | Beban | Request/detik | p95 search |
   |---|---:|---:|
   | search saja | 418 | 0,9 detik |
   | campuran (search, daftar, submit) | 137 | 3,7 detik |

   Pada beban campuran:
   - CPU server data tidak punya waktu idle (antrean 17–26 proses untuk 2 vCPU); MySQL ±100–125%.
     Server aplikasi masih longgar (idle 16–77%).
   - Koneksi database search (pool 10) terpakai semua, sehingga request mengantre. Query yang sama hanya
     3–5 ms saat server menganggur.
   - Cache di layanan search **0 hit**: setiap puisi baru membatalkan seluruh cache (paling cepat tiap
     2 detik). Cache gateway tidak termasuk hitungan ini.

   Perbaikan belum diuji; yang paling murah adalah memperjarang pembatalan cache
   (`CACHE_BUMP_INTERVAL`), dengan akibat puisi baru lebih lambat muncul di pencarian.
2. **`GET /api/poems` tidak berpaginasi:** parameter `limit` diabaikan dan semua puisi pengguna
   dikirim. Diduga memperlambat daftar puisi; belum diuji terpisah.
3. **Beban berlebih membuat sistem lambat, bukan gagal:** tidak ada error selama ramp dan spike.
4. **Dua replika poem menutupi matinya satu replika:** tidak ada request gagal saat satu replika
   dihentikan, dan tidak ada data yang tertinggal.

## 5. Batasan

- Satu laptop sebagai pembangkit beban; latensi mutlak tidak sebanding dengan uji lokal di `slo.md`.
- VU bukan pengguna sungguhan (tanpa jeda berpikir).
- Satu kali uji pada data awal 1000 puisi; harapkan angka yang bervariasi, dan jangan dianggap
  berlaku untuk data yang jauh lebih banyak.
- Deploy bawaan menjalankan 1 replika poem per server. Bila satu-satunya replika di sebuah server mati,
  request yang bergantung pada poem di server itu gagal (lihat uji "poem mati" di
  [`Laporan-Uji-Produksi-Sandbox.md`](Laporan-Uji-Produksi-Sandbox.md)).
- Replika dihentikan secara normal; crash mendadak (`docker kill`) tidak diuji.
- Tidak diuji: registrasi/login, ubah/hapus puisi, matinya node RabbitMQ, MySQL, atau gateway,
  idempotency di bawah beban, dan pembuatan gambar (mati di sandbox).
- Data mentah (`loadtest/results/`) tidak di-commit.
