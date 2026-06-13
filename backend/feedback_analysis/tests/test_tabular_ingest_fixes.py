"""Tests for tabular / text ingest data-quality handling.

These cover edge cases that arise when pandas reads CSV/Excel/structured-JSON:

  * Blank cells become float ``NaN``; a blank dimension must be dropped, not
    serialized to the literal "nan".
  * pandas widens an integer column to float64 when any cell is blank, so an
    integral ``5.0`` must collapse back to ``5`` rather than render as "5.0".
  * ``extract_comments_from_text`` must tolerate a CP-1252 .txt upload and
    decode the real characters instead of producing mojibake.

They exercise the pure functions directly (no Django ORM / Celery / LLM), so
they run fast and deterministically.
"""

from __future__ import annotations

import io
import math
import unittest

from feedback_analysis.file_extractors import extract_comments_from_text
from feedback_analysis.services.column_classifier_service import (
    build_structured_comments,
)


class BlankDimensionCellTests(unittest.TestCase):
    """A blank dimension cell must not become the string 'nan'."""

    def _classification(self):
        return {
            "primary_text": "comment",
            "context": ["plan"],
            "noise": [],
            "taxonomy_seed_column": None,
        }

    def test_nan_dimension_is_dropped_not_stringified(self):
        # pandas represents a blank cell as float('nan').
        rows = [
            {"comment": "Great filters", "plan": "Pro"},
            {"comment": "Slow on mobile", "plan": float("nan")},
        ]
        structured, _seeds = build_structured_comments(rows, self._classification())

        self.assertEqual(len(structured), 2)
        # Row with a real value keeps it.
        self.assertEqual(structured[0]["dimensions"].get("plan"), "Pro")
        # Row whose plan was NaN must NOT carry a 'plan' dimension at all,
        # and certainly not the literal string "nan".
        self.assertNotIn("plan", structured[1]["dimensions"])
        self.assertNotIn("nan", structured[1]["enriched_text"].lower().split())
        # The bracket prefix should not be emitted for an all-blank context row.
        self.assertEqual(structured[1]["enriched_text"], "Slow on mobile")

    def test_none_dimension_still_dropped(self):
        # df.where(pd.notna(df), None) converts NaN -> None upstream; that path
        # must keep working too.
        rows = [{"comment": "Nice UI", "plan": None}]
        structured, _seeds = build_structured_comments(rows, self._classification())
        self.assertNotIn("plan", structured[0]["dimensions"])

    def test_nan_primary_text_row_is_skipped(self):
        rows = [
            {"comment": float("nan"), "plan": "Pro"},
            {"comment": "Real feedback", "plan": "Pro"},
        ]
        structured, _seeds = build_structured_comments(rows, self._classification())
        # The NaN-comment row is dropped, not stored as text "nan".
        self.assertEqual(len(structured), 1)
        self.assertEqual(structured[0]["text"], "Real feedback")


class IntegerColumnTests(unittest.TestCase):
    """An integral float (5.0) must collapse back to int (5)."""

    def _classification(self):
        return {
            "primary_text": "comment",
            "context": ["rating"],
            "noise": [],
            "taxonomy_seed_column": None,
        }

    def test_integral_float_rating_renders_as_int(self):
        # pandas reads the rating column as float64 because another row is blank.
        rows = [
            {"comment": "Loved it", "rating": 5.0},
            {"comment": "Meh", "rating": float("nan")},
        ]
        structured, _seeds = build_structured_comments(rows, self._classification())

        self.assertEqual(structured[0]["dimensions"]["rating"], 5)
        self.assertIsInstance(structured[0]["dimensions"]["rating"], int)
        self.assertIn("Rating: 5", structured[0]["enriched_text"])
        self.assertNotIn("5.0", structured[0]["enriched_text"])
        # Blank rating row carries no rating dimension.
        self.assertNotIn("rating", structured[1]["dimensions"])

    def test_genuine_decimal_is_preserved(self):
        rows = [{"comment": "Half star", "rating": 4.5}]
        structured, _seeds = build_structured_comments(rows, self._classification())
        self.assertEqual(structured[0]["dimensions"]["rating"], 4.5)
        self.assertIn("Rating: 4.5", structured[0]["enriched_text"])

    def test_seed_value_integral_float_collapses(self):
        classification = {
            "primary_text": "comment",
            "context": [],
            "noise": [],
            "taxonomy_seed_column": "feature_id",
        }
        rows = [
            {"comment": "a", "feature_id": 3.0},
            {"comment": "b", "feature_id": float("nan")},
        ]
        _structured, seeds = build_structured_comments(rows, classification)
        # 3.0 -> "3", and NaN seed dropped.
        self.assertEqual(seeds, ["3"])


class TextDecodeTests(unittest.TestCase):
    """A CP-1252 .txt upload must decode to real characters."""

    def test_cp1252_smart_quotes_decode_correctly(self):
        # 0x93/0x94 are CP-1252 curly double quotes, 0x97 is an em dash.
        # These bytes are NOT valid UTF-8, so a strict utf-8 decode would have
        # produced U+FFFD replacement chars (mojibake).
        raw = b"The app is \x93great\x94 \x97 really love it"
        comments = extract_comments_from_text(io.BytesIO(raw))

        self.assertEqual(len(comments), 1)
        decoded = comments[0]
        self.assertIn("“", decoded)  # left double quote
        self.assertIn("”", decoded)  # right double quote
        self.assertIn("—", decoded)  # em dash
        self.assertNotIn("�", decoded)  # no replacement chars

    def test_plain_utf8_still_works(self):
        raw = "Line one\nLine two".encode("utf-8")
        comments = extract_comments_from_text(io.BytesIO(raw))
        self.assertEqual(comments, ["Line one", "Line two"])

    def test_utf8_bom_is_stripped(self):
        raw = "﻿First line".encode("utf-8")
        comments = extract_comments_from_text(io.BytesIO(raw))
        self.assertEqual(comments, ["First line"])


if __name__ == "__main__":
    unittest.main()
