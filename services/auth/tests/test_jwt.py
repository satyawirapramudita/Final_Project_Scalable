"""Uji JWT yang diterbitkan auth.

Klaim harus kompatibel dengan verifikasi LOKAL di layanan lain —
jalur yang sama dipakai services/poem/app/security.py::current_user:
    1. header "Authorization" harus berawalan "bearer " (case-insensitive)
    2. token didekode via shared.security.decode_token (secret bersama)
    3. poem memakai int(user["sub"]) sebagai user_id
"""

from __future__ import annotations

import time

import pytest
from shared import security as shared_security


class TestKlaimToken:
    def test_klaim_inti(self, auth_header):
        token = auth_header["Authorization"].split(" ", 1)[1]
        claims = shared_security.decode_token(token)
        assert claims["sub"] == "1"  # str(user_id) — poem memanggil int(sub)
        assert claims["username"] == "ardi"
        assert claims["nama"] == "Ardi Pratama"

    def test_iat_exp_dan_masa_24_jam(self, auth_header):
        token = auth_header["Authorization"].split(" ", 1)[1]
        claims = shared_security.decode_token(token)
        assert claims["iat"] <= time.time()
        assert claims["exp"] > time.time()
        # Keputusan F1: masa token 24 jam (1440 menit).
        assert claims["exp"] - claims["iat"] == pytest.approx(1440 * 60, abs=2)

    def test_format_bearer_case_insensitive(self, auth_header):
        # poem menerima "bearer <token>" huruf kecil sekalipun.
        lowered = {
            "Authorization": auth_header["Authorization"].replace("Bearer", "bearer")
        }
        token = lowered["Authorization"].split(" ", 1)[1]
        assert shared_security.decode_token(token)["username"] == "ardi"


class TestPenolakanToken:
    def test_kedaluwarsa_ditolak(self):
        token = shared_security.create_token(user_id=1, username="a", expires_minutes=-1)
        with pytest.raises(Exception):
            shared_security.decode_token(token)

    def test_secret_beda_ditolak(self, auth_header, monkeypatch):
        # Simulasi layanan lain yang kebetulan punya secret berbeda.
        token = auth_header["Authorization"].split(" ", 1)[1]
        monkeypatch.setenv("JWT_SECRET", "secret_lain_sama_sekali_beda_panjang")
        with pytest.raises(Exception):
            shared_security.decode_token(token)

    def test_token_sampah_ditolak(self):
        with pytest.raises(Exception):
            shared_security.decode_token("abc.def.ghi")


class TestKompatibilitasPoem:
    def test_alur_verifikasi_lokal_poem(self, auth_header):
        """Replikasi persis current_user() di services/poem/app/security.py."""
        authorization = auth_header["Authorization"]
        assert authorization.lower().startswith("bearer ")
        token = authorization.split(" ", 1)[1].strip()
        user = shared_security.decode_token(token)  # tanpa memanggil auth service
        assert int(user["sub"]) == 1  # poem: user_id = int(user["sub"])
