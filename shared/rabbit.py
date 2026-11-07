"""Helper RabbitMQ bersama.

Menyediakan koneksi dengan retry, deklarasi topologi (exchange, queue,
DLQ, binding), publisher thread-safe, dan consumer dengan retry + DLQ.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

import pika

from shared import events

log = logging.getLogger("shared.rabbit")


def _params(host: str, port: int, user: str, password: str, vhost: str) -> pika.ConnectionParameters:
    return pika.ConnectionParameters(
        host=host,
        port=port,
        virtual_host=vhost or "/",
        credentials=pika.PlainCredentials(user, password),
        heartbeat=60,
        blocked_connection_timeout=30,
        connection_attempts=3,
        retry_delay=2,
    )


def connect(
    host: str,
    port: int,
    user: str,
    password: str,
    vhost: str = "/",
    *,
    retries: int = 30,
    delay: int = 2,
) -> pika.BlockingConnection:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return pika.BlockingConnection(_params(host, port, user, password, vhost))
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("RabbitMQ belum siap (%s/%s): %s", attempt, retries, exc)
            time.sleep(delay)
    raise RuntimeError(f"Tidak dapat terhubung ke RabbitMQ: {last}")


def declare_topology(channel: pika.adapters.blocking_connection.BlockingChannel) -> None:
    """Deklarasikan exchange, queue durable, DLQ, dan binding (idempoten)."""
    channel.exchange_declare(exchange=events.EXCHANGE, exchange_type="topic", durable=True)
    channel.exchange_declare(exchange=events.DLX, exchange_type="topic", durable=True)

    for queue, keys in events.queue_bindings().items():
        channel.queue_declare(
            queue=queue,
            durable=True,
            arguments={
                "x-dead-letter-exchange": events.DLX,
                "x-dead-letter-routing-key": f"{queue}.dlq",
            },
        )
        for key in keys:
            channel.queue_bind(exchange=events.EXCHANGE, queue=queue, routing_key=key)

        dlq = f"{queue}.dlq"
        channel.queue_declare(queue=dlq, durable=True)
        channel.queue_bind(exchange=events.DLX, queue=dlq, routing_key=dlq)


class Publisher:
    """Publisher thread-safe dengan publisher confirms dan reconnect."""

    def __init__(self, host: str, port: int, user: str, password: str, vhost: str = "/") -> None:
        self._cfg = (host, port, user, password, vhost)
        self._lock = threading.Lock()
        self._conn: pika.BlockingConnection | None = None
        self._ch = None

    def _ensure(self) -> None:
        if self._conn is None or self._conn.is_closed:
            self._conn = connect(*self._cfg)
            self._ch = self._conn.channel()
            self._ch.confirm_delivery()
            declare_topology(self._ch)

    def publish(self, event_name: str, payload: dict, correlation_id: str | None = None) -> str:
        envelope = events.EventEnvelope(
            event_name=event_name, payload=payload, correlation_id=correlation_id
        )
        body = json.dumps(envelope.to_dict(), ensure_ascii=False).encode("utf-8")
        with self._lock:
            for attempt in range(2):
                try:
                    self._ensure()
                    self._ch.basic_publish(
                        exchange=events.EXCHANGE,
                        routing_key=event_name,
                        body=body,
                        properties=pika.BasicProperties(
                            content_type="application/json",
                            delivery_mode=2,
                            message_id=envelope.event_id,
                        ),
                    )
                    return envelope.event_id
                except Exception:  # noqa: BLE001
                    self._conn = None
                    if attempt == 1:
                        raise
        return envelope.event_id


def start_consumer(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    vhost: str = "/",
    queue: str,
    handler: Callable[[dict], None],
    prefetch: int = 1,
    max_retries: int = 3,
) -> threading.Thread:
    """Jalankan consumer di thread daemon. Retry backoff, lalu DLQ."""

    def run() -> None:
        while True:
            try:
                conn = connect(host, port, user, password, vhost)
                channel = conn.channel()
                declare_topology(channel)
                channel.basic_qos(prefetch_count=prefetch)

                def on_message(ch, method, _properties, body) -> None:
                    try:
                        data = json.loads(body.decode("utf-8"))
                    except Exception:  # noqa: BLE001
                        log.exception("Payload tidak valid -> DLQ")
                        ch.basic_nack(method.delivery_tag, requeue=False)
                        return
                    for attempt in range(1, max_retries + 1):
                        try:
                            handler(data)
                            ch.basic_ack(method.delivery_tag)
                            return
                        except Exception as exc:  # noqa: BLE001
                            log.warning("handler gagal (%s/%s): %s", attempt, max_retries, exc)
                            if attempt < max_retries:
                                time.sleep(2**attempt)
                    log.error("handler gagal total -> DLQ")
                    ch.basic_nack(method.delivery_tag, requeue=False)

                channel.basic_consume(queue=queue, on_message_callback=on_message)
                log.info("Konsumen %s aktif (prefetch=%s)", queue, prefetch)
                channel.start_consuming()
            except Exception:  # noqa: BLE001
                log.exception("Koneksi konsumen %s terputus, reconnect dalam 3 dtk...", queue)
                time.sleep(3)

    thread = threading.Thread(target=run, name=f"consumer-{queue}", daemon=True)
    thread.start()
    return thread
