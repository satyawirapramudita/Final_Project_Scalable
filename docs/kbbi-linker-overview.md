# Ikhtisar Teknis Modul `kbbi-linker`

## 1. Tujuan Modul

`kbbi-linker` adalah layanan yang menghubungkan kata-kata puitis hasil analisis NLP dengan entri kamus KBBI. Modul ini tidak menganalisis teks puisi secara langsung; daftar `kata_puitif` diterima melalui event `poem.analyzed`.

[VERIFY: services/kbbi-linker/app/consumer.py:50-72]

Hasil pemrosesan tersedia melalui endpoint terautentikasi:

```http
GET /api/kbbi/{poem_id}
Authorization: Bearer <JWT>
```

[VERIFY: services/kbbi-linker/app/main.py:65-68]

Contoh bentuk respons:

```json
{
  "ok": true,
  "poem_id": 42,
  "status": "ready",
  "cached": true,
  "dataset_version": "kbbi-v-local",
  "items": [
    {
      "kata_asli": "Senja",
      "normalized_term": "senja",
      "found": true,
      "bentuk_baku": "senja",
      "kelas_kata": "nomina",
      "definisi": ["waktu sesudah matahari terbenam"]
    }
  ]
}
```

Struktur setiap item dibentuk oleh `assemble_items()`.

[VERIFY: services/kbbi-linker/app/terms.py:37-56]

## 2. Struktur Internal

```text
services/kbbi-linker/
├── app/
│   ├── main.py             # FastAPI, endpoint, metrics, lifecycle
│   ├── consumer.py         # Konsumen poem.analyzed dan poem.deleted
│   ├── db.py               # Skema, connection pool, repository MySQL
│   ├── cache.py            # Cache-aside Redis
│   ├── terms.py            # Normalisasi dan penyusunan hasil
│   ├── security.py         # Validasi JWT
│   ├── config.py           # Konfigurasi environment
│   └── logging_config.py   # Structured logging
├── scripts/
│   └── import_kbbi_csv.py  # Import dataset KBBI
└── tests/                  # Pengujian API, consumer, importer, terms
```

Secara logis, modul memiliki tiga jalur utama:

1. **Write path** melalui event RabbitMQ.
2. **Read path** melalui FastAPI.
3. **Data preparation** melalui import dataset KBBI.

## 3. Gambaran Alur Utama

```text
NLP Worker
    │
    │ poem.analyzed
    ▼
RabbitMQ: kbbi.q
    │
    ▼
consumer.py
    ├── validasi envelope
    ├── normalisasi kata
    ├── deduplikasi
    └── repository.apply_analysis()
             │
             ▼
          MySQL
             │
             └── invalidasi Redis

Pengguna
    │ GET /api/kbbi/{poem_id}
    ▼
Validasi JWT
    │
    ▼
Redis ── miss ──► MySQL
    │                │
    └────────────────┘
             │
             ▼
      assemble_items()
             │
             ▼
        Respons JSON
```

## 4. Write Path: Pemrosesan Event

### 4.1 Event yang diproses

Consumer hanya menangani `poem.analyzed` dan `poem.deleted`. Event lain dikembalikan sebagai `ignored`, bukan dianggap sebagai kegagalan.

[VERIFY: services/kbbi-linker/app/consumer.py:50-58]

Consumer `kbbi.q` dijalankan ketika lifecycle FastAPI dimulai.

[VERIFY: services/kbbi-linker/app/main.py:29-36]  
[VERIFY: services/kbbi-linker/app/consumer.py:107-117]

### 4.2 Validasi event

Sebelum diproses, consumer memastikan:

- `poem_id` merupakan integer positif;
- `event_id` merupakan UUID valid;
- `occurred_at` memiliki timezone;
- `payload` berbentuk dictionary; dan
- `kata_puitif` berbentuk array.

[VERIFY: services/kbbi-linker/app/consumer.py:23-47]  
[VERIFY: services/kbbi-linker/app/consumer.py:56-66]

### 4.3 Normalisasi istilah

Setiap istilah diproses dengan aturan berikut:

1. Normalisasi Unicode NFKC.
2. Penyederhanaan spasi berulang.
3. Penghapusan spasi awal dan akhir.
4. Normalisasi huruf menggunakan `casefold()`.
5. Pembatasan panjang hingga 100 karakter.
6. Deduplikasi berdasarkan bentuk normalisasi.

[VERIFY: services/kbbi-linker/app/terms.py:9-34]

Contoh konseptual:

```text
"  Senja  "  → display: "Senja"
              normalized: "senja"

"SENJA"      → normalized: "senja"
               dianggap duplikat
```

