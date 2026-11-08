"""Uji kontrak infrastruktur: docker-compose.yml dan .env.example.

Memastikan keputusan F1 terpasang di infra: healthcheck auth, TTL JWT 24 jam,
dan secret JWT tersedia untuk semua layanan.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"


def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


class TestServiceAuth:
    def test_service_auth_terdaftar(self):
        assert "auth" in compose()["services"]

    def test_auth_menggunakan_jaringan_edge_dan_data(self):
        nets = compose()["services"]["auth"]["networks"]
        assert set(nets) == {"edge", "data"}

    def test_auth_mengikat_db_auth(self):
        env = compose()["services"]["auth"]["environment"]
        assert env["DB_NAME"] == "${AUTH_DB_NAME:-auth_db}"
        assert env["DB_USER"] == "${AUTH_DB_USER:-auth_user}"

    def test_auth_punya_healthcheck_port_8001(self):
        health = compose()["services"]["auth"].get("healthcheck")
        assert health is not None, "auth satu-satunya layanan HTTP tanpa healthcheck"
        assert ":8001/health" in str(health["test"])

    def test_auth_mewarisi_env_jwt(self):
        compose_text = COMPOSE.read_text(encoding="utf-8")
        assert "JWT_SECRET: ${JWT_SECRET}" in compose_text


class TestTtlJwt:
    def test_default_compose_24_jam(self):
        compose_text = COMPOSE.read_text(encoding="utf-8")
        assert "JWT_EXPIRE_MINUTES: ${JWT_EXPIRE_MINUTES:-1440}" in compose_text

    def test_env_example_24_jam(self):
        env = ENV_EXAMPLE.read_text(encoding="utf-8")
        assert "JWT_EXPIRE_MINUTES=1440" in env

    def test_env_example_menyarankan_secret_kuat(self):
        env = ENV_EXAMPLE.read_text(encoding="utf-8")
        assert "JWT_SECRET=" in env
        assert "minimal_32_karakter" in env


class TestSkemaDb:
    def test_tabel_users_di_init_sql(self):
        init_sql = (ROOT / "db" / "init.sql").read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS users" in init_sql
        assert "PBKDF2: salt:digest" in init_sql
        # auth_db milik Ardi
        assert "USE auth_db;" in init_sql
