"""Uji kontrak frontend/app.js — penjaga statis atas migrasi auth F1.

Ini bukan pengganti uji browser; ini memastikan kabel yang disepakati tetap
ada: token di localStorage, header Bearer di api(), endpoint /api/auth/*,
dan tidak ada sisa panggilan gaya monolit ?action=.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[3] / "frontend" / "app.js"


def source() -> str:
    return APP_JS.read_text(encoding="utf-8")


class TestPenyimpananToken:
    def test_token_disimpan_di_localstorage(self):
        src = source()
        assert "TOKEN_KEY" in src
        assert '"puisi_token"' in src
        assert "localStorage.getItem(TOKEN_KEY)" in src

    def test_ada_helper_get_set_token(self):
        src = source()
        assert re.search(r"function\s+getToken\(", src)
        assert re.search(r"function\s+setToken\(", src)

    def test_login_menyimpan_token(self):
        assert re.search(r"setToken\(data\.token\)", source())


class TestHeaderAuthorization:
    def test_api_menyisipkan_bearer(self):
        assert 'options.headers["Authorization"] = `Bearer ${token}`' in source()

    def test_api_memakai_path_bukan_action_query(self):
        src = source()
        assert "`/api/${path}`" in src
        assert "?action=" not in src


class TestEndpointDipakai:
    @pytest.mark.parametrize(
        "endpoint",
        ["auth/me", "auth/login", "auth/register", "auth/logout", "poems"],
    )
    def test_endpoint_baru_dipakai(self, endpoint):
        assert f'api("{endpoint}"' in source()

    @pytest.mark.parametrize(
        "lama",
        [
            'api("me"',
            'api("login"',
            'api("register"',
            'api("logout"',
            'api("submit_puisi"',
            'api("daftar_puisi"',
        ],
    )
    def test_endpoint_lama_tidak_lagi_dipakai(self, lama):
        assert lama not in source()


class TestPenanganan401:
    def test_401_dan_logout_membuang_token(self):
        # setToken("") dipakai di: checkSession, logout, submit 401, loadPuisi 401.
        assert source().count('setToken("")') >= 3
