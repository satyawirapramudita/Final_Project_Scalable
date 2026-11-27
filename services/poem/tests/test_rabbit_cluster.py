"""Uji konfigurasi klien RabbitMQ untuk klaster (shared/rabbit.py)."""

from __future__ import annotations

import pytest

from shared import events, rabbit


def test_hosts_dipecah_dari_koma():
    assert rabbit.hosts("rabbit-1, rabbit-2,,rabbit-3 ") == ["rabbit-1", "rabbit-2", "rabbit-3"]
    assert rabbit.hosts("rabbitmq") == ["rabbitmq"]


def test_params_satu_parameter_per_node_dan_cepat_pindah():
    params = rabbit._params("rabbit-1,rabbit-2,rabbit-3", 5672, "u", "p", "/")
    assert sorted(p.host for p in params) == ["rabbit-1", "rabbit-2", "rabbit-3"]
    # Banyak node: satu percobaan per node, agar node mati tidak menahan lama.
    assert all(p.connection_attempts == 1 for p in params)


def test_params_host_dengan_port():
    params = rabbit._params("10.0.0.5:5672,10.0.0.5:5673,10.0.0.5:5674", 5672, "u", "p", "/")
    assert sorted((p.host, p.port) for p in params) == [
        ("10.0.0.5", 5672), ("10.0.0.5", 5673), ("10.0.0.5", 5674)
    ]
    (param,) = rabbit._params("rabbit-1", 5672, "u", "p", "/")
    assert (param.host, param.port) == ("rabbit-1", 5672)


def test_params_satu_node_tetap_mengulang():
    (param,) = rabbit._params("rabbitmq", 5672, "u", "p", "/")
    assert param.host == "rabbitmq"
    assert param.connection_attempts == 3


def test_params_host_kosong_ditolak():
    with pytest.raises(ValueError):
        rabbit._params(" , ", 5672, "u", "p", "/")


def test_queue_arguments_classic_default(monkeypatch):
    monkeypatch.delenv("RABBITMQ_QUEUE_TYPE", raising=False)
    assert rabbit.queue_arguments() == {}
    assert rabbit.queue_arguments("nlp.q.dlq") == {
        "x-dead-letter-exchange": events.DLX,
        "x-dead-letter-routing-key": "nlp.q.dlq",
    }


def test_queue_arguments_quorum(monkeypatch):
    monkeypatch.setenv("RABBITMQ_QUEUE_TYPE", "quorum")
    assert rabbit.queue_arguments() == {"x-queue-type": "quorum"}
    assert rabbit.queue_arguments("nlp.q.dlq")["x-queue-type"] == "quorum"


def test_queue_type_tidak_dikenal_ditolak(monkeypatch):
    monkeypatch.setenv("RABBITMQ_QUEUE_TYPE", "stream")
    with pytest.raises(ValueError):
        rabbit.queue_arguments()


class _Channel:
    def __init__(self):
        self.declared: dict[str, dict] = {}

    def exchange_declare(self, **_kw):
        pass

    def queue_declare(self, queue, durable, arguments=None):
        assert durable
        self.declared[queue] = arguments or {}

    def queue_bind(self, **_kw):
        pass


def test_declare_topology_semua_antrean_dan_dlq_quorum(monkeypatch):
    monkeypatch.setenv("RABBITMQ_QUEUE_TYPE", "quorum")
    ch = _Channel()
    rabbit.declare_topology(ch)
    for queue in events.queue_bindings():
        assert ch.declared[queue]["x-queue-type"] == "quorum"
        assert ch.declared[queue]["x-dead-letter-routing-key"] == f"{queue}.dlq"
        assert ch.declared[f"{queue}.dlq"] == {"x-queue-type": "quorum"}
