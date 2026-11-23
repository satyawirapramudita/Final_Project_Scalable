"""Integrasi RabbitMQ poem service: konsumsi poem.analyzed.

Publish event TIDAK dilakukan di sini lagi: endpoint menulis event ke tabel
outbox dalam transaksi yang sama, dan relay di app/outbox.py yang mengirimnya.
"""

from __future__ import annotations

from typing import Callable

from shared import events
from shared.rabbit import start_consumer

from . import config


def start_analyzed_consumer(handler: Callable[[dict], None]) -> None:
    start_consumer(
        host=config.RABBITMQ_HOST,
        port=config.RABBITMQ_PORT,
        user=config.RABBITMQ_USER,
        password=config.RABBITMQ_PASSWORD,
        vhost=config.RABBITMQ_VHOST,
        queue=events.QUEUE_POEM,
        handler=handler,
        prefetch=5,
    )
