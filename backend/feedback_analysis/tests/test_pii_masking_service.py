"""Tests for the Azure AI Language PII masking service.

These mock the HTTP layer (no real Azure call) and exercise the gating and the
fail-open behavior, which are the parts most likely to regress.
"""

from __future__ import annotations

import unittest
from unittest import mock

from feedback_analysis.services import pii_masking_service as svc


def _resp(documents):
    r = mock.MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"results": {"documents": documents}}
    return r


class PiiMaskingTests(unittest.TestCase):
    def test_disabled_returns_input_unchanged_and_makes_no_call(self):
        with mock.patch.dict("os.environ", {"PII_MASKING_ENABLED": "false"}, clear=False), \
             mock.patch.object(svc.requests, "post") as post:
            out = svc.mask_texts(["jane@corp.com"])
        self.assertEqual(out, ["jane@corp.com"])
        post.assert_not_called()

    def test_enabled_but_unconfigured_fails_open(self):
        env = {"PII_MASKING_ENABLED": "true", "PII_LANGUAGE_ENDPOINT": "", "PII_LANGUAGE_KEY": ""}
        with mock.patch.dict("os.environ", env, clear=False), \
             mock.patch.object(svc.requests, "post") as post:
            out = svc.mask_texts(["jane@corp.com"])
        self.assertEqual(out, ["jane@corp.com"])
        post.assert_not_called()

    def test_enabled_and_configured_redacts(self):
        env = {
            "PII_MASKING_ENABLED": "true",
            "PII_LANGUAGE_ENDPOINT": "https://lang.cognitiveservices.azure.com",
            "PII_LANGUAGE_KEY": "k",
        }
        documents = [
            {"id": "0", "redactedText": "Email me at ***"},
            {"id": "1", "redactedText": "Call *** today"},
        ]
        with mock.patch.dict("os.environ", env, clear=False), \
             mock.patch.object(svc.requests, "post", return_value=_resp(documents)) as post:
            out = svc.mask_texts(["Email me at jane@corp.com", "Call 555-1234 today"])
        self.assertEqual(out, ["Email me at ***", "Call *** today"])
        post.assert_called_once()

    def test_http_error_fails_open_keeps_originals(self):
        env = {
            "PII_MASKING_ENABLED": "true",
            "PII_LANGUAGE_ENDPOINT": "https://lang.cognitiveservices.azure.com",
            "PII_LANGUAGE_KEY": "k",
        }
        with mock.patch.dict("os.environ", env, clear=False), \
             mock.patch.object(svc.requests, "post", side_effect=RuntimeError("boom")):
            out = svc.mask_texts(["jane@corp.com", "bob@corp.com"])
        self.assertEqual(out, ["jane@corp.com", "bob@corp.com"])

    def test_partial_response_only_redacts_returned_rows(self):
        env = {
            "PII_MASKING_ENABLED": "true",
            "PII_LANGUAGE_ENDPOINT": "https://lang.cognitiveservices.azure.com",
            "PII_LANGUAGE_KEY": "k",
            "PII_MASKING_BATCH_SIZE": "5",
        }
        # API returns a redaction only for row 0; row 1 must keep its original text.
        documents = [{"id": "0", "redactedText": "***"}]
        with mock.patch.dict("os.environ", env, clear=False), \
             mock.patch.object(svc.requests, "post", return_value=_resp(documents)):
            out = svc.mask_texts(["jane@corp.com", "keep-me@corp.com"])
        self.assertEqual(out, ["***", "keep-me@corp.com"])

    def test_empty_input_short_circuits(self):
        with mock.patch.object(svc.requests, "post") as post:
            self.assertEqual(svc.mask_texts([]), [])
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
