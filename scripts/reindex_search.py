"""Bangun ulang indeks pencarian dari poem_db.

Indeks search adalah data turunan: bila baru dibuat, dikosongkan, atau event
sempat terlewat, jalankan skrip ini. Skrip membaca semua puisi dari poem_db
lalu mengirim event poem.created (dan poem.analyzed untuk yang sudah punya tag)
LANGSUNG ke antrean search.q, jadi layanan lain tidak menerima event tambahan.

Cap waktu event diambil dari data puisi (tgl_submit/updated_at), sehingga event
asli yang lebih baru tetap menang atas hasil reindex (lihat engine.py).
Puisi yang sudah dihapus tidak ada di poem_db dan tidak bisa dideteksi di sini.

Dijalankan di dalam container poem (punya akses poem_db, RabbitMQ, dan shared/):

    Get-Content scripts/reindex_search.py | docker compose exec -T poem python -
"""

from __future__ import annotations

import json
import os
import uuid

import pika
import pymysql
from pymysql.cursors import DictCursor

from shared import events
from shared.rabbit import connect, declare_topology


def _iso(value) -> str:
    return value.isoformat() if value is not None else ""


def _body(name: str, payload: dict, at: str) -> bytes:
    envelope = {
        "event_id": str(uuid.uuid4()),
        "correlation_id": "reindex",
        "event_name": name,
        "occurred_at": at,
        "payload": payload,
    }
    return json.dumps(envelope, ensure_ascii=False).encode("utf-8")


def main() -> None:
    db = pymysql.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        cursorclass=DictCursor,
        charset="utf8mb4",
    )
    with db.cursor() as cur:
        cur.execute("SELECT id, user_id, judul, isi, kategori, status, tgl_submit, updated_at FROM puisi")
        poems = cur.fetchall()
        cur.execute("SELECT puisi_id, tag FROM puisi_tag ORDER BY tag")
        tag_rows = cur.fetchall()
    db.close()

    tags: dict[int, list[str]] = {}
    for row in tag_rows:
        tags.setdefault(row["puisi_id"], []).append(row["tag"])

    conn = connect(
        os.environ["RABBITMQ_HOST"],
        int(os.environ.get("RABBITMQ_PORT", "5672")),
        os.environ["RABBITMQ_USER"],
        os.environ["RABBITMQ_PASSWORD"],
        os.environ.get("RABBITMQ_VHOST", "/"),
    )
    channel = conn.channel()
    declare_topology(channel)
    channel.confirm_delivery()
    props = pika.BasicProperties(content_type="application/json", delivery_mode=2)

    def send(name: str, payload: dict, at: str) -> None:
        # exchange kosong + routing key = nama queue: hanya search.q yang menerima
        channel.basic_publish(
            exchange="", routing_key=events.QUEUE_SEARCH, body=_body(name, payload, at), properties=props
        )

    sent_created = sent_analyzed = 0
    for poem in poems:
        at = _iso(poem["updated_at"] or poem["tgl_submit"])
        send(
            events.POEM_CREATED,
            events.poem_created_payload(
                poem_id=poem["id"],
                user_id=poem["user_id"],
                judul=poem["judul"],
                isi=poem["isi"],
                kategori=poem["kategori"],
            ),
            at,
        )
        sent_created += 1
        if poem["status"] == "done":
            send(
                events.POEM_ANALYZED,
                events.poem_analyzed_payload(
                    poem_id=poem["id"], tags=tags.get(poem["id"], []), kata_puitif=[], terms=[]
                ),
                at,
            )
            sent_analyzed += 1

    conn.close()
    print(f"reindex: {sent_created} poem.created dan {sent_analyzed} poem.analyzed dikirim ke {events.QUEUE_SEARCH}")


if __name__ == "__main__":
    main()
