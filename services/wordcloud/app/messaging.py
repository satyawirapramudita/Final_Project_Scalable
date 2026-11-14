"""Integrasi RabbitMQ wordcloud: konsumsi poem.analyzed dan poem.deleted."""

from __future__ import annotations

import logging

from shared import events
from shared.rabbit import start_consumer

from . import config

log = logging.getLogger("wordcloud.messaging")


def start_wordcloud_consumer(handler) -> None:
    """Mulai consumer di background thread untuk queue wordcloud.q."""
    start_consumer(
        host=config.RABBITMQ_HOST,
        port=config.RABBITMQ_PORT,
        user=config.RABBITMQ_USER,
        password=config.RABBITMQ_PASSWORD,
        vhost=config.RABBITMQ_VHOST,
        queue=events.QUEUE_WORDCLOUD,
        handler=handler,
        prefetch=1,
    )
