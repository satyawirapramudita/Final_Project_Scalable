"""Integrasi RabbitMQ nlp-worker: konsumsi poem.created/updated, publish poem.analyzed."""

from __future__ import annotations

import logging
from typing import Callable

from shared import events
from shared.rabbit import Publisher, start_consumer

from . import config

log = logging.getLogger("nlp.messaging")
_publisher: Publisher | None = None


def _get_publisher() -> Publisher:
    global _publisher
    if _publisher is None:
        _publisher = Publisher(
            config.RABBITMQ_HOST,
            config.RABBITMQ_PORT,
            config.RABBITMQ_USER,
            config.RABBITMQ_PASSWORD,
            config.RABBITMQ_VHOST,
        )
    return _publisher


def publish(event_name: str, payload: dict, correlation_id: str | None = None) -> str:
    """Publish dengan publisher confirms. Kegagalan SENGAJA dilempar.

    Dulu exception ditelan dan pesan asal tetap di-ack, sehingga hasil analisis
    hilang diam-diam saat RabbitMQ bermasalah. Sekarang exception naik ke
    start_consumer: pesan asal diulang dengan backoff, lalu masuk DLQ bila
    tetap gagal. Analisis ulang aman karena konsumen poem.analyzed idempoten.
    """
    try:
        return _get_publisher().publish(event_name, payload, correlation_id)
    except Exception:
        log.exception("Gagal publish %s", event_name)
        raise


def start_created_consumer(handler: Callable[[dict], None]) -> None:
    start_consumer(
        host=config.RABBITMQ_HOST,
        port=config.RABBITMQ_PORT,
        user=config.RABBITMQ_USER,
        password=config.RABBITMQ_PASSWORD,
        vhost=config.RABBITMQ_VHOST,
        queue=events.QUEUE_NLP,
        handler=handler,
        prefetch=1,
    )
