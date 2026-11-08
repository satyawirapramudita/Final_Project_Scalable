"""Uji POST /api/auth/register — validasi & penyimpanan mengikuti monolit."""

from __future__ import annotations

import pytest

from services.auth.app.security import verify_password


class TestRegisterSukses:
    def test_201_dengan_format_monolit(self, client):
        resp = client.post(
            "/api/auth/register",
            json={"username": "ardi", "nama": "Ardi Pratama", "password": "rahasia123"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["ok"] is True
        assert "ardi" in body["message"]

    def test_password_tersimpan_sebagai_hash_pbkdf2(self, client, store):
        client.post(
            "/api/auth/register",
            json={"username": "ardi", "nama": "Ardi", "password": "rahasia123"},
        )
        stored = store[0]["password"]
        assert "rahasia123" not in stored
        assert stored.count(":") == 1
        assert verify_password("rahasia123", stored)

    def test_username_dan_nama_ditrim(self, client, store):
        client.post(
            "/api/auth/register",
            json={"username": "  ardi  ", "nama": "  Ardi  ", "password": "rahasia123"},
        )
        assert store[0]["username"] == "ardi"
        assert store[0]["nama"] == "Ardi"

    @pytest.mark.parametrize(
        "username,nama",
        [
            ("ardi", "Ardi"),
            ("a_b-c.d1", "Nama dengan simbol"),
            ("123", "Angka saja"),
        ],
    )
    def test_username_valid(self, client, username, nama):
        resp = client.post(
            "/api/auth/register",
            json={"username": username, "nama": nama, "password": "rahasia123"},
        )
        assert resp.status_code == 201, resp.text


class TestRegisterDitolak:
    @pytest.mark.parametrize(
        "username",
        ["ab", "A" * 51, "ada spasi", "ardi@x", "", "ardi#"],
    )
    def test_400_username_tidak_valid(self, client, username):
        resp = client.post(
            "/api/auth/register",
            json={"username": username, "nama": "Ardi", "password": "rahasia123"},
        )
        assert resp.status_code == 400
        assert resp.json()["ok"] is False
        assert "Username" in resp.json()["message"]

    @pytest.mark.parametrize("nama", ["", "A" * 101])
    def test_400_nama(self, client, nama):
        resp = client.post(
            "/api/auth/register",
            json={"username": "ardi", "nama": nama, "password": "rahasia123"},
        )
        assert resp.status_code == 400
        assert "Nama" in resp.json()["message"]

    @pytest.mark.parametrize("password", ["", "12345", "a" * 5])
    def test_400_password_terlalu_pendek(self, client, password):
        resp = client.post(
            "/api/auth/register",
            json={"username": "ardi", "nama": "Ardi", "password": password},
        )
        assert resp.status_code == 400
        assert "Password" in resp.json()["message"]

    def test_409_username_sudah_terdaftar(self, client):
        payload = {"username": "ardi", "nama": "Ardi", "password": "rahasia123"}
        assert client.post("/api/auth/register", json=payload).status_code == 201
        resp = client.post("/api/auth/register", json=payload)
        assert resp.status_code == 409
        assert resp.json()["message"] == "Username sudah terdaftar"

    def test_409_balapan_integrity_error(self, client, fake_db_duplicate_race):
        """SELECT tak menemukan duplikat, tapi INSERT menabrak unique constraint."""
        resp = client.post(
            "/api/auth/register",
            json={"username": "ardi", "nama": "Ardi", "password": "rahasia123"},
        )
        assert resp.status_code == 409
        assert resp.json()["message"] == "Username sudah terdaftar"

    def test_422_body_tidak_lengkap(self, client):
        resp = client.post("/api/auth/register", json={"username": "ardi"})
        assert resp.status_code == 422

    def test_422_bukan_json(self, client):
        resp = client.post("/api/auth/register", content="bukan json")
        assert resp.status_code == 422
