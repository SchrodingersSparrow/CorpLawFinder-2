"""Stage 4 tests: pure utility helpers — dates and naming (stdlib only)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.utils.dates import extract_date, parse_date  # noqa: E402
from app.utils.naming import (  # noqa: E402
    render_filename,
    sanitize_component,
    unique_path,
)


class TestDates(unittest.TestCase):
    def test_iso(self) -> None:
        self.assertEqual(parse_date("2026-07-15"), "2026-07-15")

    def test_day_first_numeric_is_indian_convention(self) -> None:
        # 05/07/2026 is 5 July, never 7 May.
        self.assertEqual(parse_date("05/07/2026"), "2026-07-05")
        self.assertEqual(parse_date("15-07-2026"), "2026-07-15")
        self.assertEqual(parse_date("15.07.2026"), "2026-07-15")

    def test_named_months_both_orders(self) -> None:
        self.assertEqual(parse_date("July 15, 2026"), "2026-07-15")
        self.assertEqual(parse_date("15 July 2026"), "2026-07-15")
        self.assertEqual(parse_date("15th July, 2026"), "2026-07-15")
        self.assertEqual(parse_date("1st March 2024"), "2024-03-01")
        self.assertEqual(parse_date("15 Jul 2026"), "2026-07-15")
        self.assertEqual(parse_date("Sept 3, 2025"), "2025-09-03")

    def test_rejects_impossible_and_out_of_range(self) -> None:
        self.assertIsNone(parse_date("32/01/2026"))       # no 32nd day
        self.assertIsNone(parse_date("29/02/2025"))       # not a leap year
        self.assertIsNone(parse_date("15/07/1846"))       # before range
        self.assertIsNone(parse_date(""))
        self.assertIsNone(parse_date(None))
        self.assertIsNone(parse_date("Master Direction"))

    def test_extract_finds_date_inside_text(self) -> None:
        self.assertEqual(
            extract_date("Master Direction – KYC (Updated as on July 15, 2026)"),
            "2026-07-15",
        )
        self.assertEqual(
            extract_date("Circular No. 12/2026 dated 05/03/2026 regarding FDI"),
            "2026-03-05",
        )
        self.assertEqual(extract_date("NT123CIRC2026.pdf"), None)

    def test_iso_wins_over_other_forms(self) -> None:
        self.assertEqual(
            extract_date("published 01/02/2026, effective 2026-03-04"), "2026-03-04"
        )


class TestNaming(unittest.TestCase):
    def test_sanitize_strips_windows_forbidden_characters(self) -> None:
        self.assertEqual(
            sanitize_component('KYC: "Master" <Direction>?'), "KYC Master Direction"
        )
        self.assertEqual(sanitize_component("a/b\\c|d*e"), "a b c d e")
        self.assertEqual(sanitize_component("name..."), "name")
        self.assertEqual(sanitize_component("  "), "Unknown")
        self.assertEqual(sanitize_component(None, fallback="X"), "X")

    def test_sanitize_avoids_reserved_device_names(self) -> None:
        self.assertEqual(sanitize_component("CON"), "CON file")
        self.assertEqual(sanitize_component("com1"), "com1 file")

    def test_render_full_template(self) -> None:
        name = render_filename(
            "{authority} - {doc_type} - {title} - {date}",
            {
                "authority": "RBI",
                "doc_type": "Master Direction",
                "title": "KYC Direction 2016",
                "date": "2026-07-15",
            },
            extension="pdf",
        )
        self.assertEqual(name, "RBI - Master Direction - KYC Direction 2016 - 2026-07-15.pdf")

    def test_missing_fields_collapse_tidily(self) -> None:
        name = render_filename(
            "{authority} - {doc_type} - {title} - {date}",
            {"authority": "SEBI", "title": "LODR amendments"},
            extension="pdf",
            unknown="",
        )
        # Empty unknowns leave "- -" runs which must collapse.
        self.assertNotIn("- -", name)
        self.assertTrue(name.startswith("SEBI"))
        self.assertTrue(name.endswith("LODR amendments.pdf"))

    def test_length_cap_preserves_extension(self) -> None:
        name = render_filename(
            "{title}", {"title": "x" * 500}, extension="pdf", max_length=60
        )
        self.assertLessEqual(len(name), 60)
        self.assertTrue(name.endswith(".pdf"))

    def test_unique_path_appends_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            first = unique_path(directory, "report.pdf")
            self.assertEqual(first.name, "report.pdf")
            first.write_bytes(b"1")
            second = unique_path(directory, "report.pdf")
            self.assertEqual(second.name, "report (2).pdf")
            second.write_bytes(b"2")
            third = unique_path(directory, "report.pdf")
            self.assertEqual(third.name, "report (3).pdf")


if __name__ == "__main__":
    unittest.main()
