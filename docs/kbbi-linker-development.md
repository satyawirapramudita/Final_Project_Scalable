# Catatan Pengembangan `kbbi-linker`

Tanggal: 10 November 2026  
Branch: `feat/wiki`

## Tujuan

Mengubah rancangan awal `wiki-linker` menjadi layanan KBBI yang memasok bentuk baku dan definisi untuk tooltip Kata Puitif. Layanan harus read-heavy, event-driven, stateless, menggunakan database miliknya sendiri, dan tetap dapat melayani cache miss ketika Redis tidak tersedia.

## Konteks Awal

Implementasi awal hanya memiliki `/health`, `/metrics`, dan TODO untuk Wikipedia. Skema awal menyimpan hasil Wikipedia langsung per `(puisi_id, kata)`. Model tersebut menduplikasi hasil ketika kata yang sama dipakai banyak puisi dan tidak cocok dengan kebutuhan tooltip definisi.

Kontrak event yang sudah tersedia dipertahankan: NLP tetap memublikasikan `poem.analyzed` dengan `kata_puitif[]`; layanan ini hanya mengganti queue konsumen dari `wiki.q` menjadi `kbbi.q`.

## Keputusan Arsitektur

### 1. KBBI menggantikan Wikipedia

Kebutuhan UI adalah bentuk baku, kelas kata, dan definisi singkat, bukan artikel ensiklopedis. Seluruh nama service, route, queue, database, environment variable, dan dokumentasi diubah dari `wiki` menjadi `kbbi`.

Kontrak baru:

- Service: `kbbi-linker`
- Endpoint: `GET /api/kbbi/{poem_id}`
- Queue: `kbbi.q`
- Database: `kbbi_db`

Perubahan ini breaking dan sengaja tidak menyediakan alias `/api/wiki`, karena proyek masih berada pada fase implementasi dan belum memiliki consumer produksi untuk kontrak lama.

### 2. Dataset lokal, bukan scraping KBBI Daring

KBBI Daring tidak mempunyai API publik resmi yang terdokumentasi. Scraping ditolak karena rapuh dan perlu kepastian izin. Service membaca dataset lokal yang diimpor ke MySQL.

Dataset tidak disertakan di repository. Dataset KBBI V yang dievaluasi menyatakan kepemilikan Badan Bahasa dan larangan penggunaan komersial. Pengguna proyek wajib memastikan izin sebelum mengimpor atau mendistribusikan data.

### 3. Kata loosely coupled dari puisi

Model dibuat many-to-many:

```text
kbbi_entries ──< kbbi_definitions
      ▲
      │
poetic_terms >── poem_terms ── poem_id
```

`poem.deleted` hanya menghapus `poem_terms` dan membuat tombstone. `poetic_terms`, `kbbi_entries`, serta definisi tetap tersedia untuk puisi lain. `poetic_terms` tidak menyimpan foreign key ke versi dataset tertentu; definisi di-resolve melalui `normalized_term`, sehingga import versi baru tidak meninggalkan pointer basi.

### 4. Endpoint batch untuk tooltip

Frontend tidak perlu melakukan request pada setiap hover. Satu request saat halaman puisi dibuka mengembalikan seluruh kata beserta definisinya. Tooltip kemudian membaca data dari memori browser.

Status endpoint:

- `200 pending`: event belum diterima; tidak diperlakukan sebagai 404 palsu.
- `200 ready`: read model tersedia, termasuk item `found=false`.
- `401`: JWT hilang/tidak valid.
- `404`: tombstone delete sudah diterima.
- `503`: database tidak tersedia atau pool penuh.

### 5. Cache tidak menduplikasi definisi per puisi

Redis menyimpan:

- `kbbi:poem:{poem_id}`: status dan daftar term, tanpa definisi.
- `kbbi:entry:{version}:{term}`: satu definisi bersama per kata.
- `kbbi:missing:{version}:{term}`: negative cache.

Endpoint menggunakan satu pembacaan relasi dan pipeline Redis untuk semua term. Definisi yang belum ada di Redis diambil melalui satu query MySQL `IN (...)`, bukan N+1 query. Consumer juga memakai bulk insert dan satu bulk-select untuk membangun relasi puisi–kata.

TTL awal:

| Cache | TTL | Alasan |
|---|---:|---|
| Definisi kata | 30 hari | Dataset versioned dan jarang berubah |
| Relasi puisi | 24 jam | Diinvalidasi langsung oleh event |
| Kata tidak ditemukan | 24 jam | Mengurangi query berulang, tetap memberi ruang koreksi import |

MySQL adalah source of truth; kegagalan Redis bersifat fail-open.

### 6. Idempotency dan event terlambat

