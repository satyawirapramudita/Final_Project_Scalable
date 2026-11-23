# SLO (Service Level Objective) — ringkas

Dokumen ini adalah rujukan angka yang dipakai skrip uji beban
(`loadtest/search.js`, `loadtest/stress_test.py`). Angka usulan lengkap dan
alasannya ada di dokumen rancangan arsitektur §10; di sini hanya yang
diperiksa otomatis.

| Alur | SLI | Target | Dipakai oleh |
|---|---|---|---|
| Pencarian `GET /api/search` | latensi p95 | < 300 ms | `stress_test.py` (`SLO_P95_MS`) |
| Pencarian `GET /api/search` | latensi p99 | < 500 ms | `stress_test.py` (`SLO_P99_MS`) |
| Semua endpoint | error rate (5xx + gagal koneksi) | < 1% | `search.js`, `submit.js`, `stress_test.py` |
| Submit `POST /api/poems` | latensi p95 | < 500 ms | `loadtest/submit.js` |

Catatan:

- **k6 threshold** `http_req_duration: p(95)<500` di `search.js` sengaja lebih
  longgar daripada target SLO (300 ms) agar sebuah run tidak dinyatakan gagal
  hanya karena sedikit melewati target; target 300 ms dibaca dari ringkasan
  hasil dan tabel `stress_test.py`. Threshold error tetap `rate<0.01`.
- **Submit** diukur dengan idempotency aktif; latensi mencakup replay
  (respons tersimpan), bukan hanya INSERT pertama.
- Uji beban dijalankan di host lokal (Docker Compose); angka pada instance
  EC2 dapat berbeda dan perlu diukur ulang saat deploy.
