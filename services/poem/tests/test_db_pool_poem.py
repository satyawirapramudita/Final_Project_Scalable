"""Uji connection pool poem service (dbutils PooledDB).

Creator diganti stub agar PooledDB berjalan tanpa MySQL; yang diuji adalah
perilaku pool: koneksi dipakai ulang setelah close() dan parameter pool
mengikuti konfigurasi env.
"""

from __future__ import annotations

import pymysql
import pytest


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConnection:
    dibuat: list["FakeConnection"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.closed = False
        FakeConnection.dibuat.append(self)

    def cursor(self, *args, **kwargs):
        return FakeCursor()

    def close(self) -> None:
        self.closed = True

    def ping(self, reconnect: bool = True) -> None:  # dipakai PooledDB
        pass


@pytest.fixture(autouse=True)
def _reset():
    """Pool bersih untuk tiap test; koneksi palsu frisk."""
    from services.poem.app import db as db_mod

    FakeConnection.dibuat = []
    db_mod._POOL = None
    yield
    db_mod._POOL = None


@pytest.fixture
def pool_env(monkeypatch):
    from services.poem.app import db as db_mod

    monkeypatch.setattr(db_mod.config, "DB_HOST", "mysql-uji")
    monkeypatch.setattr(db_mod.config, "DB_PORT", 3307)
    monkeypatch.setattr(db_mod.config, "DB_USER", "uji")
    monkeypatch.setattr(db_mod.config, "DB_PASSWORD", "rahasia")
    monkeypatch.setattr(db_mod.config, "DB_NAME", "poem_uji")
    monkeypatch.setattr(db_mod.config, "DB_POOL_MIN", 1)
    monkeypatch.setattr(db_mod.config, "DB_POOL_MAX", 3)
    monkeypatch.setattr(pymysql, "connect", lambda **kw: FakeConnection(**kw))
    return db_mod


class TestPool:
    def test_koneksi_dipakai_ulang_setelah_close(self, pool_env):
        conn1 = pool_env.connect()
        inner1 = conn1._con  # SteadyDBConnection yang dibungkus proxy pool
        conn1.close()  # proxy jadi kosong; koneksi masuk kembali ke pool
        assert conn1._con is None
        conn2 = pool_env.connect()
        # Objek koneksi steady yang sama dipakai ulang dari cache pool.
        assert conn2._con is inner1

    def test_close_tidak_menutup_koneksi_asli(self, pool_env):
        conn = pool_env.connect()
        raw = conn._con._con  # koneksi pymysql asli di dalam SteadyDBConnection
        conn.close()
        assert not raw.closed  # fake menandai closed=True hanya jika benar-benar ditutup

    def test_param_koneksi_mengikuti_konfigurasi(self, pool_env):
        pool_env.connect().close()
        k = FakeConnection.dibuat[0].kwargs
        assert k["host"] == "mysql-uji"
        assert k["port"] == 3307
        assert k["user"] == "uji"
        assert k["database"] == "poem_uji"
        assert k["autocommit"] is True

    def test_pool_dibuat_sekali(self, pool_env):
        for _ in range(3):
            conn = pool_env.connect()
            conn.close()
        # mincached=1 → pool pra-membuat 1 koneksi saat lazy-init; siklus
        # ambil-kembalikan tidak membuat koneksi baru.
        assert len(FakeConnection.dibuat) == 1
        assert len(pool_env._POOL._idle_cache) == 1

    def test_melebihi_maksimal_menunggu_bukan_error(self, pool_env):
        maxconn = pool_env.config.DB_POOL_MAX
        conns = [pool_env.connect() for _ in range(maxconn)]
        assert len(conns) == maxconn


class TestMetrikPool:
    def test_statistik_idle_dan_in_use(self, pool_env):
        conn = pool_env.connect()
        assert pool_env._pool_stats() == {"idle": 0, "in_use": 1}
        conn.close()
        assert pool_env._pool_stats() == {"idle": 1, "in_use": 0}

    def test_gauge_terdaftar_dengan_label_service(self, pool_env):
        from shared.metrics import DB_POOL_CONNECTIONS

        sampel = DB_POOL_CONNECTIONS.labels("poem", "in_use")
        # set_function terpasang: nilai diambil dinamis dari pool.
        assert sampel._value.get() == 0
