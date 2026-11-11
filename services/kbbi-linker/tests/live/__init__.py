"""Uji integrasi live untuk kbbi-linker (MySQL + Redis + RabbitMQ nyata).

Jalankan:
    docker compose -f services/kbbi-linker/tests/live/compose.yml up -d
    $env:KBBI_LIVE="1"
    python -m pytest services/kbbi-linker/tests/live -q
"""

from __future__ import annotations
