"""Reasoning & Explainability: rationale threads into the evidence buckets.

Verifies that per-comment LLM rationale flows into the per-feature evidence
buckets as {text, rationale} objects, while the narration prompt still receives
plain comment strings. No DB/Azure — pure data-shape logic.
"""
from django.test import SimpleTestCase

from feedback_analysis.services.local_processing_service import (
    LocalProcessingService, AspectMatch, AspectSentiment,
)
from aiCore.services.sentiment_types import SentimentResult


def _match(cid, text, aspect, sentiment, rationale):
    sr = SentimentResult(sentiment=sentiment, confidence="HIGH", raw_scores={}, processing_time=0.0)
    asp = AspectSentiment(
        aspect=aspect, sentiment=sentiment, confidence="HIGH",
        source_sentence=text, raw_scores={},
    )
    return AspectMatch(
        comment_id=cid, comment_text=text, matched_aspects=[aspect],
        aspect_scores={aspect: 0.9}, comment_sentiment=sr,
        aspect_sentiments={aspect: asp}, rationale=rationale,
    )


class EvidenceRationaleTests(SimpleTestCase):
    def setUp(self):
        self.svc = LocalProcessingService()

    def test_buckets_carry_text_and_rationale(self):
        matches = [
            _match(0, "great login flow", "Login", "POSITIVE", "praises the login experience"),
            _match(1, "cannot log in", "Login", "NEGATIVE", "reports being unable to log in"),
        ]
        buckets = self.svc._build_feature_comment_buckets(matches)
        key = next(iter(buckets))
        self.assertEqual(
            buckets[key]["positive"][0],
            {"text": "great login flow", "rationale": "praises the login experience"},
        )
        self.assertEqual(buckets[key]["negative"][0]["rationale"], "reports being unable to log in")

    def test_missing_rationale_becomes_empty_string(self):
        buckets = self.svc._build_feature_comment_buckets(
            [_match(0, "neutral note", "Login", "NEUTRAL", "")]
        )
        key = next(iter(buckets))
        self.assertEqual(buckets[key]["neutral"][0], {"text": "neutral note", "rationale": ""})

    def test_narration_samples_are_plain_strings(self):
        """The narration prompt must get bare comment text, not {text, rationale} objects."""
        matches = [_match(0, "cannot log in", "Login", "NEGATIVE", "reports a login failure")]
        buckets = self.svc._build_feature_comment_buckets(matches)
        samples = self.svc._sample_comments_for_narration(buckets)
        key = next(iter(samples))
        self.assertTrue(all(isinstance(s, str) for s in samples[key]))
        self.assertIn("cannot log in", samples[key])
