"""Uji importer KBBI di atas CSV dan MySQL sungguhan.

scripts/import_kbbi_csv.py sebelumnya hanya diuji pada dua fungsi murni.
Di sini jalur tulis yang sebenarnya diuji: alias bentuk tidak baku, urutan
definisi yang stabil, dan penolakan CSV yang cacat.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from tests.live.conftest import requires_live

pytestmark = requires_live

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "services" / "kbbi-linker" / "scripts" / "import_kbbi_csv.py"

SPEC = importlib.util.spec_from_file_location("import_kbbi_csv_live", SCRIPT)
importer = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(importer)

HEADER = "key,nama,kelas,submakna,bentuk_tidak_baku\n"


def _write_csv(tmp_path: Path, rows: str) -> Path:
    path = tmp_path / "kbbi.csv"
    path.write_text(HEADER + rows, encoding="utf-8-sig")
    return path


def _count(table: str) -> int:
    from app.db import pool

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
        return cur.fetchone()["n"]


def _rows(sql: str, params=()) -> list[dict]:
    from app.db import pool

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def test_import_creates_entry_and_definition(schema, tmp_path) -> None:
    path = _write_csv(tmp_path, "senja,senja,nomina,waktu setelah matahari terbenam,\n")

    entries, definitions = importer.import_csv(str(path), "kbbi-v-live")

    assert (entries, definitions) == (1, 1)
    row = _rows("SELECT kata,normalized_kata,bentuk_baku,kelas_kata FROM kbbi_entries")[0]
    assert row["normalized_kata"] == "senja"
    assert row["bentuk_baku"] == "senja"
    assert row["kelas_kata"] == "nomina"


def test_import_creates_alias_entries_pointing_to_canonical_form(schema, tmp_path) -> None:
    """Bentuk tidak baku harus bisa dicari dan menunjuk ke bentuk baku."""
    path = _write_csv(
        tmp_path, "aktivitas,aktivitas,nomina,kegiatan,aktifitas; aktipitas\n"
    )

    entries, _ = importer.import_csv(str(path), "kbbi-v-live")

    assert entries == 3
    rows = {r["normalized_kata"]: r for r in _rows("SELECT normalized_kata,bentuk_baku FROM kbbi_entries")}
    assert set(rows) == {"aktivitas", "aktifitas", "aktipitas"}
    assert all(r["bentuk_baku"] == "aktivitas" for r in rows.values())


def test_import_normalizes_case_and_whitespace(schema, tmp_path) -> None:
    path = _write_csv(tmp_path, "Aktivitas ,Aktivitas,nomina,kegiatan,\n")

    importer.import_csv(str(path), "kbbi-v-live")

    assert _rows("SELECT normalized_kata FROM kbbi_entries")[0]["normalized_kata"] == "aktivitas"


def test_import_keeps_multiple_definitions_in_stable_order(schema, tmp_path) -> None:
    """Definisi kedua untuk entri yang sama harus mendapat urutan 2, bukan menimpa."""
    path = _write_csv(
        tmp_path,
        "senja,senja,nomina,waktu setelah matahari terbenam,\n"
        "senja,senja,nomina,waktu senja,\n",
    )

    _, definitions = importer.import_csv(str(path), "kbbi-v-live")

    assert definitions == 2
    orders = [r["urutan"] for r in _rows("SELECT urutan FROM kbbi_definitions ORDER BY urutan")]
    assert orders == [1, 2]


def test_reimport_is_idempotent_and_refreshes_definitions(schema, tmp_path) -> None:
    """Impor ulang versi sama tidak boleh menggandakan baris."""
    path = _write_csv(tmp_path, "senja,senja,nomina,definisi awal,\n")
    importer.import_csv(str(path), "kbbi-v-live")

    importer.import_csv(str(path), "kbbi-v-live")

    assert _count("kbbi_entries") == 1
    assert _count("kbbi_definitions") == 1
    assert _rows("SELECT definisi FROM kbbi_definitions")[0]["definisi"] == "definisi awal"


def test_import_skips_rows_without_lookup_key(schema, tmp_path) -> None:
    path = _write_csv(
        tmp_path,
        ",,,definisi yatim,\n"
        "senja,senja,nomina,definisi valid,\n",
    )

    entries, _ = importer.import_csv(str(path), "kbbi-v-live")

    assert entries == 1
    assert _rows("SELECT kata FROM kbbi_entries")[0]["kata"] == "senja"


def test_import_without_definition_still_creates_entry(schema, tmp_path) -> None:
    path = _write_csv(tmp_path, "senja,senja,nomina,,\n")

    entries, definitions = importer.import_csv(str(path), "kbbi-v-live")

    assert (entries, definitions) == (1, 0)


def test_import_rejects_csv_missing_required_columns(schema, tmp_path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("key,nama,kelas\nsenja,senja,nomina\n", encoding="utf-8-sig")

    with pytest.raises(ValueError, match="Kolom dataset tidak lengkap"):
        importer.import_csv(str(bad), "kbbi-v-live")


def test_import_multiple_dataset_versions_coexist(schema, tmp_path) -> None:
    """Dua versi dataset harus bisa hidup berdampingan tanpa saling menimpa."""
    path = _write_csv(tmp_path, "senja,senja,nomina,definisi lama,\n")
    importer.import_csv(str(path), "kbbi-v-2020")
    importer.import_csv(str(path), "kbbi-v-2024")

    versions = {r["dataset_version"] for r in _rows("SELECT dataset_version FROM kbbi_entries")}

    assert versions == {"kbbi-v-2020", "kbbi-v-2024"}


def test_import_makes_terms_available_through_api(schema, flush_cache, tmp_path) -> None:
    """Rantai penuh: CSV -> importer -> DB -> event -> API definisi."""
    from app import consumer
    from app import main
    from app.cache import cache
    from app.db import repository
    from app.security import current_user
    from fastapi.testclient import TestClient

    path = _write_csv(
        tmp_path,
        "senja,senja,nomina,waktu setelah matahari terbenam,\n"
        "membentang,membentang,verba,tampak terhampar,bentang\n",
    )
    importer.import_csv(str(path), "kbbi-v-live")

    consumer.apply_event(
        {
            "event_id": "b8829981-2ca7-4214-9100-4ab5cf96289f",
            "event_name": "poem.analyzed",
            "occurred_at": "2026-11-10T08:00:00+00:00",
            "payload": {"poem_id": 42, "kata_puitif": ["Senja", "Membentang", "Bentang"]},
        },
        repository=repository, cache_backend=cache, max_terms=30,
    )

    main.app.dependency_overrides[current_user] = lambda: {"sub": "1"}
    try:
        body = TestClient(main.app).get("/api/kbbi/42").json()
    finally:
        main.app.dependency_overrides.clear()
    by_term = {item["normalized_term"]: item for item in body["items"]}

    assert by_term["senja"]["definisi"] == ["waktu setelah matahari terbenam"]
    assert by_term["membentang"]["kelas_kata"] == "verba"
    assert by_term["bentang"]["bentuk_baku"] == "membentang"
