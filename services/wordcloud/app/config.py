"""Konfigurasi wordcloud service dari environment variable."""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


SERVICE_NAME = os.getenv("SERVICE_NAME", "wordcloud")
PORT = _int("PORT", 8003)

# MySQL
DB_HOST     = os.getenv("WORDCLOUD_DB_HOST", "mysql")
DB_PORT     = _int("WORDCLOUD_DB_PORT", 3306)
DB_NAME     = os.getenv("WORDCLOUD_DB_NAME", "wordcloud_db")
DB_USER     = os.getenv("WORDCLOUD_DB_USER", "wordcloud_user")
DB_PASSWORD = os.getenv("WORDCLOUD_DB_PASSWORD", "")

# RabbitMQ
RABBITMQ_HOST     = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_PORT     = _int("RABBITMQ_PORT", 5672)
RABBITMQ_USER     = os.getenv("RABBITMQ_USER", "puisi")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "")
RABBITMQ_VHOST    = os.getenv("RABBITMQ_VHOST", "/")