Jumlah istilah per puisi secara default dibatasi hingga 30.

[VERIFY: services/kbbi-linker/app/config.py:37]

### 4.4 Penyimpanan hasil analisis

`Repository.apply_analysis()` melakukan proses berikut dalam satu transaksi:

1. Membuat status awal `pending` jika puisi belum dikenal.
2. Mengunci baris status dengan `SELECT ... FOR UPDATE`.
3. Mengabaikan event jika puisi sudah dihapus, event yang sama sudah diproses, atau waktu event lebih lama dari analisis terakhir.
4. Menghapus relasi istilah lama.
5. Melakukan upsert istilah puitis.
6. Membuat relasi puisi–istilah baru.
7. Mengubah status menjadi `ready`.
8. Menyimpan ID event dan waktu analisis.

[VERIFY: services/kbbi-linker/app/db.py:137-182]

Mekanisme tersebut membuat pemrosesan event analisis idempoten dan mencegah event analisis lama menimpa data yang lebih baru.

### 4.5 Penghapusan puisi

Untuk `poem.deleted`, semua relasi istilah milik puisi dihapus dan statusnya diubah menjadi `deleted`.

[VERIFY: services/kbbi-linker/app/db.py:184-196]

Setelah event berhasil diterapkan, cache puisi dihapus agar request berikutnya membaca data terbaru.

[VERIFY: services/kbbi-linker/app/consumer.py:73-79]

## 5. Keandalan RabbitMQ

Consumer berjalan pada daemon thread dan menggunakan:

- durable topic exchange dan queue;
- manual acknowledgement;
- `prefetch_count`;
- maksimal tiga percobaan handler;
- exponential backoff;
- dead-letter queue setelah semua percobaan gagal; dan
- reconnect otomatis ketika koneksi terputus.

[VERIFY: shared/rabbit.py:56-75]  
[VERIFY: shared/rabbit.py:121-170]

```text
Pesan masuk
   │
   ├─ JSON tidak valid ─────────────► NACK ─► kbbi.q.dlq
   │
   └─ JSON valid
         │
         ├─ proses berhasil ─────────► ACK
         │
         └─ proses gagal
               ├─ retry 1
               ├─ retry 2
               ├─ retry 3
               └─ NACK ─────────────► kbbi.q.dlq
```

[VERIFY: shared/rabbit.py:143-160]

## 6. Read Path: API KBBI

### 6.1 Autentikasi

Endpoint KBBI mewajibkan Bearer Token. Token diperiksa melalui `shared.security.decode_token()`. Token kosong, salah format, tidak valid, atau kedaluwarsa menghasilkan HTTP 401.

[VERIFY: services/kbbi-linker/app/security.py:8-15]

### 6.2 Proses pembacaan

`get_poem_definitions()` melakukan langkah berikut:

1. Mencari status dan istilah puisi di Redis.
2. Mengambil data dari MySQL ketika cache tidak tersedia.
3. Menyimpan hasil pembacaan puisi ke Redis.
4. Mengembalikan status `pending` jika hasil analisis belum tersedia.
5. Mengembalikan 404 jika puisi telah dihapus.
6. Mencari definisi setiap istilah di Redis.
7. Mengambil definisi yang belum berada di cache melalui satu query MySQL.
8. Menggabungkan relasi istilah dengan entri KBBI.
9. Mengembalikan hasil sebagai JSON.

[VERIFY: services/kbbi-linker/app/main.py:69-103]

### 6.3 Status respons

| Kondisi | Respons |
|---|---|
| Hasil analisis belum tersedia | `200`, status `pending`, items kosong |
| Analisis tersedia | `200`, status `ready` |
| Puisi telah dihapus | `404` |
| Connection pool penuh | `503` |
| MySQL gagal | `503` |

[VERIFY: services/kbbi-linker/app/main.py:77-108]

Status `pending` digunakan karena pemrosesan NLP berjalan asinkron: puisi dapat sudah tersedia, sementara event hasil analisis belum diterima `kbbi-linker`.

## 7. Strategi Cache Redis

| Cache | Key | TTL default |
|---|---|---:|
| Status dan istilah puisi | `kbbi:poem:{poem_id}` | 1 hari |
| Entri KBBI | `kbbi:entry:{dataset_version}:{term}` | 30 hari |
| Negative cache | `kbbi:missing:{dataset_version}:{term}` | 1 hari |

[VERIFY: services/kbbi-linker/app/cache.py:24-34]  
[VERIFY: services/kbbi-linker/app/config.py:31-36]

