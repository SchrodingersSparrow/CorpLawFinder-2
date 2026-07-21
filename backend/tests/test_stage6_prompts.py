"""Stage 6 tests: prompt building and tolerant answer parsing (pure)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.ai.prompts import build_prompt, parse_response  # noqa: E402

KNOWN_AUTH = ["RBI", "SEBI", "MCA"]
KNOWN_TYPES = ["Master Direction", "Circular", "Notification"]

GOOD_ANSWER = json.dumps({
    "one_line_summary": "RBI tightens KYC updation timelines for banks.",
    "detailed_summary": "The direction requires regulated entities to…",
    "title": "Master Direction – KYC Direction, 2016 (Updated)",
    "authority": "rbi",
    "doc_type": "master direction",
    "doc_date": "15 July 2026",
    "circular_no": "DOR.AML.REC.12/14.01.001/2026-27",
    "language": "en",
    "topics": ["Banking – KYC/AML", "Compliance", "Banking – KYC/AML"],
    "keywords": ["KYC", "re-KYC", "V-CIP", "periodic updation"],
    "confidence": 0.87,
})


class TestBuildPrompt(unittest.TestCase):
    def test_contains_schema_known_lists_context_and_text(self) -> None:
        prompt = build_prompt(
            title="Existing title", authority="RBI", doc_type=None,
            filename="x.pdf", text="Document body here.", max_chars=1000,
            known_authorities=KNOWN_AUTH, known_doc_types=KNOWN_TYPES,
            known_topics=["Compliance"],
        )
        self.assertIn('"one_line_summary"', prompt)
        self.assertIn('"confidence"', prompt)
        self.assertIn("Master Direction", prompt)      # known lists included
        self.assertIn("Existing title", prompt)
        self.assertIn("Document body here.", prompt)
        self.assertIn("JSON object and nothing else", prompt.replace("\n", " "))

    def test_long_text_is_truncated_with_marker(self) -> None:
        prompt = build_prompt(
            title=None, authority=None, doc_type=None, filename="x.pdf",
            text="y" * 500, max_chars=100,
            known_authorities=[], known_doc_types=[], known_topics=[],
        )
        self.assertIn("[…document truncated…]", prompt)
        self.assertNotIn("y" * 200, prompt)


class TestParseResponse(unittest.TestCase):
    def parse(self, raw: str):
        return parse_response(
            raw, known_authorities=KNOWN_AUTH, known_doc_types=KNOWN_TYPES
        )

    def test_clean_json_normalises_everything(self) -> None:
        parsed = self.parse(GOOD_ANSWER)
        self.assertEqual(parsed["authority"], "RBI")               # case mapped
        self.assertEqual(parsed["doc_type"], "Master Direction")   # case mapped
        self.assertEqual(parsed["doc_date"], "2026-07-15")         # date parsed
        self.assertEqual(parsed["confidence"], 0.87)
        self.assertEqual(parsed["topics"],
                         ["Banking – KYC/AML", "Compliance"])      # deduped
        self.assertEqual(len(parsed["keywords"]), 4)

    def test_code_fences_and_chatter_tolerated(self) -> None:
        wrapped = "Sure! Here is the JSON:\n```json\n" + GOOD_ANSWER + "\n```\nHope it helps."
        parsed = self.parse(wrapped)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["authority"], "RBI")

    def test_braces_inside_strings_do_not_confuse_matching(self) -> None:
        tricky = json.dumps({
            "one_line_summary": "Uses {braces} and \"quotes\" inside.",
            "confidence": 1.4,   # clamped
            "topics": "not-a-list",
        })
        parsed = self.parse("noise " + tricky + " trailing")
        self.assertEqual(parsed["one_line_summary"], 'Uses {braces} and "quotes" inside.')
        self.assertEqual(parsed["confidence"], 1.0)
        self.assertEqual(parsed["topics"], [])

    def test_unknown_authority_kept_short_or_dropped(self) -> None:
        parsed = self.parse(json.dumps({
            "one_line_summary": "s", "authority": "Insurance Regulator",
            "doc_type": "z" * 200,
        }))
        self.assertEqual(parsed["authority"], "Insurance Regulator")  # free text ok
        self.assertIsNone(parsed["doc_type"])                          # absurd → None

    def test_bad_or_missing_json_returns_none(self) -> None:
        self.assertIsNone(self.parse("I could not read the document, sorry."))
        self.assertIsNone(self.parse("{broken json"))
        self.assertIsNone(self.parse(""))

    def test_null_fields_survive_as_none(self) -> None:
        parsed = self.parse(json.dumps({
            "one_line_summary": "s", "doc_date": None, "confidence": None,
        }))
        self.assertIsNone(parsed["doc_date"])
        self.assertIsNone(parsed["confidence"])
        self.assertIsNone(parsed["authority"])

    def test_unparseable_date_becomes_none(self) -> None:
        parsed = self.parse(json.dumps({
            "one_line_summary": "s", "doc_date": "sometime last year",
        }))
        self.assertIsNone(parsed["doc_date"])


if __name__ == "__main__":
    unittest.main()
