"""Uji POST /api/auth/login — penerbitan JWT & ketahanan terhadap enumerasi."""

from __future__ import annotations


class TestLoginSukses:
    def test_200_format_monolit_plus_token(self, client, registered_user):
        resp = client.post(
            "/api/auth/login",
            json={"username": "ardi", "password": "rahasia123"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["message"] == "Login berhasil"
        assert body["nama"] == "Ardi Pratama"
        assert body["username"] == "ardi"
        assert isinstance(body["token"], str) and body["token"].count(".") == 2

    def test_tidak_membocorkan_hash(self, client, registered_user):
        body = client.post(
            "/api/auth/login",
            json={"username": "ardi", "password": "rahasia123"},
        ).json()
        serialized = str(body)
        assert "password" not in serialized.lower().replace("password salah", "")
        assert ":" not in body.get("token", "")  # token JWT, bukan salt:digest

    def test_username_dengan_spasi_ditrim(self, client, registered_user):
        resp = client.post(
            "/api/auth/login",
            json={"username": "  ardi  ", "password": "rahasia123"},
        )
        assert resp.status_code == 200


class TestLoginDitolak:
    def test_401_password_salah(self, client, registered_user):
        resp = client.post(
            "/api/auth/login",
            json={"username": "ardi", "password": "salahbanget"},
        )
        assert resp.status_code == 401
        assert resp.json()["message"] == "Username atau password salah"

    def test_401_user_tidak_ada(self, client):
        resp = client.post(
            "/api/auth/login",
            json={"username": "tidakada", "password": "apa_saja"},
        )
        assert resp.status_code == 401
        # Pesan sama dengan password salah — tidak membocorkan keberadaan user.
        assert resp.json()["message"] == "Username atau password salah"

    def test_422_body_tidak_lengkap(self, client):
        resp = client.post("/api/auth/login", json={"username": "ardi"})
        assert resp.status_code == 422
