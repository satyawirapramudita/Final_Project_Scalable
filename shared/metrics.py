"""Metrik HTTP bersama: histogram latensi + label path yang aman.

Dipakai middleware tiap layanan HTTP:

    from shared.metrics import REQUEST_SECONDS, route_path

    started = time.perf_counter()
    response = await call_next(request)
    path = route_path(request)
    REQUEST_SECONDS.labels(SERVICE_NAME, request.method, path).observe(time.perf_counter() - started)

Label `path` memakai TEMPLATE route (mis. /api/poems/{poem_id}), bukan URL mentah.
URL mentah (/api/poems/1, /api/poems/2, ...) membuat satu deret waktu baru per id
dan memenuhi memori Prometheus.
"""

from __future__ import annotations

from prometheus_client import Histogram

REQUEST_SECONDS = Histogram(
    "http_request_duration_seconds",
    "Latensi request HTTP",
    ["service", "method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)


def route_path(request) -> str:
    """Template route yang cocok dengan request, atau 'unmatched' (404, dsb.)."""
    route = request.scope.get("route")
    return getattr(route, "path", "unmatched")
