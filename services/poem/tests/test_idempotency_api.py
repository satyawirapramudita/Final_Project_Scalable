"""Uji integrasi Idempotency-Key pada POST/PUT/DELETE /api/poems.

Skenario kunci untuk stress test & pentest: retry dengan key yang sama tidak
menduplikasi data, key dengan payload berbeda ditolak 409, dan key ter-scope
per user.
"""

from __future__ import annotations

BODY = {"judul": "Hujan", "isi": "Bumi basah", "kategori": "alam"}


class TestSubmit:
    def test_submit_dengan_key_diterima(self, client, auth_header):
        resp = client.post(
            "/api/poems", json=BODY, headers={**auth_header, "Idempotency-Key": "k-1"}
        )
        assert resp.status_code == 201
        assert resp.json()["ok"] is True

    def test_retry_key_sama_replay_bukan_duplikat(self, client, auth_header):
        headers = {**auth_header, "Idempotency-Key": "k-1"}
        pertama = client.post("/api/poems", json=BODY, headers=headers)
        ulang = client.post("/api/poems", json=BODY, headers=headers)
        assert ulang.status_code == 201
        assert ulang.json()["id_puisi"] == pertama.json()["id_puisi"]
        assert ulang.headers.get("Idempotent-Replay") == "true"

    def test_key_sama_body_beda_409(self, client, auth_header):
        headers = {**auth_header, "Idempotency-Key": "k-1"}
        client.post("/api/poems", json=BODY, headers=headers)
        resp = client.post(
            "/api/poems",
            json={**BODY, "judul": "Berbeda"},
            headers=headers,
        )
        assert resp.status_code == 409

    def test_key_sama_antar_user_tidak_bentrok(self, client, auth_header, auth_header_user2):
        headers1 = {**auth_header, "Idempotency-Key": "k-bersama"}
        headers2 = {**auth_header_user2, "Idempotency-Key": "k-bersama"}
        pertama = client.post("/api/poems", json=BODY, headers=headers1)
        kedua = client.post("/api/poems", json=BODY, headers=headers2)
        assert kedua.status_code == 201
        assert kedua.json()["id_puisi"] != pertama.json()["id_puisi"]

    def test_tanpa_key_perilaku_lama_terjaga(self, client, auth_header):
        # Klien lama tanpa header: dua submit tetap dua puisi (tidak breaking).
        r1 = client.post("/api/poems", json=BODY, headers=auth_header)
        r2 = client.post("/api/poems", json=BODY, headers=auth_header)
        assert r1.json()["id_puisi"] != r2.json()["id_puisi"]


class TestUpdate:
    def test_retry_put_key_sama_replay(self, client, auth_header, submitted_poem):
        headers = {**auth_header, "Idempotency-Key": "u-1"}
        body = {"judul": "Judul Baru"}
        pertama = client.put(f"/api/poems/{submitted_poem}", json=body, headers=headers)
        assert pertama.status_code == 200
        ulang = client.put(f"/api/poems/{submitted_poem}", json=body, headers=headers)
        assert ulang.status_code == 200
        assert ulang.json() == pertama.json()
        assert ulang.headers.get("Idempotent-Replay") == "true"

    def test_put_key_sama_body_beda_409(self, client, auth_header, submitted_poem):
        headers = {**auth_header, "Idempotency-Key": "u-1"}
        client.put(f"/api/poems/{submitted_poem}", json={"judul": "A"}, headers=headers)
        resp = client.put(f"/api/poems/{submitted_poem}", json={"judul": "B"}, headers=headers)
        assert resp.status_code == 409


class TestDelete:
    def test_retry_delete_key_sama_replay(self, client, auth_header, submitted_poem):
        headers = {**auth_header, "Idempotency-Key": "d-1"}
        pertama = client.delete(f"/api/poems/{submitted_poem}", headers=headers)
        assert pertama.status_code == 200
        ulang = client.delete(f"/api/poems/{submitted_poem}", headers=headers)
        assert ulang.status_code == 200
        assert ulang.json() == pertama.json()
        assert ulang.headers.get("Idempotent-Replay") == "true"

    def test_key_sama_untuk_puisi_lain_409_bukan_replay(self, client, auth_header, submitted_poem):
        # Key dipakai ulang untuk DELETE puisi LAIN: tidak boleh replay
        # "Puisi dihapus" (puisi kedua sebenarnya tidak terhapus).
        kedua = client.post("/api/poems", json=BODY, headers=auth_header).json()["id_puisi"]
        headers = {**auth_header, "Idempotency-Key": "d-bersama"}
        assert client.delete(f"/api/poems/{submitted_poem}", headers=headers).status_code == 200
        resp = client.delete(f"/api/poems/{kedua}", headers=headers)
        assert resp.status_code == 409
        assert resp.headers.get("Idempotent-Replay") is None
        assert client.get(f"/api/poems/{kedua}", headers=auth_header).status_code == 200

    def test_delete_tanpa_key_setelah_hapus_404(self, client, auth_header, submitted_poem):
        client.delete(f"/api/poems/{submitted_poem}", headers=auth_header)
        resp = client.delete(f"/api/poems/{submitted_poem}", headers=auth_header)
        assert resp.status_code == 404


class TestGagalTidakMengunciKey:
    def test_key_dilepas_saat_request_gagal(self, client, auth_header):
        # Submit pertama gagal validasi (judul kosong) → key boleh dipakai ulang.
        headers = {**auth_header, "Idempotency-Key": "g-1"}
        gagal = client.post("/api/poems", json={**BODY, "judul": ""}, headers=headers)
        assert gagal.status_code == 400
        sukses = client.post("/api/poems", json=BODY, headers=headers)
        assert sukses.status_code == 201