`kbbi_poem_status` menyimpan `source_event_id`, `analysis_at`, dan tombstone. Consumer:

1. Mengabaikan event ID yang sudah diterapkan.
2. Mengabaikan analisis yang lebih lama.
3. Mengabaikan analisis setelah tombstone delete.
4. Melakukan ACK hanya setelah handler selesai.
5. Memakai retry eksponensial dan DLQ dari `shared.rabbit`.

### 7. JWT lokal

Endpoint mengikuti layanan referensi `search`: Bearer JWT diverifikasi secara lokal melalui `shared.security`, tanpa request sinkron ke auth service.

## Proses Implementasi

### Slice 1 — Logika term

Test ditulis terlebih dahulu untuk normalisasi Unicode/spasi, deduplikasi, mempertahankan bentuk tampilan pertama, serta penggabungan relasi dan definisi. Test awal gagal karena `app.terms` belum ada, kemudian implementasi minimal ditambahkan.

Hasil: 4 test lulus.

### Slice 2 — Consumer event

Test ditulis untuk:

- Deduplikasi `kata_puitif` pada `poem.analyzed`.
- Invalidasi cache setelah perubahan berhasil.
- `poem.deleted` hanya menghapus relasi puisi.
- Event yang tidak dikenal diabaikan.

Kemudian ditambahkan `config.py`, `db.py`, `cache.py`, `consumer.py`, helper shared RabbitMQ/security/metrics, serta skema MySQL.

Hasil: 7 test lulus.

### Slice 3 — API read-heavy

Test endpoint mencakup JWT wajib, status pending, respons definisi siap, dan tombstone 404. Endpoint dibangun menggunakan cache-aside dua lapis dan bulk lookup.

Hasil: 11 test lulus.

### Slice 4 — Importer dan integrasi infrastruktur

Ditambahkan importer CSV yang:

- Memvalidasi header dataset.
- Menormalisasi lookup key.
- Mengimpor bentuk tidak baku sebagai alias menuju bentuk baku.
- Melakukan commit per 1.000 baris.
- Menyimpan beberapa definisi secara berurutan.

Compose, Nginx, `.env.example`, kontrak event, README, dan skema bootstrap diperbarui. Container menggunakan root build context agar dapat mengimpor modul `shared`.

## File Utama

- `services/kbbi-linker/app/main.py`: API dan observability HTTP.
- `services/kbbi-linker/app/consumer.py`: validasi dan penerapan event.
- `services/kbbi-linker/app/db.py`: pool, skema, transaksi, bulk query.
- `services/kbbi-linker/app/cache.py`: cache Redis fail-open.
- `services/kbbi-linker/app/terms.py`: normalisasi dan assembly respons.
- `services/kbbi-linker/scripts/import_kbbi_csv.py`: import dataset.
- `services/kbbi-linker/tests/`: unit dan API tests.

## Verifikasi yang Dilakukan

```text
python -m pytest services/kbbi-linker/tests -q
17 passed

python -m compileall -q services/kbbi-linker shared
berhasil

docker compose config --quiet
berhasil; memberi warning KBBI_DB_PASSWORD kosong pada .env lokal

docker compose build kbbi-linker
tidak dapat dijalankan karena Docker daemon tidak tersedia pada mesin pengembangan
```

Warning deprecation test berasal dari FastAPI/Starlette yang dipin dan Python 3.14 lokal; bukan kegagalan test. Image produksi memakai Python 3.12 sesuai Dockerfile.

Audit lanjutan memperketat validasi UUID dan timezone event, menambahkan log JSON
terkorelasi, membuat re-import versi dataset yang sama menghapus definisi basi, serta
memasukkan script importer ke image agar dapat dijalankan melalui jaringan Compose.

## Batasan dan Tindak Lanjut

1. Dataset tidak disertakan dan harus diimpor sebelum definisi tersedia.
2. Importer menggunakan model upsert versioned. Penghapusan versi lama perlu dilakukan eksplisit setelah masa rollback.
3. Bentuk tidak baku dipisahkan konservatif berdasarkan koma/titik koma; kualitas mapping harus divalidasi pada dataset yang dipilih.
4. Uji integrasi nyata dengan MySQL, Redis, RabbitMQ, dan satu sampel dataset masih diperlukan di lingkungan Docker.
5. Branch lain yang masih menggunakan `QUEUE_WIKI` harus dimigrasikan ke `QUEUE_KBBI` sebelum merge bila memiliki salinan kontrak event lama.
6. `db/init.sql` hanya berjalan pada volume MySQL baru. Volume lama memerlukan pembuatan database/user/tabel KBBI secara manual; volume tidak boleh dihapus bila berisi data yang perlu dipertahankan.
