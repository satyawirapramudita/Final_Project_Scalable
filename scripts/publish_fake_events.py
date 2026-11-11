"""Kirim event puisi palsu ke indeks search (seed data, uji tanpa layanan lain).

Secara default event dikirim LANGSUNG ke antrean search.q (tanpa lewat exchange),
jadi nlp-worker, wordcloud, kbbi-linker, dan tts tidak ikut menerimanya. Dengan
--via-exchange event lewat poem.events seperti event asli.

Dijalankan di dalam container search (punya shared/ dan akses RabbitMQ):

    Get-Content scripts/publish_fake_events.py | docker compose exec -T search python - --count 1000 --user 99

Puisi palsu memakai id mulai --start-id (default 100000) agar tidak bentrok
dengan puisi asli. Isinya deterministik (seed tetap), jadi hasilnya bisa diulang.
Bersihkan setelah selesai:  DELETE FROM search_db.search_poem WHERE user_id = 99;
"""

from __future__ import annotations

import argparse
import json
import os
import random
import uuid
from datetime import datetime, timezone

import pika

from shared import events
from shared.rabbit import connect, declare_topology

KATA = (
    "senja laut rindu hujan malam bintang angin embun bulan sunyi kenangan pagi "
    "cakrawala ombak pantai gunung sungai daun kabut cahaya bayang mimpi harapan "
    "luka cinta waktu jalan langit awan api abu doa sepi fajar kunang rumput badai"
).split()
KATEGORI = ("Alam", "Cinta", "Sedih", "Rindu", "Harapan")
PENGHUBUNG = ("di", "dan", "yang", "pada", "tanpa", "bersama", "menuju")


def make_poem(rng: random.Random) -> tuple[str, str, str, list[str]]:
    words = rng.sample(KATA, 3)
    judul = " ".join(w.capitalize() for w in words[:2])
    baris = []
    for _ in range(3):
        isi = []
        for _ in range(rng.randint(5, 8)):
            isi.append(rng.choice(KATA) if rng.random() < 0.6 else rng.choice(PENGHUBUNG))
        baris.append(" ".join(isi).capitalize() + ".")
    tags = rng.sample(KATA, 4)
    return judul, "\n".join(baris), rng.choice(KATEGORI), tags


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--user", type=int, default=99)
    parser.add_argument("--start-id", type=int, default=100000)
    parser.add_argument("--no-analyze", action="store_true", help="jangan kirim poem.analyzed")
    parser.add_argument("--via-exchange", action="store_true", help="lewat poem.events, bukan langsung ke search.q")
    args = parser.parse_args()

    conn = connect(
        os.environ["RABBITMQ_HOST"],
        int(os.environ.get("RABBITMQ_PORT", "5672")),
        os.environ["RABBITMQ_USER"],
        os.environ["RABBITMQ_PASSWORD"],
        os.environ.get("RABBITMQ_VHOST", "/"),
    )
    ch = conn.channel()
    declare_topology(ch)
    ch.confirm_delivery()
    props = pika.BasicProperties(content_type="application/json", delivery_mode=2)

    def send(name: str, payload: dict) -> None:
        body = {
            "event_id": str(uuid.uuid4()),
            "correlation_id": "fake-seed",
            "event_name": name,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if args.via_exchange:
            ch.basic_publish(events.EXCHANGE, name, data, props)
        else:
            ch.basic_publish("", events.QUEUE_SEARCH, data, props)

    rng = random.Random(42)
    for i in range(args.count):
        poem_id = args.start_id + i
        judul, isi, kategori, tags = make_poem(rng)
        send(
            events.POEM_CREATED,
            events.poem_created_payload(
                poem_id=poem_id, user_id=args.user, judul=judul, isi=isi, kategori=kategori
            ),
        )
        if not args.no_analyze:
            send(
                events.POEM_ANALYZED,
                events.poem_analyzed_payload(poem_id=poem_id, tags=tags, kata_puitif=[], terms=[]),
            )
    conn.close()
    sent = args.count * (1 if args.no_analyze else 2)
    target = "poem.events" if args.via_exchange else events.QUEUE_SEARCH
    print(f"{sent} event untuk {args.count} puisi (user {args.user}) dikirim ke {target}")


if __name__ == "__main__":
    main()
