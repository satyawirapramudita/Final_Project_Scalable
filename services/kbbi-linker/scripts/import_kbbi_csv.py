"""Import the non-commercial KBBI V CSV dataset into kbbi_db.

Usage from the repository root:
    python services/kbbi-linker/scripts/import_kbbi_csv.py path/to/kbbi_v.csv \
        --version kbbi-v-2024-07
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import unicodedata

import pymysql


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip().casefold()[:100]


def aliases(value: str) -> list[str]:
    """Split the dataset's non-standard-form column conservatively."""
    if not value:
        return []
    return [part.strip() for part in re.split(r"[,;]", value) if part.strip()]


def canonical_form(key: str, nama: str) -> str:
    """Bentuk baku untuk ditampilkan.

    Di KBBI V, kolom `nama` untuk kata biasa berisi pemenggalan suku kata
    ("sen.ja", "ang.ga.na.se.kar"), bukan bentuk baku. Bila `nama` hanya
    `key` plus titik suku kata, pakai `key`. Bila benar-benar berbeda (mis.
    singkatan pada peribahasa: "dng" -> "dengan"), pakai `nama`.
    """
    key, nama = (key or "").strip(), (nama or "").strip()
    if not nama:
        return key[:100]
    if key and re.sub(r"[.\s]", "", nama) == re.sub(r"\s", "", key):
        return key[:100]
    return nama[:100]


def next_definition_order(cursor, orders: dict[str, int], key: str, entry_id: int) -> int:
    """Reset stale definitions once, then allocate stable order within this import."""
    if key not in orders:
        cursor.execute("DELETE FROM kbbi_definitions WHERE entry_id=%s", (entry_id,))
    order = orders.get(key, 0) + 1
    orders[key] = order
    return order


def import_csv(path: str, version: str, commit_every: int = 1_000) -> tuple[int, int]:
    connection = pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "kbbi_user"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "kbbi_db"),
        charset="utf8mb4",
        autocommit=False,
        local_infile=False,
    )
    definition_order: dict[str, int] = {}
    entries = definitions = 0
    try:
        with open(path, encoding="utf-8-sig", newline="") as source, connection.cursor() as cursor:
            reader = csv.DictReader(source)
            required = {"key", "nama", "kelas", "submakna", "bentuk_tidak_baku"}
            if not required.issubset(reader.fieldnames or []):
                missing = sorted(required.difference(reader.fieldnames or []))
                raise ValueError(f"Kolom dataset tidak lengkap: {', '.join(missing)}")

            for row_number, row in enumerate(reader, start=1):
                lookup = normalize(row["key"] or row["nama"])
                canonical = canonical_form(row["key"], row["nama"])
                if not lookup or not canonical:
                    continue
                forms = [(lookup, row["key"].strip()[:100] or canonical)]
                forms.extend((normalize(alias), alias[:100]) for alias in aliases(row["bentuk_tidak_baku"]))
                for normalized, display in forms:
                    if not normalized:
                        continue
                    cursor.execute(
                        "INSERT INTO kbbi_entries "
                        "(kata,normalized_kata,bentuk_baku,kelas_kata,dataset_version) "
                        "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
                        "id=LAST_INSERT_ID(id),kata=VALUES(kata),bentuk_baku=VALUES(bentuk_baku),"
                        "kelas_kata=VALUES(kelas_kata)",
                        (display, normalized, canonical, (row["kelas"] or "")[:80] or None, version),
                    )
                    entry_id = cursor.lastrowid
                    entries += 1
                    definition = (row["submakna"] or "").strip()
                    if definition:
                        order_key = f"{version}:{normalized}"
                        order = next_definition_order(
                            cursor, definition_order, order_key, entry_id
                        )
                        cursor.execute(
                            "INSERT INTO kbbi_definitions (entry_id,urutan,definisi) VALUES (%s,%s,%s) "
                            "ON DUPLICATE KEY UPDATE definisi=VALUES(definisi)",
                            (entry_id, order, definition),
                        )
                        definitions += 1
                if row_number % commit_every == 0:
                    connection.commit()
                    print(f"Diproses {row_number:,} baris...", flush=True)
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return entries, definitions


def main() -> None:
    parser = argparse.ArgumentParser(description="Import dataset KBBI CSV ke kbbi_db")
    parser.add_argument("csv_path")
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    entries, definitions = import_csv(args.csv_path, args.version)
    print(f"Selesai: {entries:,} entri/form dan {definitions:,} definisi diproses")


if __name__ == "__main__":
    main()
