# kbbi-linker

Layanan read-heavy yang menghubungkan `kata_puitif` hasil NLP dengan bentuk baku dan definisi KBBI. Dataset KBBI disimpan lokal di `kbbi_db`; puisi dan kata berelasi many-to-many sehingga definisi tidak diduplikasi per puisi.

## Endpoint

`GET /api/kbbi/{poem_id}` membutuhkan `Authorization: Bearer <jwt>` dan mengembalikan semua definisi untuk satu puisi agar frontend dapat menampilkan tooltip tanpa request pada setiap hover.

Status respons:

- `200 ready`: analisis tersedia, termasuk kata yang tidak ditemukan (`found=false`).
- `200 pending`: event analisis belum diterima.
- `401`: JWT hilang/tidak valid.
- `404`: tombstone `poem.deleted` sudah diterima.
- `503`: MySQL tidak tersedia atau pool koneksi penuh.

## Cache Redis

- `kbbi:poem:{poem_id}`: relasi puisi–kata, TTL 24 jam, diinvalidasi oleh event.
- `kbbi:entry:{dataset_version}:{term}`: definisi hot, TTL 30 hari.
- `kbbi:missing:{dataset_version}:{term}`: negative cache, TTL 24 jam.

MySQL tetap menjadi source of truth. Redis boleh kosong atau tidak tersedia tanpa kehilangan data.

## Import dataset

Dataset tidak disertakan dalam repository karena kepemilikan dan batasan nonkomersialnya. Pastikan penggunaan dataset telah diizinkan. Jalankan importer di jaringan Compose karena MySQL sengaja tidak diekspos ke host:

```powershell
docker compose up -d mysql
docker compose run --rm --no-deps `
  -v "C:\data\kbbi_v.csv:/data/kbbi.csv:ro" `
  kbbi-linker python scripts/import_kbbi_csv.py /data/kbbi.csv --version kbbi-v-2024-07
```

Set `KBBI_DATASET_VERSION` pada `.env` ke versi yang sama, lalu restart `kbbi-linker`.

`db/init.sql` hanya berjalan saat volume MySQL pertama kali dibuat. Untuk volume lama, buat `kbbi_db`, `kbbi_user`, grant, dan tabel secara manual. Jangan menjalankan `docker compose down -v` pada data yang perlu dipertahankan.

## Test

```powershell
python -m pip install -r services/kbbi-linker/requirements-dev.txt
python -m pytest services/kbbi-linker/tests -q
```
