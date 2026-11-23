"""Uji transactional outbox: event ditulis atomik bersama perubahan puisi,
dan relay mengirim/menandai baris dengan benar (termasuk saat RabbitMQ gagal)."""

from __future__ import annotations

import json

import pytest

from shared import events


def _envelopes(store) -> list[dict]:
    return [json.loads(r["envelope"]) for r in store["outbox"]]


POEM = {"judul": "Senja", "isi": "Senja di dermaga", "kategori": "alam"}


# ── Sisi penulis ──────────────────────────────────────────────


def test_create_writes_puisi_and_event_in_one_transaction(client, auth_header, fake_db):
    resp = client.post(
        "/api/poems", json=POEM, headers={**auth_header, "X-Request-ID": "rid-1"}
    )
    assert resp.status_code == 201
    poem_id = resp.json()["id_puisi"]

    [row] = fake_db["outbox"]
    [env] = _envelopes(fake_db)
    assert row["routing_key"] == events.POEM_CREATED
    assert row["event_id"] == env["event_id"]
    assert env["payload"]["poem_id"] == poem_id
    assert env["payload"]["isi"] == "Senja di dermaga"
    assert env["correlation_id"] == "rid-1"
    assert env["occurred_at"]
    assert fake_db["tx"] == ["begin", "commit"]


def test_update_and_delete_write_events(client, auth_header, fake_db, submitted_poem):
    client.put(f"/api/poems/{submitted_poem}", json={"judul": "Baru"}, headers=auth_header)
    client.delete(f"/api/poems/{submitted_poem}", headers=auth_header)

    keys = [r["routing_key"] for r in fake_db["outbox"]]
    assert keys == [events.POEM_CREATED, events.POEM_UPDATED, events.POEM_DELETED]
    updated = _envelopes(fake_db)[1]["payload"]
    assert updated["judul"] == "Baru"
    assert _envelopes(fake_db)[2]["payload"] == {"poem_id": submitted_poem}


def test_outbox_failure_rolls_back_poem(client, auth_header, fake_db, monkeypatch):
    """Bila event gagal ditulis, puisi juga tidak boleh tersimpan (atomik)."""
    from services.poem.app import outbox

    def boom(*_a, **_k):
        raise RuntimeError("disk penuh")

    monkeypatch.setattr(outbox, "add", boom)
    with pytest.raises(RuntimeError):
        client.post("/api/poems", json=POEM, headers=auth_header)

    assert fake_db["puisi"] == []
    assert fake_db["outbox"] == []
    assert fake_db["tx"] == ["begin", "rollback"]


def test_idempotent_replay_does_not_duplicate_event(client, auth_header, fake_db):
    headers = {**auth_header, "Idempotency-Key": "kunci-1"}
    first = client.post("/api/poems", json=POEM, headers=headers)
    second = client.post("/api/poems", json=POEM, headers=headers)

    assert second.headers.get("Idempotent-Replay") == "true"
    assert second.json() == first.json()
    assert len(fake_db["outbox"]) == 1


# ── Relay ─────────────────────────────────────────────────────


class FakePublisher:
    def __init__(self, fail_on: int | None = None) -> None:
        self.sent: list[tuple[str, dict, str]] = []
        self._fail_on = fail_on

    def publish_raw(self, routing_key: str, body: bytes, message_id: str) -> None:
        if self._fail_on is not None and len(self.sent) == self._fail_on:
            raise ConnectionError("RabbitMQ mati")
        self.sent.append((routing_key, json.loads(body), message_id))


def _seed(fake_db, n: int) -> None:
    from services.poem.app import db, outbox

    for i in range(1, n + 1):
        with db.transaction() as cur:
            outbox.add(cur, events.POEM_DELETED, {"poem_id": i}, None)


def test_relay_publishes_and_marks_rows(fake_db):
    from services.poem.app import outbox

    _seed(fake_db, 3)
    pub = FakePublisher()

    assert outbox.relay_once(pub) == 3
    assert [s[1]["payload"]["poem_id"] for s in pub.sent] == [1, 2, 3]
    # message_id = event_id agar konsumen dapat mendeteksi kiriman ulang.
    assert [s[2] for s in pub.sent] == [r["event_id"] for r in fake_db["outbox"]]
    assert all(r["published_at"] for r in fake_db["outbox"])
    assert "READ COMMITTED" in fake_db["isolation"]
    # Putaran berikutnya tidak mengirim ulang.
    assert outbox.relay_once(pub) == 0
    assert len(pub.sent) == 3


def test_relay_respects_batch_size(fake_db):
    from services.poem.app import outbox

    _seed(fake_db, 5)
    pub = FakePublisher()
    assert outbox.relay_once(pub, batch=2) == 2
    assert outbox.relay_once(pub, batch=2) == 2
    assert outbox.relay_once(pub, batch=2) == 1


def test_relay_failure_keeps_unsent_rows_pending(fake_db):
    """RabbitMQ mati di tengah batch: yang terkirim ditandai, sisanya menunggu."""
    from services.poem.app import outbox

    _seed(fake_db, 3)
    with pytest.raises(ConnectionError):
        outbox.relay_once(FakePublisher(fail_on=1))

    rows = fake_db["outbox"]
    assert rows[0]["published_at"] is not None
    assert rows[1]["published_at"] is None and rows[1]["attempts"] == 1
    assert "RabbitMQ mati" in rows[1]["last_error"]
    assert rows[2]["published_at"] is None and rows[2]["attempts"] == 0

    # RabbitMQ pulih: sisa event terkirim, tidak ada yang hilang.
    pub = FakePublisher()
    assert outbox.relay_once(pub) == 2
    assert [s[1]["payload"]["poem_id"] for s in pub.sent] == [2, 3]