### 7.1 Negative cache

Jika istilah tidak ditemukan, modul menyimpan penanda `missing`. Hal ini mencegah istilah yang sama terus-menerus menghasilkan query MySQL.

[VERIFY: services/kbbi-linker/app/cache.py:60-83]  
[VERIFY: services/kbbi-linker/app/cache.py:85-97]

### 7.2 Versioned cache key

Versi dataset dimasukkan ke cache key. Ketika versi dataset berubah, aplikasi menggunakan namespace cache baru tanpa harus lebih dahulu menghapus seluruh cache lama.

[VERIFY: services/kbbi-linker/app/cache.py:28-34]

### 7.3 Degradasi ketika Redis gagal

Redis tidak menjadi syarat wajib pada read path. Kegagalan membaca cache dianggap sebagai cache miss, sedangkan kegagalan menulis cache hanya dicatat sebagai warning. Data tetap dapat dibaca dari MySQL.

[VERIFY: services/kbbi-linker/app/cache.py:36-58]  
[VERIFY: services/kbbi-linker/app/cache.py:60-99]

## 8. Struktur Database

```text
kbbi_entries
 ├── id
 ├── kata
 ├── normalized_kata
 ├── bentuk_baku
 ├── kelas_kata
 └── dataset_version
        │
        │ 1:N
        ▼
kbbi_definitions
 ├── entry_id
 ├── urutan
 └── definisi

kbbi_poem_status
 ├── poem_id
 ├── status
 ├── deleted
 ├── analysis_at
 └── source_event_id
        │
        │ 1:N
        ▼
poem_terms ─────────► poetic_terms
                       ├── display_term
                       └── normalized_term
```

[VERIFY: services/kbbi-linker/app/db.py:18-61]

Database tidak menggandakan seluruh definisi KBBI untuk setiap puisi. `poem_terms` hanya menyimpan hubungan antara puisi dan istilah unik, sementara definisi disimpan terpisah.

[VERIFY: services/kbbi-linker/app/db.py:38-60]

`get_entries()` melakukan `LEFT JOIN` antara `kbbi_entries` dan `kbbi_definitions`, mengelompokkan beberapa definisi pada satu istilah, dan membatasi hasil berdasarkan `dataset_version`.

[VERIFY: services/kbbi-linker/app/db.py:214-239]

## 9. Connection Pool MySQL

Connection pool internal menggunakan:

- `BoundedSemaphore` untuk membatasi koneksi aktif;
- `LifoQueue` untuk menyimpan koneksi idle;
- timeout saat menunggu slot;
- `ping(reconnect=True)` ketika koneksi digunakan kembali; dan
- rollback apabila operasi gagal.

[VERIFY: services/kbbi-linker/app/db.py:68-115]

Konfigurasi default-nya adalah delapan koneksi dengan timeout dua detik.

[VERIFY: services/kbbi-linker/app/config.py:16-22]

Jika slot tidak tersedia, repository melempar `Unavailable`, lalu API menerjemahkannya menjadi HTTP 503.

[VERIFY: services/kbbi-linker/app/db.py:90-106]  
[VERIFY: services/kbbi-linker/app/main.py:104-105]

## 10. Observability

Modul menyediakan:

- `GET /health`;
- `GET /metrics`;
- penghitung request berdasarkan service, method, route, dan status;
- histogram durasi request;
- metrik cache hit/miss;
- metrik event berdasarkan nama dan outcome; dan
- log dengan `event_id` serta `correlation_id`.

[VERIFY: services/kbbi-linker/app/main.py:23-27]  
[VERIFY: services/kbbi-linker/app/main.py:39-62]  
[VERIFY: services/kbbi-linker/app/consumer.py:20-20]  
[VERIFY: services/kbbi-linker/app/consumer.py:82-104]

## 11. Kesimpulan

`kbbi-linker` merupakan read model berbasis event untuk fitur penjelasan kata puitis. Modul ini menerima hasil NLP secara asinkron, menormalisasi dan mendeduplikasi istilah, menjaga status pemrosesan per puisi, mencari definisi dari dataset KBBI, menggunakan Redis dengan pola cache-aside dan negative caching, serta menyediakan hasil melalui API yang dilindungi JWT.

Alur bisnis end-to-end modul ini adalah:

```text
event masuk → validasi → transaksi database → invalidasi cache
            → request API → cache/MySQL → respons definisi
```

Modul juga memiliki perlindungan terhadap duplicate dan out-of-order analysis event, retry dan DLQ untuk kegagalan consumer, serta metrics dan structured logging untuk observability.
