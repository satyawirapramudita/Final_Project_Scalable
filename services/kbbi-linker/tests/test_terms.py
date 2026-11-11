from app.terms import assemble_items, normalize_term, unique_terms


def test_normalize_term_collapses_spaces_and_casefolds_unicode() -> None:
    assert normalize_term("  Cakrawala\t SENJA  ") == "cakrawala senja"


def test_unique_terms_preserves_first_display_form_and_limits_input() -> None:
    assert unique_terms(["Senja", " senja ", "Cakrawala", ""], limit=2) == [
        ("Senja", "senja"),
        ("Cakrawala", "cakrawala"),
    ]


def test_assemble_items_keeps_original_poem_term_and_shared_definition() -> None:
    relations = [
        {"display_term": "Membentang", "normalized_term": "membentang"},
        {"display_term": "Senja", "normalized_term": "senja"},
    ]
    entries = {
        "membentang": {
            "bentuk_baku": "bentang",
            "kelas_kata": "verba",
            "definisi": ["tampak terhampar"],
        },
        "senja": {
            "bentuk_baku": "senja",
            "kelas_kata": "nomina",
            "definisi": ["waktu setelah matahari terbenam"],
        },
    }

    assert assemble_items(relations, entries) == [
        {
            "kata_asli": "Membentang",
            "normalized_term": "membentang",
            "found": True,
            "bentuk_baku": "bentang",
            "kelas_kata": "verba",
            "definisi": ["tampak terhampar"],
        },
        {
            "kata_asli": "Senja",
            "normalized_term": "senja",
            "found": True,
            "bentuk_baku": "senja",
            "kelas_kata": "nomina",
            "definisi": ["waktu setelah matahari terbenam"],
        },
    ]


def test_assemble_items_marks_unknown_terms_without_dropping_them() -> None:
    assert assemble_items(
        [{"display_term": "Temaram", "normalized_term": "temaram"}], {}
    ) == [
        {
            "kata_asli": "Temaram",
            "normalized_term": "temaram",
            "found": False,
            "bentuk_baku": None,
            "kelas_kata": None,
            "definisi": [],
        }
    ]
