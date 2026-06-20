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
from unittest import mock

from feedback_analysis.file_extractors import extract_comments_from_text
from feedback_analysis.services import column_classifier_service
from feedback_analysis.services.column_classifier_service import (
    build_structured_comments,
    classify_columns,
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


class MultiTextColumnTests(unittest.TestCase):
    """A CSV with more than one end-user text column must keep all of them."""

    def _classification(self):
        return {
            "primary_text": ["description", "additional_comments"],
            "context": ["plan"],
            "noise": [],
            "taxonomy_seed_column": None,
        }

    def test_both_text_columns_are_concatenated_and_labeled(self):
        rows = [
            {
                "description": "Cannot log into the travel system.",
                "additional_comments": "Still no response after two days.",
                "plan": "Pro",
            }
        ]
        structured, _seeds = build_structured_comments(rows, self._classification())

        self.assertEqual(len(structured), 1)
        text = structured[0]["text"]
        # Each part is labeled by its column so the downstream LLM can tell them apart.
        self.assertIn("Description: Cannot log into the travel system.", text)
        self.assertIn("Additional Comments: Still no response after two days.", text)
        # Context still folds into the enriched bracket prefix.
        self.assertIn("Plan: Pro", structured[0]["enriched_text"])

    def test_blank_second_column_is_skipped_no_empty_label(self):
        rows = [
            {
                "description": "App crashes on upload.",
                "additional_comments": float("nan"),  # pandas blank
                "plan": "Free",
            }
        ]
        structured, _seeds = build_structured_comments(rows, self._classification())

        text = structured[0]["text"]
        self.assertIn("Description: App crashes on upload.", text)
        # No empty "Additional Comments:" section and no literal "nan".
        self.assertNotIn("Additional Comments:", text)
        self.assertNotIn("nan", text.lower())

    def test_row_blank_in_all_text_columns_is_skipped(self):
        rows = [
            {"description": float("nan"), "additional_comments": None, "plan": "Pro"},
            {"description": "Real feedback", "additional_comments": None, "plan": "Pro"},
        ]
        structured, _seeds = build_structured_comments(rows, self._classification())
        self.assertEqual(len(structured), 1)
        # Labeling stays consistent across rows: a multi-text file labels every
        # row, even one where only a single text column has content.
        self.assertEqual(structured[0]["text"], "Description: Real feedback")

    def test_single_column_list_keeps_raw_text_no_label(self):
        # A one-element list must behave exactly like the legacy string shape.
        classification = {
            "primary_text": ["comment"],
            "context": [],
            "noise": [],
            "taxonomy_seed_column": None,
        }
        rows = [{"comment": "Just works great"}]
        structured, _seeds = build_structured_comments(rows, classification)
        self.assertEqual(structured[0]["text"], "Just works great")
        self.assertEqual(structured[0]["enriched_text"], "Just works great")


class ClassifyColumnsNoFallbackTests(unittest.TestCase):
    """With the heuristic removed, the classifier must fail loudly or return empty."""

    _ROWS = [{"comment": "App keeps crashing on launch", "id": "INC1"}]

    def _patch_llm(self, *, raises=None, content=None):
        """Patch get_azure_client so the create() call raises or returns `content`."""
        client = mock.MagicMock()
        if raises is not None:
            client.chat.completions.create.side_effect = raises
        else:
            completion = mock.MagicMock()
            completion.choices = [mock.MagicMock()]
            completion.choices[0].message.content = content
            client.chat.completions.create.return_value = completion
        outer = mock.MagicMock()
        outer.get_client.return_value = client
        return mock.patch.object(
            column_classifier_service, "get_azure_client", return_value=outer
        )

    def test_llm_failure_raises_not_guesses(self):
        with self._patch_llm(raises=RuntimeError("Azure down")):
            with self.assertRaises(RuntimeError):
                classify_columns(["comment", "id"], self._ROWS)

    def test_no_text_column_returns_empty(self):
        # Valid JSON, but the model picked a column that isn't a header.
        with self._patch_llm(content='{"primary_text": ["nonexistent"], "context": [], "noise": []}'):
            result = classify_columns(["comment", "id"], self._ROWS)
        self.assertEqual(result["primary_text"], [])

    def test_unparseable_response_returns_empty(self):
        with self._patch_llm(content="not json at all"):
            result = classify_columns(["comment", "id"], self._ROWS)
        self.assertEqual(result["primary_text"], [])

    def test_clean_response_classifies(self):
        with self._patch_llm(content='{"primary_text": ["comment"], "context": [], "noise": ["id"], "taxonomy_seed_column": null}'):
            result = classify_columns(["comment", "id"], self._ROWS)
        self.assertEqual(result["primary_text"], ["comment"])
        self.assertEqual(result["source"], "llm")


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
