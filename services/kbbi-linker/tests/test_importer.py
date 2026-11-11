from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_kbbi_csv.py"
SPEC = importlib.util.spec_from_file_location("import_kbbi_csv", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)


def test_importer_normalizes_lookup_keys() -> None:
    assert module.normalize("  Aktivitas  ") == "aktivitas"


def test_importer_splits_non_standard_forms_conservatively() -> None:
    assert module.aliases("aktifitas; aktipitas, aktifitasi") == [
        "aktifitas",
        "aktipitas",
        "aktifitasi",
    ]


def test_first_definition_for_entry_removes_stale_rows() -> None:
    class Cursor:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params):
            self.calls.append((sql, params))

    cursor = Cursor()
    orders = {}

    assert module.next_definition_order(cursor, orders, "v1:senja", 7) == 1
    assert module.next_definition_order(cursor, orders, "v1:senja", 7) == 2
    assert cursor.calls == [("DELETE FROM kbbi_definitions WHERE entry_id=%s", (7,))]
