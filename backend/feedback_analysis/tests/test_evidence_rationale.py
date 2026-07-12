"""Sample-comment evidence buckets carry plain text (rationale removed).

Verifies per-comment evidence flows into the per-feature buckets as {text}
objects (no rationale), and the narration prompt receives plain comment
strings. No DB/Azure — pure data-shape logic.
"""
from django.test import SimpleTestCase

from feedback_analysis.services.local_processing_service import (
    LocalProcessingService, AspectMatch, AspectSentiment,
)
from aiCore.services.sentiment_types import SentimentResult


def _match(cid, text, aspect, sentiment):
    sr = SentimentResult(sentiment=sentiment, confidence="HIGH", raw_scores={}, processing_time=0.0)
    asp = AspectSentiment(
        aspect=aspect, sentiment=sentiment, confidence="HIGH",
        source_sentence=text, raw_scores={},
    )
    return AspectMatch(
        comment_id=cid, comment_text=text, matched_aspects=[aspect],
        aspect_scores={aspect: 0.9}, comment_sentiment=sr,
        aspect_sentiments={aspect: asp},
    )


class EvidenceBucketTests(SimpleTestCase):
    def setUp(self):
        self.svc = LocalProcessingService()

    def test_buckets_carry_text_only(self):
        matches = [
            _match(0, "great login flow", "Login", "POSITIVE"),
            _match(1, "cannot log in", "Login", "NEGATIVE"),
        ]
        buckets = self.svc._build_feature_comment_buckets(matches)
        key = next(iter(buckets))
        self.assertEqual(buckets[key]["positive"][0], {"text": "great login flow"})
        self.assertNotIn("rationale", buckets[key]["negative"][0])

    def test_narration_samples_are_plain_strings(self):
        """The narration prompt must get bare comment text, not objects."""
        matches = [_match(0, "cannot log in", "Login", "NEGATIVE")]
        buckets = self.svc._build_feature_comment_buckets(matches)
        samples = self.svc._sample_comments_for_narration(buckets)
        key = next(iter(samples))
        self.assertTrue(all(isinstance(s, str) for s in samples[key]))
        self.assertIn("cannot log in", samples[key])
