"""Periksa dan kirim ulang pesan dead-letter queue (DLQ).

Pesan masuk `<antrean>.dlq` setelah konsumen gagal memprosesnya berulang kali
(lihat shared/rabbit.start_consumer). Setelah penyebabnya diperbaiki, pesan
dapat dikirim ulang ke antrean asalnya. Pengiriman ulang aman karena konsumen
idempoten (event_id/occurred_at, upsert).

Pesan dikirim LANGSUNG ke antrean asal (exchange kosong + nama antrean), bukan
ke exchange poem.events, sehingga layanan lain tidak menerima event ganda.

Dijalankan di dalam container poem (punya akses RabbitMQ dan shared/):

    # ringkasan isi semua DLQ
    Get-Content scripts/replay_dlq.py | docker compose exec -T poem python -

    # lihat pesan di DLQ search.q tanpa mengubah apa pun
    Get-Content scripts/replay_dlq.py | docker compose exec -T poem python - --queue search.q --list

    # kirim ulang semua pesan DLQ search.q (atau --limit N)
    Get-Content scripts/replay_dlq.py | docker compose exec -T poem python - --queue search.q --replay

Di bash ganti `Get-Content scripts/replay_dlq.py |` dengan `< scripts/replay_dlq.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import pika

from shared import events
from shared.rabbit import connect, declare_topology


def _queues() -> list[str]:
    return sorted(events.queue_bindings())


def _death(props: pika.BasicProperties) -> str:
    deaths = (props.headers or {}).get("x-death") or []
    if not deaths:
        return "-"
    d = deaths[0]
    return f"reason={d.get('reason')} count={d.get('count')} dari={d.get('queue')}"


def _describe(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return f"(bukan JSON, {len(body)} byte)"
    payload = data.get("payload") or {}
    return (
        f"{data.get('event_name')} poem_id={payload.get('poem_id')} "
        f"event_id={data.get('event_id')} occurred_at={data.get('occurred_at')}"
    )


def _count(channel, queue: str) -> int:
    return channel.queue_declare(queue=queue, passive=True).method.message_count


def summary(channel) -> None:
    print(f"{'antrean':<14} {'pesan':>6} {'dlq':>6}")
    for q in _queues():
        print(f"{q:<14} {_count(channel, q):>6} {_count(channel, q + '.dlq'):>6}")


def list_messages(channel, queue: str, limit: int) -> None:
    dlq = f"{queue}.dlq"
    total = _count(channel, dlq)
    print(f"{dlq}: {total} pesan (menampilkan maks {limit})")
    shown = 0
    while shown < min(limit, total):
        method, props, body = channel.basic_get(dlq, auto_ack=False)
        if method is None:
            break
        shown += 1
        print(f"{shown:>4}. {_describe(body)} | {_death(props)}")
    # Tidak ada ack: semua pesan kembali ke DLQ saat channel ditutup.


def replay(channel, queue: str, limit: int | None) -> None:
    dlq = f"{queue}.dlq"
    # Batasi ke jumlah awal: pesan yang gagal lagi dan kembali ke DLQ selama
    # skrip berjalan tidak dikirim ulang berulang-ulang.
    todo = _count(channel, dlq)
    if limit is not None:
        todo = min(todo, limit)
    channel.confirm_delivery()
    sent = 0
    while sent < todo:
        method, props, body = channel.basic_get(dlq, auto_ack=False)
        if method is None:
            break
        channel.basic_publish(
            exchange="",
            routing_key=queue,
            body=body,
            properties=pika.BasicProperties(
                content_type=props.content_type or "application/json",
                delivery_mode=2,
                message_id=props.message_id,
                headers=props.headers,
            ),
        )
        # Ack DLQ baru setelah broker mengonfirmasi pesan di antrean asal.
        channel.basic_ack(method.delivery_tag)
        sent += 1
        print(f"  kirim ulang: {_describe(body)}")
    print(f"{sent} pesan dari {dlq} dikirim ulang ke {queue}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queue", help="antrean asal, mis. search.q (boleh juga search.q.dlq)")
    parser.add_argument("--list", action="store_true", help="tampilkan pesan DLQ tanpa mengubahnya")
    parser.add_argument("--replay", action="store_true", help="kirim ulang pesan DLQ ke antrean asal")
    parser.add_argument("--limit", type=int, default=None, help="jumlah pesan maksimum")
    args = parser.parse_args(sys.argv[1:])

    conn = connect(
        os.environ["RABBITMQ_HOST"],
        int(os.environ.get("RABBITMQ_PORT", "5672")),
        os.environ["RABBITMQ_USER"],
        os.environ["RABBITMQ_PASSWORD"],
        os.environ.get("RABBITMQ_VHOST", "/"),
    )
    channel = conn.channel()
    declare_topology(channel)

    try:
        if not args.queue:
            summary(channel)
            return
        queue = args.queue.removesuffix(".dlq")
        if queue not in _queues():
            parser.error(f"antrean tidak dikenal: {queue} (pilihan: {', '.join(_queues())})")
        if args.replay:
            replay(channel, queue, args.limit)
        else:
            list_messages(channel, queue, args.limit or 20)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
