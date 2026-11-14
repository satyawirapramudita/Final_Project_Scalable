"""Analisis ulang semua puisi agar word cloud, KBBI, tag, dan indeks search terisi.

Dipakai bila layanan konsumen baru ditambahkan setelah puisi sudah ada (mis.
wordcloud/kbbi-linker baru di-deploy, atau dataset KBBI baru di-import), atau
bila event sempat masuk DLQ. Skrip membaca semua puisi dari poem_db dan mengirim
event poem.updated LANGSUNG ke antrean nlp.q. nlp-worker lalu mem-publish
poem.analyzed lewat exchange seperti biasa, sehingga poem, search, wordcloud,
dan kbbi-linker memperbarui datanya. Semua konsumen idempoten, jadi aman diulang.

Dijalankan di dalam container poem (punya akses poem_db, RabbitMQ, dan shared/):

    Get-Content scripts/reanalyze_poems.py | docker compose exec -T poem python -
    Get-Content scripts/reanalyze_poems.py | docker compose exec -T poem python - 12 15   # hanya id 12 dan 15
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone

import pika
import pymysql
from pymysql.cursors import DictCursor

from shared import events
from shared.rabbit import connect, declare_topology


def main(ids: list[int]) -> None:
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
        sql = "SELECT id, user_id, judul, isi, kategori, keyword FROM puisi"
        if ids:
            cur.execute(sql + f" WHERE id IN ({','.join(['%s'] * len(ids))})", ids)
        else:
            cur.execute(sql)
        poems = cur.fetchall()
    db.close()

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

    for poem in poems:
        payload = events.poem_created_payload(
            poem_id=poem["id"],
            user_id=poem["user_id"],
            judul=poem["judul"],
            isi=poem["isi"],
            kategori=poem["kategori"] or "",
        )
        payload["keyword"] = poem.get("keyword") or ""
        envelope = {
            "event_id": str(uuid.uuid4()),
            "correlation_id": "reanalyze",
            "event_name": events.POEM_UPDATED,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        # exchange kosong + routing key = nama queue: hanya nlp-worker yang menerima
        channel.basic_publish(
            exchange="",
            routing_key=events.QUEUE_NLP,
            body=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
            properties=props,
        )
    conn.close()
    print(f"reanalyze: {len(poems)} puisi dikirim ke {events.QUEUE_NLP}")


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]])
