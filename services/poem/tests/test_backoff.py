"""Uji shared.backoff — delay retry dengan jitter.

Helper ini dipakai lintas service (rabbit, init_schema db.py), jadi diuji
dari sini agar perilakunya terkunci: penuh acak dalam batas atas yang jelas,
dan rata-rata delay tetap tidak berubah untuk loop delay tetap.
"""

from __future__ import annotations

import pytest

from shared.backoff import equal_jitter, full_jitter


class TestFullJitter:
    """Full jitter: acak merata di [0, min(cap, base * 2**(attempt-1))]."""

    def test_attempt_pertama_tidak_melebihi_base(self) -> None:
        for _ in range(200):
            assert 0 <= full_jitter(1, base=2, cap=8) <= 2

    def test_ceiling_eksponensial_dibatasi_cap(self) -> None:
        # rng = 1.0 → nilai maksimum interval, untuk mengunci bentuk ceiling.
        assert full_jitter(1, base=2, cap=8, rng=lambda: 1.0) == pytest.approx(2)
        assert full_jitter(3, base=2, cap=8, rng=lambda: 1.0) == pytest.approx(8)
        assert full_jitter(4, base=2, cap=8, rng=lambda: 1.0) == pytest.approx(8)

    def test_bisa_nol_agar_tidak_blokir_uji(self) -> None:
        assert full_jitter(2, base=2, cap=8, rng=lambda: 0.0) == 0

    def test_attempt_nol_ditolak(self) -> None:
        with pytest.raises(ValueError):
            full_jitter(0, base=2, cap=8)


class TestEqualJitter:
    """Equal jitter untuk delay tetap: acak di [delay/2, delay] — rata-rata dipelihara."""

    def test_rentang_setengah_sampai_penuh(self) -> None:
        for _ in range(200):
            assert 1 <= equal_jitter(2) <= 2

    def test_batas_dengan_rng_deterministik(self) -> None:
        assert equal_jitter(2, rng=lambda: 0.0) == pytest.approx(1)
        assert equal_jitter(2, rng=lambda: 1.0) == pytest.approx(2)

    def test_rata_rata_dipelihara(self) -> None:
        sampel = [equal_jitter(4) for _ in range(500)]
        assert 2.5 <= sum(sampel) / len(sampel) <= 3.5  # rata-rata ideal 3.0
