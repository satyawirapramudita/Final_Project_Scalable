# Kontrak Event — `poem.events`

**Pemilik:** Satya (poem + nlp-worker)
**Status:** dibekukan di fase F3. Perubahan bersifat *additive* (tambah field boleh, hapus/tubah tidak) dan harus disepakati semua pemilik konsumen.

Broker: **RabbitMQ**, satu topic exchange `poem.events`. Setiap konsumen punya queue durable sendiri.

## Routing key & konsumen

| Routing key | Producer | Konsumen (queue) | Payload inti |
|---|---|---|---|
| `poem.created` | poem (Satya) | nlp.q (Satya), search.q (Anders) | `event_id, correlation_id, poem_id, user_id, judul, isi, kategori, visibilitas, occurred_at` |
| `poem.updated` | poem (Satya) | nlp.q (Satya), search.q (Anders) | sama dengan `poem.created` |
| `poem.deleted` | poem (Satya) | search.q (Anders), wordcloud.q (Lindra, Anders), kbbi.q (Dzaky) | `event_id, poem_id, occurred_at` |
| `poem.analyzed` | nlp-worker (Satya) | poem.q (Satya), wordcloud.q (Lindra, Anders), kbbi.q (Dzaky), search.q (Anders) | `event_id, correlation_id, poem_id, tags[], kata_puitif[], terms[{term,count}], occurred_at` |

## Visibilitas puisi (`visibilitas`)

Ditambahkan secara *additive* ke `poem.created` dan `poem.updated`: `"public"` atau `"private"`.

- Field tidak ada (event lama) berarti **publik**: semua puisi sebelum fitur ini memang publik.
- Nilai yang tidak dikenal diperlakukan sebagai **privat** (gagal tertutup). Pakai
  `shared.events.parse_visibility()` agar semua konsumen konsisten.
- Puisi privat hanya boleh terlihat oleh pemiliknya (`user_id`). Pengguna lain mendapat
  **404**, bukan 403, agar keberadaannya tidak terungkap.
- `poem.analyzed` **tidak** membawa visibilitas. Data turunan (word cloud, KBBI) dilindungi di
  **gateway**: sebelum meneruskan `/api/wordcloud/{id}` dan `/api/kbbi/{id}`, Nginx memanggil
  `GET /internal/poems/{id}/access` di layanan poem (`auth_request`). Layanan wordcloud dan
  kbbi-linker tidak perlu tahu soal visibilitas.
- search menyimpan visibilitas bersama konten (cap waktu `content_at` yang sama), menyaring
  `visibilitas = 'public' OR user_id = <pengguna>` di SQL, dan memasukkan id pengguna ke kunci cache.

## Amplop standar

Semua event membawa:

```json
{
  "event_id": "<uuid>",
  "correlation_id": "<uuid dari X-Request-ID gateway>",
  "event_name": "poem.analyzed",
  "occurred_at": "2026-11-07T10:15:00+00:00",
  "payload": { }
}
```

## Contoh `poem.analyzed`

```json
{
  "event_id": "7c1f0a52-3b0e-4b9e-9d1a-0c2f6c8e1a11",
  "correlation_id": "e2a4c9d0-55b1-4f0a-8f3e-2b7d9c1a4e66",
  "event_name": "poem.analyzed",
  "occurred_at": "2026-11-07T10:15:00Z",
  "payload": {
    "poem_id": 42,
    "tags": ["alam", "rindu"],
    "kata_puitif": ["senja", "cakrawala"],
    "terms": [
      { "term": "senja", "count": 3 },
      { "term": "langit", "count": 2 }
    ]
  }
}
```

## Aturan kontrak

1. Setiap event membawa `event_id` (UUID) dan `correlation_id` agar konsumen dapat idempoten dan log dapat dilacak lintas layanan.
2. Konsumen memakai **manual acknowledgement** dan `prefetch_count` kecil; pesan baru di-ack setelah hasil tersimpan.
3. Pesan gagal diulang dengan **backoff**, lalu dipindahkan ke **dead-letter queue** (`*.dlq`) setelah batas percobaan.
4. Perubahan skema bersifat **additive**.
5. Idempotency: dedupe `event_id` atau upsert berdasarkan `poem_id`.
6. Urutan: konsumen membandingkan `occurred_at` dengan event terakhir yang diterapkan untuk puisi itu dan
   mengabaikan event yang lebih lama; `poem.deleted` meninggalkan tombstone sehingga `poem.analyzed` yang
   terlambat tidak menghidupkan data lagi (search, wordcloud, kbbi-linker).
7. **Transactional outbox (poem).** `poem.created/updated/deleted` ditulis ke tabel `poem_db.outbox` dalam
   transaksi yang sama dengan perubahan puisi; relay mengirimnya kemudian. Akibatnya:
   - `event_id` dan `occurred_at` ditetapkan saat perubahan di-commit, bukan saat dikirim, jadi aturan 6 tetap benar
     walau event tertahan beberapa saat (mis. RabbitMQ mati).
   - Pengiriman *at-least-once*: event yang sama (dengan `event_id` dan `message_id` AMQP yang sama) bisa tiba
     dua kali bila relay mati setelah publish tetapi sebelum menandai baris; konsumen wajib idempoten (aturan 5).
   - Relay di beberapa replika poem berbagi baris dengan `SKIP LOCKED`, jadi urutan kirim antar-puisi tidak dijamin;
     konsumen mengandalkan `occurred_at`, bukan urutan kedatangan.

Definisi kode: [`shared/events.py`](../shared/events.py).
