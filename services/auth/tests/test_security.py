"""Uji PBKDF2 yang dipindah apa adanya dari monolit Tugas 1 (server.py L42-61).

Angka dan format di bawah adalah persis perilaku monolit; mengubahnya berarti
mengubah hash yang sudah tersimpan, jadi test ini berfungsi sebagai pengaman.
"""

from __future__ import annotations

import pytest

from services.auth.app.security import (
    PBKDF2_ITERATIONS,
    USERNAME_PATTERN,
    hash_password,
    verify_password,
)


class TestParameterMonolit:
    def test_iterasi_sama_dengan_monolit(self):
        # server.py L42: PBKDF2_ITERATIONS = 200_000
        assert PBKDF2_ITERATIONS == 200_000

    def test_algoritma_sha256(self):
        # hash_password memakai pbkdf2_hmac("sha256", ...); panjang digest hex = 64
        stored = hash_password("rahasia123")
        assert len(stored.split(":", 1)[1]) == 64


class TestHashPassword:
    def test_format_salt_digest(self):
        stored = hash_password("rahasia123")
        salt, digest = stored.split(":", 1)
        assert len(salt) == 32  # token_hex(16)
        int(salt, 16)  # harus hex valid
        int(digest, 16)

    def test_salt_selalu_berbeda(self):
        assert hash_password("sama") != hash_password("sama")

    def test_bukan_plaintext(self):
        assert "rahasia123" not in hash_password("rahasia123")


class TestVerifyPassword:
    def test_password_benar(self):
        assert verify_password("rahasia123", hash_password("rahasia123")) is True

    def test_password_salah(self):
        assert verify_password("salah", hash_password("rahasia123")) is False

    def test_stored_rusak_tanpa_titik_dua(self):
        assert verify_password("rahasia123", "bukan-format-yang-benar") is False

    def test_stored_kosong(self):
        assert verify_password("rahasia123", "") is False

    @pytest.mark.parametrize("password", ["a" * 6, "kupu-kupu_123!", "x" * 200])
    def test_password_panjang_dan_simbol(self, password):
        assert verify_password(password, hash_password(password)) is True


class TestUsernamePattern:
    @pytest.mark.parametrize(
        "username",
        ["abc", "ardi", "ardi_putra", "ardi-99", "a.b_c-d1", "A" * 50, "123"],
    )
    def test_valid(self, username):
        assert USERNAME_PATTERN.fullmatch(username)

    @pytest.mark.parametrize(
        "username",
        [
            "ab",           # < 3 karakter
            "A" * 51,       # > 50 karakter
            "ada spasi",    # karakter terlarang
            "ardi@x",       # karakter terlarang
            "",             # kosong
            "ardi#",
            "ardi/jati",
        ],
    )
    def test_tidak_valid(self, username):
        assert USERNAME_PATTERN.fullmatch(username) is None
