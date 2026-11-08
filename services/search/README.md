# search service

Pemilik: Anders. Indeks dan pencarian puisi (MySQL FULLTEXT di `search_db`).

## Endpoint

`GET /api/search` — butuh `Authorization: Bearer <jwt>`.

| Parameter | Keterangan |
|---|---|
| `q` | Wajib. Minimal 2 karakter setelah dibersihkan; maksimal 8 kata. Tiap kata dicocokkan sebagai awalan (`sen` menemukan `senja`); dokumen yang cocok dengan lebih banyak kata diurut lebih tinggi. |
| `kategori` | Opsional. Kategori persis. |
| `tag` | Opsional. Satu tag hasil analisis. |
| `page`, `limit` | Default 1 dan 10. `limit` dibatasi 20, `page` dibatasi 50. |

Respons: `{ok, q, page, limit, has_more, items:[{id, judul, snippet, kategori, tags, score}], cached}`.
Pencarian hanya mencakup puisi milik pengguna di JWT (puisi bersifat privat). `400` bila `q` kosong, `503` bila database penuh atau tidak terjangkau.

## Cara kerja

- **Indeks** dipelihara dari event, bukan dari database poem: `poem.created/updated` mengisi judul, isi, kategori; `poem.analyzed` mengisi tag; `poem.deleted` memberi tombstone.
- **Urutan event aman.** Tiap kelompok data punya cap waktu event; event yang lebih lama tidak menimpa yang lebih baru, dan puisi yang sudah dihapus tidak hidup lagi oleh event yang terlambat. Pengiriman ganda aman.
- **Cache Redis** per pengguna (TTL 45 dtk). Setiap event yang mengubah indeks menaikkan versi cache pengguna itu, sehingga puisi baru langsung muncul tanpa menunggu TTL.
- **Perlindungan beban.** Koneksi MySQL dibatasi (`DB_POOL_SIZE`, default 10); request yang tidak kebagian koneksi gagal cepat (503). Saat Redis atau MySQL mati, layanan berhenti mencobanya sementara sehingga request tidak menunggu ~8 dtk.
- **Pemadaman MySQL.** Consumer menahan event hingga ~45 dtk menunggu database kembali, alih-alih membuangnya ke DLQ. Bila lebih lama, bangun ulang indeks dengan `scripts/reindex_search.py`.

## Operasional

```powershell
# bangun ulang indeks dari poem_db (indeks baru/kosong, atau event terlewat)
Get-Content scripts/reindex_search.py | docker exec -i puisi-poem python -

# seed puisi palsu untuk uji beban (user 99), lalu bersihkan
Get-Content scripts/publish_fake_events.py | docker compose exec -T search python - --count 1000 --user 99
#   DELETE FROM search_db.search_poem WHERE user_id = 99;

# scale out (gateway menyebar request ke semua replika lewat DNS Docker)
docker compose up -d --scale search=3 search
```

Database `search_db` dan pengguna `search_user` dibuat oleh `db/init.sql` hanya saat volume MySQL pertama kali dibuat.
Pada volume yang sudah ada, jalankan `CREATE DATABASE/USER/GRANT` untuk `search_db` secara manual (atau `docker compose down -v`).

Uji beban: `loadtest/search.js` (k6). Metrik di `/metrics`: `http_request_duration_seconds`,
`search_cache_total{result}`, `search_events_total{event,outcome}`, `search_db_pool_timeouts_total`.

## Batasan yang diketahui

- Satu proses melayani sekitar 200 req/s sebelum CPU penuh (terukur lokal, 20% request menembus cache); skala dengan menambah replika.
- MySQL FULLTEXT tidak melakukan stemming bahasa Indonesia dan mengabaikan kata di bawah 3 huruf pada indeks.
- Puisi yang dihapus saat search mati tidak terdeteksi oleh `reindex_search.py`.
