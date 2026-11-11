"""Uji end-to-end: event RabbitMQ -> consumer -> MySQL -> Redis -> API HTTP.

Ini uji yang membuktikan rantai utuh kbbi-linker bekerja, bukan hanya satuannya.
Jalankan handler di thread sendiri sehingga tidak perlu container service hidup.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from tests.live.conftest import requires_live

pytestmark = requires_live

T0 = datetime(2026, 11, 10, 8, 0, 0)


def _seed_entry(normalized: str, *, bentuk_baku: str | None = None, kelas: str = "nomina",
                definisi: list[str] | None = None) -> None:
    from app.db import pool

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kbbi_entries (kata,normalized_kata,bentuk_baku,kelas_kata,dataset_version) "
            "VALUES (%s,%s,%s,%s,%s)",
            (normalized, normalized, bentuk_baku, kelas, "kbbi-v-live"),
        )
        entry_id = cur.lastrowid
        for order, text in enumerate(definisi or [], start=1):
            cur.execute(
                "INSERT INTO kbbi_definitions (entry_id,urutan,definisi) VALUES (%s,%s,%s)",
                (entry_id, order, text),
            )
        conn.commit()


@pytest.fixture
def api_client(schema, clean_redis_module):
    """TestClient FastAPI tanpa lifespan (tidak memulai konsumen sendiri)."""
    from fastapi.testclient import TestClient

    from app import main
    from app.security import current_user

    main.app.dependency_overrides[current_user] = lambda: {"sub": "1"}
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


@pytest.fixture
def clean_redis_module(flush_cache):
    return flush_cache


@pytest.fixture
def token():
    from shared.security import create_token

    return create_token(user_id=1, username="dzaky")


# ─────────────────────────── HTTP: batas autentikasi ───────────────────────────


def test_health_is_public(api_client) -> None:
    response = api_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "kbbi-linker"}


def test_metrics_is_public_and_exposes_counters(api_client) -> None:
    api_client.get("/health")

    response = api_client.get("/metrics")

    assert response.status_code == 200
    assert "http_requests_total" in response.text


def test_missing_authorization_header_is_401(api_client) -> None:
    from app import main

    main.app.dependency_overrides.clear()
    response = api_client.get("/api/kbbi/1")

    assert response.status_code == 401
    assert response.json()["detail"] == "Token tidak ada atau format salah"


def test_wrong_scheme_is_401(api_client) -> None:
    from app import main

    main.app.dependency_overrides.clear()
    response = api_client.get("/api/kbbi/1", headers={"Authorization": "Basic abc"})

    assert response.status_code == 401


def test_garbage_token_is_401(api_client) -> None:
    from app import main

    main.app.dependency_overrides.clear()
    response = api_client.get("/api/kbbi/1", headers={"Authorization": "Bearer not.a.jwt"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Token tidak valid atau kedaluwarsa"


def test_token_signed_with_wrong_secret_is_401(api_client) -> None:
    import jwt

    from app import main

    main.app.dependency_overrides.clear()
    forged = jwt.encode({"sub": "1", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, "salah", algorithm="HS256")
    response = api_client.get("/api/kbbi/1", headers={"Authorization": f"Bearer {forged}"})

    assert response.status_code == 401


def test_expired_token_is_401(api_client) -> None:
    from shared.security import create_token

    from app import main

    main.app.dependency_overrides.clear()
    expired = create_token(
        user_id=1, username="dzaky", expires_minutes=-5
    )
    response = api_client.get("/api/kbbi/1", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401


def test_valid_token_is_accepted(api_client, token) -> None:
    from app import main

    main.app.dependency_overrides.clear()
    response = api_client.get("/api/kbbi/999", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["status"] == "pending"


def test_lowercase_bearer_prefix_is_accepted(api_client, token) -> None:
    from app import main

    main.app.dependency_overrides.clear()
    response = api_client.get("/api/kbbi/999", headers={"Authorization": f"bearer {token}"})

    assert response.status_code == 200


def test_sql_injection_in_path_is_rejected_by_validation(api_client) -> None:
    response = api_client.get("/api/kbbi/1%20OR%201=1")

    assert response.status_code == 422


@pytest.mark.parametrize("poem_id", [0, -1, -999])
def test_non_positive_poem_id_is_422(api_client, poem_id) -> None:
    response = api_client.get(f"/api/kbbi/{poem_id}")

    assert response.status_code == 422


def test_router_template_label_avoids_cardinality_explosion(api_client) -> None:
    """Label path harus template route, bukan URL mentah berisi id."""
    api_client.get("/api/kbbi/999")

    body = api_client.get("/metrics").text
    histogram = [line for line in body.splitlines() if line.startswith("http_request_duration_seconds_count")]

    assert histogram
    assert any('path="/api/kbbi/{poem_id}"' in line for line in histogram)
    assert not any("/api/kbbi/999" in line for line in histogram)


# ─────────────────────────── pipeline: consumer -> DB -> HTTP ───────────────────────────


def _envelope(event_name: str, payload: dict, occurred_at: datetime | None = None) -> dict:
    import uuid

    return {
        "event_id": str(uuid.uuid4()),
        "event_name": event_name,
        "occurred_at": (occurred_at or T0).replace(tzinfo=timezone.utc).isoformat(),
        "payload": payload,
    }


def test_analyzed_event_makes_poem_available_with_definitions(api_client, schema) -> None:
    """Rantai penuh: event analyzed -> DB -> API mengembalikan definisi untuk tooltip."""
    from app import consumer
    from app.cache import cache
    from app.db import repository

    _seed_entry("senja", kelas="nomina", definisi=["waktu setelah matahari terbenam"])
    _seed_entry("membentang", bentuk_baku="bentang", kelas="verba", definisi=["tampak terhampar"])

    consumer.apply_event(
        _envelope("poem.analyzed", {"poem_id": 42, "kata_puitif": ["Senja", "Membentang", "Temaram"]}),
        repository=repository, cache_backend=cache, max_terms=30,
    )

    body = api_client.get("/api/kbbi/42").json()

    assert body["status"] == "ready"
    assert body["dataset_version"] == "kbbi-v-live"
    by_term = {item["normalized_term"]: item for item in body["items"]}
    assert by_term["senja"]["found"] is True
    assert by_term["senja"]["kelas_kata"] == "nomina"
    assert by_term["senja"]["definisi"] == ["waktu setelah matahari terbenam"]
    assert by_term["membentang"]["bentuk_baku"] == "bentang"
    assert by_term["temaram"]["found"] is False
    assert by_term["temaram"]["definisi"] == []


def test_deleted_event_turns_endpoint_into_404(api_client, schema) -> None:
    from app import consumer
    from app.cache import cache
    from app.db import repository

    consumer.apply_event(
        _envelope("poem.analyzed", {"poem_id": 42, "kata_puitif": ["Senja"]}),
        repository=repository, cache_backend=cache, max_terms=30,
    )
    assert api_client.get("/api/kbbi/42").status_code == 200

    consumer.apply_event(
        _envelope("poem.deleted", {"poem_id": 42}, occurred_at=T0 + timedelta(hours=1)),
        repository=repository, cache_backend=cache, max_terms=30,
    )

    assert api_client.get("/api/kbbi/42").status_code == 404


def test_delete_event_invalidates_warm_cache(api_client, schema) -> None:
    """Cache puisi yang sudah hangat harus ditembus tombstone, bukan menyajikan data lama."""
    from app import consumer
    from app.cache import cache
    from app.db import repository

    consumer.apply_event(
        _envelope("poem.analyzed", {"poem_id": 42, "kata_puitif": ["Senja"]}),
        repository=repository, cache_backend=cache, max_terms=30,
    )
    api_client.get("/api/kbbi/42")  # hangatkan cache
    assert cache.get_poem(42) is not None

    consumer.apply_event(
        _envelope("poem.deleted", {"poem_id": 42}, occurred_at=T0 + timedelta(hours=1)),
        repository=repository, cache_backend=cache, max_terms=30,
    )

    assert cache.get_poem(42) is None
    assert api_client.get("/api/kbbi/42").status_code == 404


def test_second_request_serves_from_cache(api_client, schema) -> None:
    from app import consumer
    from app.cache import cache
    from app.db import repository

    consumer.apply_event(
        _envelope("poem.analyzed", {"poem_id": 42, "kata_puitif": ["Senja"]}),
        repository=repository, cache_backend=cache, max_terms=30,
    )

    assert api_client.get("/api/kbbi/42").json()["cached"] is False
    assert api_client.get("/api/kbbi/42").json()["cached"] is True


def test_term_limit_is_enforced_from_config(api_client, schema) -> None:
    from app import consumer
    from app.cache import cache
    from app.db import repository

    many = [f"kata{i}" for i in range(100)]
    consumer.apply_event(
        _envelope("poem.analyzed", {"poem_id": 42, "kata_puitif": many}),
        repository=repository, cache_backend=cache, max_terms=5,
    )

    assert len(api_client.get("/api/kbbi/42").json()["items"]) == 5


def test_reanalysis_through_api_shows_latest_terms(api_client, schema) -> None:
    from app import consumer
    from app.cache import cache
    from app.db import repository

    consumer.apply_event(
        _envelope("poem.analyzed", {"poem_id": 42, "kata_puitif": ["Senja"]}),
        repository=repository, cache_backend=cache, max_terms=30,
    )
    consumer.apply_event(
        _envelope("poem.analyzed", {"poem_id": 42, "kata_puitif": ["Cakrawala"]}, occurred_at=T0 + timedelta(hours=1)),
        repository=repository, cache_backend=cache, max_terms=30,
    )

    items = api_client.get("/api/kbbi/42").json()["items"]

    assert [i["normalized_term"] for i in items] == ["cakrawala"]


def test_api_degrades_to_503_when_database_is_unreachable(monkeypatch, token) -> None:
    """Pool penuh / DB mati harus jadi 503, bukan 500 dengan stack trace."""
    from fastapi.testclient import TestClient

    from app import main
    from app.db import Unavailable

    main.app.dependency_overrides.clear()

    def explode(_poem_id):
        raise Unavailable("database pool penuh")

    monkeypatch.setattr(main.cache, "get_poem", lambda _poem_id: None)
    monkeypatch.setattr(main.repository, "get_poem", explode)

    response = TestClient(main.app).get("/api/kbbi/42", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Layanan KBBI sedang sibuk"


# ─────────────────────────── pipeline: RabbitMQ sungguhan ───────────────────────────


def test_event_published_to_rabbitmq_reaches_handler(schema, clean_redis_module) -> None:
    """Bukti topologi: publish ke exchange benar-benar sampai ke konsumen kbbi.q."""
    import pika

    from app import config, consumer
    from shared import events
    from shared.rabbit import Publisher, declare_topology

    received: list[dict] = []
    done = threading.Event()

    params = pika.ConnectionParameters(
        host=config.RABBITMQ_HOST,
        port=config.RABBITMQ_PORT,
        virtual_host=config.RABBITMQ_VHOST or "/",
        credentials=pika.PlainCredentials(config.RABBITMQ_USER, config.RABBITMQ_PASSWORD),
        heartbeat=60,
    )

    def run() -> None:
        conn = pika.BlockingConnection(params)
        channel = conn.channel()
        declare_topology(channel)
        channel.basic_qos(prefetch_count=1)

        def on_message(ch, method, _props, body) -> None:
            received.append(json.loads(body.decode("utf-8")))
            ch.basic_ack(method.delivery_tag)
            ch.stop_consuming()

        channel.basic_consume(queue=events.QUEUE_KBBI, on_message_callback=on_message)
        channel.connection.call_later(20, lambda: channel.stop_consuming())
        channel.start_consuming()
        conn.close()
        done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    time.sleep(2)

    publisher = Publisher(
        config.RABBITMQ_HOST, config.RABBITMQ_PORT,
        config.RABBITMQ_USER, config.RABBITMQ_PASSWORD, config.RABBITMQ_VHOST,
    )
    event_id = publisher.publish(
        events.POEM_ANALYZED,
        {"poem_id": 1234, "tags": [], "kata_puitif": ["Senja"], "terms": []},
    )

    assert done.wait(timeout=25), "konsumen tidak menerima event"
    assert len(received) == 1
    envelope = received[0]
    assert envelope["event_id"] == event_id
    assert envelope["event_name"] == events.POEM_ANALYZED
    assert envelope["payload"]["poem_id"] == 1234


def test_kbbi_queue_binds_analyzed_and_deleted_only(schema) -> None:
    """Kontrak topologi: kbbi.q tidak boleh kebanjiran poem.created/updated."""
    import pika

    from app import config
    from shared import events
    from shared.rabbit import declare_topology

    params = pika.ConnectionParameters(
        host=config.RABBITMQ_HOST,
        port=config.RABBITMQ_PORT,
        credentials=pika.PlainCredentials(config.RABBITMQ_USER, config.RABBITMQ_PASSWORD),
        connection_attempts=3,
    )
    conn = pika.BlockingConnection(params)
    channel = conn.channel()
    declare_topology(channel)

    expected = {
        events.POEM_ANALYZED: True,
        events.POEM_DELETED: True,
        events.POEM_CREATED: False,
    }
    for key, should_arrive in expected.items():
        channel.queue_purge(events.QUEUE_KBBI)
        channel.basic_publish(
            exchange=events.EXCHANGE, routing_key=key, body=b"{}",
            properties=pika.BasicProperties(content_type="application/json"),
        )
        deadline = time.time() + 5
        delivered = False
        while time.time() < deadline:
            method, _, _body = channel.basic_get(queue=events.QUEUE_KBBI)
            if method is not None:
                channel.basic_ack(method.delivery_tag)
                delivered = True
                break
            time.sleep(0.1)
        assert delivered is should_arrive, (
            f"{key} -> kbbi.q: diharapkan {should_arrive}, dapat {delivered}"
        )
    conn.close()
