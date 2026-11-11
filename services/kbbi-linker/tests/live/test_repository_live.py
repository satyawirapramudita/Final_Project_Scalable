"""Uji Repository di atas MySQL sungguhan: urutan definisi, idempotensi, tombstone.

Yang diuji di sini TIDAK BISA diuji dengan mock: constraint UNIQUE, semantik
ON DUPLICATE KEY UPDATE, SELECT ... FOR UPDATE, dan foreign key CASCADE.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.live.conftest import requires_live

pytestmark = requires_live

UTC = timezone.utc
T0 = datetime(2026, 11, 10, 8, 0, 0)
UUID_A = "b8829981-2ca7-4214-9100-4ab5cf96289f"
UUID_B = "8ba04991-c388-4b0e-b0e0-2fa9e225fac4"
UUID_C = "1c9b7f3e-0000-4000-8000-000000000001"
UUID_D = "1c9b7f3e-0000-4000-8000-000000000002"


def _seed_entry(normalized: str, *, display: str | None = None, bentuk_baku: str | None = None,
                kelas: str = "nomina", definisi: list[str] | None = None,
                version: str = "kbbi-v-live") -> int:
    """Masukkan satu entri kamus + definisinya, kembalikan entry_id."""
    from app.db import pool

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kbbi_entries (kata,normalized_kata,bentuk_baku,kelas_kata,dataset_version) "
            "VALUES (%s,%s,%s,%s,%s)",
            (display or normalized, normalized, bentuk_baku, kelas, version),
        )
        entry_id = cur.lastrowid
        for order, text in enumerate(definisi or [], start=1):
            cur.execute(
                "INSERT INTO kbbi_definitions (entry_id,urutan,definisi) VALUES (%s,%s,%s)",
                (entry_id, order, text),
            )
        conn.commit()
    return entry_id


# ──────────────────────────── apply_analysis ────────────────────────────


def test_apply_analysis_persists_terms_and_status(schema) -> None:
    repo = schema.Repository()

    outcome = repo.apply_analysis(
        poem_id=42,
        event_id=UUID_A,
        occurred_at=T0,
        terms=[("Senja", "senja"), ("Cakrawala", "cakrawala")],
    )

    assert outcome == "applied"
    poem = repo.get_poem(42)
    assert poem["status"] == "ready"
    assert poem["deleted"] == 0
    assert poem["analysis_at"] == T0
    assert [(t["display_term"], t["normalized_term"]) for t in poem["terms"]] == [
        ("Senja", "senja"),
        ("Cakrawala", "cakrawala"),
    ]


def test_apply_analysis_is_idempotent_for_same_event_id(schema) -> None:
    repo = schema.Repository()
    repo.apply_analysis(poem_id=42, event_id=UUID_A, occurred_at=T0, terms=[("Senja", "senja")])

    second = repo.apply_analysis(
        poem_id=42, event_id=UUID_A, occurred_at=T0, terms=[("Senja", "senja")]
    )

    assert second == "ignored"
    assert len(repo.get_poem(42)["terms"]) == 1


def test_stale_event_out_of_order_is_ignored(schema) -> None:
    """Event lebih tua yang datang belakangan tidak boleh menimpa hasil baru."""
    repo = schema.Repository()
    repo.apply_analysis(
        poem_id=42, event_id=UUID_B, occurred_at=T0, terms=[("Baru", "baru")]
    )

    outcome = repo.apply_analysis(
        poem_id=42,
        event_id=UUID_C,
        occurred_at=T0 - timedelta(hours=1),
        terms=[("Lama", "lama")],
    )

    assert outcome == "ignored"
    assert [t["normalized_term"] for t in repo.get_poem(42)["terms"]] == ["baru"]


def test_analysis_after_delete_is_ignored(schema) -> None:
    """Tombstone harus menang: poem yang sudah dihapus tidak boleh hidup lagi."""
    repo = schema.Repository()
    assert repo.apply_delete(poem_id=42, event_id=UUID_A, occurred_at=T0) == "applied"

    outcome = repo.apply_analysis(
        poem_id=42, event_id=UUID_B, occurred_at=T0 + timedelta(hours=1), terms=[("Senja", "senja")]
    )

    assert outcome == "ignored"
    assert repo.get_poem(42)["deleted"] == 1
    assert repo.get_poem(42)["terms"] == ()


def test_reanalysis_replaces_previous_terms(schema) -> None:
    repo = schema.Repository()
    repo.apply_analysis(
        poem_id=42, event_id=UUID_A, occurred_at=T0, terms=[("Senja", "senja"), ("Lama", "lama")]
    )

    repo.apply_analysis(
        poem_id=42,
        event_id=UUID_B,
        occurred_at=T0 + timedelta(hours=1),
        terms=[("Cakrawala", "cakrawala")],
    )

    assert [t["normalized_term"] for t in repo.get_poem(42)["terms"]] == ["cakrawala"]


def test_apply_analysis_with_empty_terms_is_ready_not_pending(schema) -> None:
    repo = schema.Repository()

    assert repo.apply_analysis(poem_id=42, event_id=UUID_A, occurred_at=T0, terms=[]) == "applied"

    assert repo.get_poem(42)["status"] == "ready"
    assert repo.get_poem(42)["terms"] == ()


def test_terms_are_shared_between_poems_not_duplicated(schema) -> None:
    """Kata yang sama dipakai dua puisi harus menunjuk satu baris poetic_terms."""
    repo = schema.Repository()
    repo.apply_analysis(poem_id=1, event_id=UUID_A, occurred_at=T0, terms=[("Senja", "senja")])
    repo.apply_analysis(poem_id=2, event_id=UUID_B, occurred_at=T0, terms=[("Senja", "senja")])

    from app.db import pool

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM poetic_terms")
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT COUNT(*) AS n FROM poem_terms")
        assert cur.fetchone()["n"] == 2


def test_display_term_of_shared_term_follows_latest_event(schema) -> None:
    """ON DUPLICATE KEY UPDATE display_term=VALUES(...) — verifikasi perilaku nyata."""
    repo = schema.Repository()
    repo.apply_analysis(poem_id=1, event_id=UUID_A, occurred_at=T0, terms=[("SENJA", "senja")])
    repo.apply_analysis(poem_id=2, event_id=UUID_B, occurred_at=T0, terms=[("Senja", "senja")])

    assert repo.get_poem(1)["terms"][0]["display_term"] == "Senja"


def test_get_poem_returns_none_for_unknown_poem(schema) -> None:
    assert schema.Repository().get_poem(999) is None


# ──────────────────────────── apply_delete ────────────────────────────


def test_apply_delete_creates_tombstone_and_drops_relations(schema) -> None:
    repo = schema.Repository()
    repo.apply_analysis(poem_id=42, event_id=UUID_A, occurred_at=T0, terms=[("Senja", "senja")])

    assert repo.apply_delete(poem_id=42, event_id=UUID_B, occurred_at=T0) == "applied"

    poem = repo.get_poem(42)
    assert poem["status"] == "deleted"
    assert poem["deleted"] == 1
    assert poem["terms"] == ()


def test_apply_delete_on_unknown_poem_creates_tombstone(schema) -> None:
    """Delete sebelum analyze (urutan event tak terjamin) tetap harus menghasilkan tombstone."""
    repo = schema.Repository()

    assert repo.apply_delete(poem_id=77, event_id=UUID_A, occurred_at=T0) == "applied"

    assert repo.get_poem(77)["deleted"] == 1


def test_delete_tombstone_is_not_purged_by_dictionary_cleanup(schema) -> None:
    """Definisi kamus boleh dihapus, tombstone puisi harus tetap ada."""
    repo = schema.Repository()
    repo.apply_delete(poem_id=42, event_id=UUID_A, occurred_at=T0)

    from app.db import pool

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM kbbi_entries")
        conn.commit()

    assert repo.get_poem(42)["deleted"] == 1


# ──────────────────────────── get_entries ────────────────────────────


def test_get_entries_orders_definitions_and_joins_metadata(schema) -> None:
    _seed_entry(
        "senja",
        display="senja",
        bentuk_baku="senja",
        kelas="nomina",
        definisi=["waktu setelah matahari terbenam", "waktu senja"],
    )
    repo = schema.Repository()

    entries = repo.get_entries(["senja"])

    assert entries["senja"]["kelas_kata"] == "nomina"
    assert entries["senja"]["bentuk_baku"] == "senja"
    assert entries["senja"]["definisi"] == [
        "waktu setelah matahari terbenam",
        "waktu senja",
    ]


def test_get_entries_includes_all_normalized_terms(schema) -> None:
    _seed_entry("membentang", bentuk_baku="bentang", kelas="verba", definisi=["tampak terhampar"])
    _seed_entry("senja", definisi=["waktu setelah matahari terbenam"])
    repo = schema.Repository()

    entries = repo.get_entries(["membentang", "senja"])

    assert set(entries) == {"membentang", "senja"}
    assert entries["membentang"]["bentuk_baku"] == "bentang"


def test_get_entries_omits_unknown_terms(schema) -> None:
    _seed_entry("senja", definisi=["waktu setelah matahari terbenam"])
    repo = schema.Repository()

    entries = repo.get_entries(["senja", "temaram"])

    assert set(entries) == {"senja"}


def test_get_entries_filters_by_dataset_version(schema) -> None:
    """Versi dataset lain tidak boleh bocor ke jawaban versi aktif."""
    _seed_entry("senja", definisi=["versi lama"], version="kbbi-v-2020")
    repo = schema.Repository()

    assert repo.get_entries(["senja"]) == {}


def test_get_entries_entry_without_definition_still_returned(schema) -> None:
    """LEFT JOIN: entri tanpa definisi harus tetap muncul, definisi kosong."""
    _seed_entry("senja", bentuk_baku="senja")
    repo = schema.Repository()

    entries = repo.get_entries(["senja"])

    assert entries["senja"]["definisi"] == []
    assert entries["senja"]["bentuk_baku"] == "senja"


def test_get_entries_with_empty_input_skips_query(schema) -> None:
    assert schema.Repository().get_entries([]) == {}


def test_get_entries_handles_term_injection_attempt_safely(schema) -> None:
    """Nilai selalu lewat placeholder — pastikan tidak ada SQL injection."""
    _seed_entry("senja", definisi=["waktu setelah matahari terbenam"])
    repo = schema.Repository()

    entries = repo.get_entries(["senja' OR '1'='1", "senja'; DROP TABLE kbbi_entries;--"])

    assert entries == {}
    assert repo.get_entries(["senja"]) != {}


# ──────────────────────────── init_schema ────────────────────────────


def test_init_schema_is_idempotent(schema) -> None:
    schema.init_schema(retries=1)

    from app.db import pool

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SHOW TABLES")
        tables = {list(row.values())[0] for row in cur.fetchall()}

    assert {
        "kbbi_entries",
        "kbbi_definitions",
        "poetic_terms",
        "kbbi_poem_status",
        "poem_terms",
    } <= tables
