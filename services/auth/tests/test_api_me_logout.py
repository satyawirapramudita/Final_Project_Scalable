"""Uji GET /api/auth/me dan POST /api/auth/logout.

Kontrak monolit dipertahankan: /me selalu 200 — {ok:false} saat tidak
berlogin (bukan 401), karena frontend memeriksa data.ok.
"""

from __future__ import annotations


class TestMe:
    def test_200_ok_false_tanpa_token(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json() == {"ok": False, "message": "Belum login"}

    def test_200_ok_false_header_bukan_bearer(self, client):
        resp = client.get("/api/auth/me", headers={"Authorization": "Token abc"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_200_ok_false_token_sampah(self, client):
        resp = client.get("/api/auth/me", headers={"Authorization": "Bearer abc.def.ghi"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert "kedaluwarsa" in resp.json()["message"] or "tidak valid" in resp.json()["message"]

    def test_200_ok_true_dengan_token_valid(self, client, auth_header):
        resp = client.get("/api/auth/me", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "username": "ardi", "nama": "Ardi Pratama"}

    def test_200_ok_false_token_kedaluwarsa(self, client, monkeypatch):
        from shared import security

        token = security.create_token(user_id=1, username="ardi", expires_minutes=-1)
        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False


class TestLogout:
    def test_200_dengan_token(self, client, auth_header):
        resp = client.post("/api/auth/logout", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "message": "Logout berhasil"}

    def test_200_tanpa_token_sekalipun(self, client):
        # Stateless: tidak ada state server yang perlu dihapus.
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestAlurLengkap:
    def test_register_login_me_logout_me(self, client):
        """Skenario end-to-end persis alur frontend."""
        assert (
            client.post(
                "/api/auth/register",
                json={"username": "ardi", "nama": "Ardi", "password": "rahasia123"},
            ).status_code
            == 201
        )
        login = client.post(
            "/api/auth/login", json={"username": "ardi", "password": "rahasia123"}
        )
        token = login.json()["token"]
        header = {"Authorization": f"Bearer {token}"}

        me = client.get("/api/auth/me", headers=header)
        assert me.json() == {"ok": True, "username": "ardi", "nama": "Ardi"}

        assert client.post("/api/auth/logout").status_code == 200

        # Logout stateless: token masih diterima sampai kedaluwarsa —
        # pembuangan dilakukan klien. (Perilaku yang disepakati.)
        me2 = client.get("/api/auth/me", headers=header)
        assert me2.json()["ok"] is True
