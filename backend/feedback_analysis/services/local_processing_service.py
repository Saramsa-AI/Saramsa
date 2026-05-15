"""
Local ML Processing Service

Orchestrates the local ML pipeline:
  1. Bi-encoder aspect classification (all-MiniLM-L6-v2 cosine similarity)
  2. Aspect-relative sentiment (sentence-level sentiment per matched aspect)
  3. Aggregation + keyword extraction
  4. Lean GPT-5-mini synthesis (aggregates + evidence samples only)
"""

import logging
import time
import re
import numpy as np
from typing import List, Dict, Any, Tuple, Optional, Callable
from dataclasses import dataclass, field
from collections import defaultdict
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.feature_extraction.text import TfidfVectorizer

from aiCore.services.aspect_service_factory import get_aspect_service
from aiCore.services.embedding_service import EmbeddingService
from aiCore.services.local_sentiment_service import LocalSentimentService, SentimentResult
from .narration_service import get_narration_service

logger = logging.getLogger(__name__)

# Sentence splitting regex: split on . ! ? followed by space or end-of-string,
# but not on abbreviations like "Mr." or "e.g."
_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z])|(?<=[.!?])$')

# Common English stopwords for keyword extraction (expanded for feedback domain)
_STOPWORDS = frozenset({
    # --- pronouns, prepositions, auxiliaries, determiners ---
    'i', 'me', 'my', 'myself', 'we', 'our', 'ours', 'ourselves', 'you', 'your',
    'yours', 'yourself', 'yourselves', 'he', 'him', 'his', 'himself', 'she', 'her',
    'hers', 'herself', 'it', 'its', 'itself', 'they', 'them', 'their', 'theirs',
    'themselves', 'what', 'which', 'who', 'whom', 'this', 'that', 'these', 'those',
    'am', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had',
    'having', 'do', 'does', 'did', 'doing', 'a', 'an', 'the', 'and', 'but', 'if',
    'or', 'because', 'as', 'until', 'while', 'of', 'at', 'by', 'for', 'with',
    'about', 'against', 'between', 'through', 'during', 'before', 'after', 'above',
    'below', 'to', 'from', 'up', 'down', 'in', 'out', 'on', 'off', 'over', 'under',
    'again', 'further', 'then', 'once', 'here', 'there', 'when', 'where', 'why',
    'how', 'all', 'both', 'each', 'few', 'more', 'most', 'other', 'some', 'such',
    'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very', 's',
    't', 'can', 'will', 'just', 'don', 'should', 'now', 'd', 'll', 'm', 'o', 're',
    've', 'y', 'ain', 'aren', 'couldn', 'didn', 'doesn', 'hadn', 'hasn', 'haven',
    'isn', 'ma', 'mightn', 'mustn', 'needn', 'shan', 'shouldn', 'wasn', 'weren',
    'won', 'wouldn', 'also', 'would', 'could', 'much', 'really', 'get', 'got',
    'even', 'still', 'well', 'back', 'like', 'one', 'two', 'go', 'going', 'went',
    'come', 'came', 'make', 'made', 'take', 'took', 'know', 'knew', 'think',
    'thought', 'want', 'said', 'say', 'way', 'thing', 'things', 'lot', 'every',
    # --- generic feedback / filler verbs & nouns ---
    'app', 'application', 'product', 'service', 'use', 'used', 'using', 'user',
    'users', 'feature', 'features', 'please', 'need', 'needs', 'needed',
    'work', 'works', 'working', 'worked', 'try', 'tried', 'trying',
    'time', 'times', 'able', 'always', 'never', 'anything', 'something',
    'everything', 'nothing', 'someone', 'anyone', 'everyone',
    'see', 'seen', 'look', 'looking', 'find', 'found', 'give', 'given',
    'help', 'helped', 'put', 'set', 'new', 'old', 'first', 'last',
    'many', 'another', 'often', 'since', 'already', 'yet', 'let',
    'keep', 'start', 'started', 'end', 'seems', 'seem', 'actually',
    'maybe', 'though', 'however', 'instead', 'rather', 'quite',
    'enough', 'sure', 'right', 'left', 'getting', 'using',
    # --- generic UI / product noise ---
    'button', 'page', 'screen', 'click', 'clicked', 'clicking',
    'option', 'options', 'menu', 'tab', 'section', 'update', 'updated',
})

_WORD_RE = re.compile(r'[a-zA-Z]{3,}')


@dataclass
class AspectSentiment:
    """Sentiment result for a specific aspect within a comment."""
    aspect: str
    sentiment: str        # POSITIVE / NEGATIVE / NEUTRAL
    confidence: str       # HIGH / MEDIUM / LOW
    source_sentence: str  # The sentence used for this aspect's sentiment
    raw_scores: Dict[str, float] = field(default_factory=dict)


@dataclass
class AspectMatch:
    comment_id: int
    comment_text: str
    matched_aspects: List[str]
    aspect_scores: Dict[str, float]
    comment_sentiment: SentimentResult          # Overall comment-level sentiment
    aspect_sentiments: Dict[str, AspectSentiment] = field(default_factory=dict)  # Per-aspect sentiment


@dataclass
class AggregatedStats:
    aspect_sentiment_counts: Dict[str, Dict[str, int]]
    confidence_distribution: Dict[str, int]
    unmapped_count: int
    unmapped_percentage: float
    total_comments: int
    aspect_keywords: Dict[str, List[str]]
    overall_sentiment: Dict[str, float]


@dataclass
class ProcessingResult:
    matches: List[AspectMatch]
    aggregated_stats: AggregatedStats
    processing_time: float
    model_info: Dict[str, str]
    insights: List[str]
    features: List[Dict[str, Any]]
    work_items: List[Dict[str, Any]]


class LocalProcessingService:
    """
    Orchestrates the local ML pipeline for feedback analysis.

    Pipeline:
      1. Bi-encoder aspect classification (all-MiniLM-L6-v2)
      2. Aspect-relative sentiment (sentence → aspect → sentiment)
      3. Aggregate statistics + extract keywords
      4. Lean GPT-5-mini synthesis (aggregates + evidence only)
    """

    UNMAPPED_WARNING_THRESHOLD = 0.12
    AUTO_REGENERATE_THRESHOLD = 0.70  # If >70% unmapped (i.e. <30% mapped), auto-regenerate taxonomy
    MAX_EVIDENCE_SAMPLES = 30
    SAMPLES_PER_ASPECT = 5

    def __init__(self):
        self.aspect_service = get_aspect_service()
        self.embedding_service = EmbeddingService()
        self.sentiment_service = LocalSentimentService()
        logger.info("LocalProcessingService initialized with aspect classification + aspect-relative sentiment")

    def process_comments(self, comments: List[str], aspects: List[str],
                         company_name: str = "Company", run_id: str = None,
                         is_cancelled: Optional[Callable[[], bool]] = None,
                         user_id: Optional[str] = None,
                         regenerate_callback: Optional[Callable[[List[str]], List[str]]] = None) -> ProcessingResult:
        """Run the full pipeline and return a ProcessingResult.

        Args:
            regenerate_callback: Optional callback that takes comments and returns new aspects.
                Called when unmapped rate is too high (taxonomy mismatch detected).
        """
        start_time = time.time()

        if not comments:
            raise ValueError("Comments list cannot be empty")
        if not aspects:
            raise ValueError("Aspects list cannot be empty")

        if run_id is None:
            run_id = f"run_{int(time.time())}"

        logger.info(f"Processing {len(comments)} comments with {len(aspects)} aspects (run: {run_id})")

        # Strip bracket metadata before ML processing (20-30% token reduction for NLI + sentiment)
        # The enriched metadata is only useful for LLM narration, not local ML models.
        stripped_comments = [self._strip_bracket_metadata(c) for c in comments]

        # Step 1: Aspect classification (NLI or similarity, via factory)
        similarity_results = self.aspect_service.classify_aspects(
            stripped_comments, aspects, run_id, is_cancelled=is_cancelled
        )

        # Step 1b: Check mapping rate and auto-regenerate taxonomy if too low
        if regenerate_callback is not None:
            unmapped_count = sum(1 for r in similarity_results if not r.get("matched_aspects") or r.get("matched_aspects") == ["UNMAPPED"])
            unmapped_rate = unmapped_count / max(len(similarity_results), 1)
            if unmapped_rate > self.AUTO_REGENERATE_THRESHOLD:
                logger.warning(
                    f"🔄 AUTO-REGENERATE: {unmapped_rate:.1%} unmapped ({unmapped_count}/{len(similarity_results)}) "
                    f"exceeds threshold {self.AUTO_REGENERATE_THRESHOLD:.0%}. "
                    f"Domain mismatch detected. Regenerating taxonomy from comments..."
                )
                try:
                    new_aspects = regenerate_callback(comments)
                    if new_aspects and len(new_aspects) > 0:
                        logger.info(f"✅ Generated {len(new_aspects)} new aspects: {new_aspects[:5]}...")
                        aspects = new_aspects
                        # Re-run NLI with new aspects
                        similarity_results = self.aspect_service.classify_aspects(
                            stripped_comments, aspects, f"{run_id}_regen", is_cancelled=is_cancelled
                        )
                        new_unmapped = sum(1 for r in similarity_results if not r.get("matched_aspects") or r.get("matched_aspects") == ["UNMAPPED"])
                        new_rate = new_unmapped / max(len(similarity_results), 1)
                        logger.info(f"📈 After regeneration: {(1-new_rate):.1%} mapped (was {(1-unmapped_rate):.1%})")
                    else:
                        logger.warning("Regenerate callback returned no aspects; keeping original taxonomy.")
                except Exception as e:
                    logger.exception(f"Auto-regenerate failed: {e}. Continuing with original taxonomy.")

        # Restore original comment text (with brackets) in similarity results for display + LLM narration
        for i, result in enumerate(similarity_results):
            result["comment_text"] = comments[i]

        # Step 2: Aspect-relative sentiment
        # 2a. Get comment-level sentiment for overall counts
        comment_sentiments = self.sentiment_service.classify_batch(stripped_comments)

        # 2b. Compute aspect-relative sentiment (sentence-level, uses stripped text internally)
        combined_matches = self._compute_aspect_relative_sentiment(
            similarity_results, comment_sentiments, aspects, stripped_comments
        )

        # Step 3: aggregate + keywords (now uses per-aspect sentiment)
        aggregated_stats = self._aggregate_results(combined_matches, aspects)

        if aggregated_stats.unmapped_percentage > self.UNMAPPED_WARNING_THRESHOLD:
            logger.warning(
                f"High unmapped rate: {aggregated_stats.unmapped_percentage:.1%} "
                f"({aggregated_stats.unmapped_count}/{aggregated_stats.total_comments}). "
                "Consider updating the aspect taxonomy."
            )

        # Step 4: Unified narration (single GPT entrypoint)
        insights, features, work_items = self._narrate_with_service(
            combined_matches, aggregated_stats, aspects, company_name, user_id=user_id
        )

        processing_time = time.time() - start_time
        logger.info(f"Pipeline completed in {processing_time:.2f}s")

        return ProcessingResult(
            matches=combined_matches,
            aggregated_stats=aggregated_stats,
            processing_time=processing_time,
            model_info={
                "aspect_model": getattr(self.aspect_service, 'MODEL_NAME', 'ensemble'),
                "sentiment_model": getattr(self.sentiment_service, 'MODEL_NAME', 'distilbert'),
                "processing_method": "local_ml_pipeline_aspect_sentiment",
            },
            insights=insights,
            features=features,
            work_items=work_items,
        )

    # ------------------------------------------------------------------
    # Aspect-relative sentiment
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_bracket_metadata(text: str) -> str:
        """
        Strip enriched-comment bracket metadata added by column_classifier.

        Example:
          "[Persona: P1-Analyst | Rating: 4/5 | Feature: Stock Screener]\nThe screener is great."
          → "The screener is great."

        The bracket metadata is useful for LLM narration (helps GPT understand
        context) but adds noise for ML models (NLI, sentiment, embeddings) that
        work better on raw user text.
        """
        if not text or not text.startswith("["):
            return text

        # Find the closing bracket and newline
        bracket_end = text.find("]\n")
        if bracket_end == -1:
            # No bracket pattern found
            return text

        # Return text after the bracket + newline
        return text[bracket_end + 2:].strip()

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        """Split text into sentences. Returns [text] if only one sentence."""
        sentences = _SENTENCE_RE.split(text)
        sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 5]
        return sentences if sentences else [text]

    def _compute_aspect_relative_sentiment(
        self,
        similarity_results: List[Dict[str, Any]],
        comment_sentiments: List[SentimentResult],
        aspects: List[str],
        stripped_comments: List[str],
    ) -> List[AspectMatch]:
        """
        For each comment, find the best-matching sentence per assigned aspect
        and run sentiment on that sentence instead of the whole comment.

        For single-sentence comments, uses the comment-level sentiment directly.

        Args:
            similarity_results: NLI/aspect classification results (comment_text has original with brackets)
            comment_sentiments: Sentiment for stripped comments (already computed on stripped text)
            aspects: List of aspect names
            stripped_comments: Comments with bracket metadata removed (used for sentence splitting)
        """
        embedding_service = self.embedding_service

        # Pre-compute aspect embeddings once
        aspect_embeddings = embedding_service.get_embeddings(aspects)
        aspect_to_idx = {a: i for i, a in enumerate(aspects)}

        # Collect all sentences that need sentiment (deduplicated)
        # Use stripped_comments for sentence splitting (no bracket noise)
        sentence_set: Dict[str, None] = {}  # ordered set via dict
        comment_sentence_map: List[Tuple[List[str], bool]] = []  # (sentences, is_multi)

        for stripped_comment in stripped_comments:
            sentences = self._split_sentences(stripped_comment)
            is_multi = len(sentences) > 1
            comment_sentence_map.append((sentences, is_multi))
            if is_multi:
                for s in sentences:
                    sentence_set[s] = None

        # Batch-embed and batch-sentiment all unique sentences
        unique_sentences = list(sentence_set.keys())
        sentence_sentiment_map: Dict[str, SentimentResult] = {}
        sentence_embedding_map: Dict[str, np.ndarray] = {}

        if unique_sentences:
            logger.info(f"Aspect-relative sentiment: {len(unique_sentences)} unique sentences from multi-sentence comments")
            sent_embeddings = embedding_service.get_embeddings(unique_sentences)
            sent_sentiments = self.sentiment_service.classify_batch(unique_sentences)
            for i, s in enumerate(unique_sentences):
                sentence_sentiment_map[s] = sent_sentiments[i]
                sentence_embedding_map[s] = sent_embeddings[i]

        # Build AspectMatch objects with per-aspect sentiment
        combined_matches = []
        for idx, (sim_result, comment_sentiment) in enumerate(zip(similarity_results, comment_sentiments)):
            sentences, is_multi = comment_sentence_map[idx]
            matched_aspects = sim_result["matched_aspects"]

            aspect_sentiments: Dict[str, AspectSentiment] = {}

            if not is_multi or not matched_aspects:
                # Single sentence or unmapped: use comment-level sentiment for all aspects
                for aspect in matched_aspects:
                    aspect_sentiments[aspect] = AspectSentiment(
                        aspect=aspect,
                        sentiment=comment_sentiment.sentiment,
                        confidence=comment_sentiment.confidence,
                        source_sentence=sim_result["comment_text"],
                        raw_scores=comment_sentiment.raw_scores,
                    )
            else:
                # Multi-sentence: find best sentence per aspect
                sent_embs = np.array([sentence_embedding_map[s] for s in sentences])

                for aspect in matched_aspects:
                    a_idx = aspect_to_idx.get(aspect)
                    if a_idx is None:
                        # Aspect not in original list (shouldn't happen)
                        aspect_sentiments[aspect] = AspectSentiment(
                            aspect=aspect,
                            sentiment=comment_sentiment.sentiment,
                            confidence=comment_sentiment.confidence,
                            source_sentence=sim_result["comment_text"],
                            raw_scores=comment_sentiment.raw_scores,
                        )
                        continue

                    # Cosine similarity between each sentence and this aspect
                    aspect_emb = aspect_embeddings[a_idx].reshape(1, -1)
                    sims = cosine_similarity(sent_embs, aspect_emb).flatten()
                    best_sent_idx = int(np.argmax(sims))
                    best_sentence = sentences[best_sent_idx]

                    sent_sentiment = sentence_sentiment_map[best_sentence]
                    aspect_sentiments[aspect] = AspectSentiment(
                        aspect=aspect,
                        sentiment=sent_sentiment.sentiment,
                        confidence=sent_sentiment.confidence,
                        source_sentence=best_sentence,
                        raw_scores=sent_sentiment.raw_scores,
                    )

            combined_matches.append(AspectMatch(
                comment_id=sim_result["comment_id"],
                comment_text=sim_result["comment_text"],
                matched_aspects=matched_aspects,
                aspect_scores=sim_result["aspect_scores"],
                comment_sentiment=comment_sentiment,
                aspect_sentiments=aspect_sentiments,
            ))

        multi_count = sum(1 for _, is_multi in comment_sentence_map if is_multi)
        logger.info(
            f"Aspect-relative sentiment complete: {multi_count}/{len(similarity_results)} "
            f"multi-sentence comments processed"
        )

        return combined_matches

    # ------------------------------------------------------------------
    # Aggregation + keyword extraction
    # ------------------------------------------------------------------

    def _aggregate_results(self, matches: List[AspectMatch], aspects: List[str]) -> AggregatedStats:
        aspect_sentiment_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        confidence_dist: Dict[str, int] = defaultdict(int)
        overall_counts: Dict[str, int] = defaultdict(int)
        unmapped_count = 0
        total = len(matches)

        # Collect comment texts per aspect for keyword extraction
        aspect_comments: Dict[str, List[str]] = defaultdict(list)

        for m in matches:
            # Overall sentiment uses comment-level (1 per comment, no inflation)
            overall_counts[m.comment_sentiment.sentiment] += 1
            confidence_dist[m.comment_sentiment.confidence] += 1

            if not m.matched_aspects:
                unmapped_count += 1
                continue

            for aspect in m.matched_aspects:
                if aspect == "UNMAPPED":
                    unmapped_count += 1
                else:
                    # Use aspect-relative sentiment for per-aspect counts
                    asp_sent = m.aspect_sentiments.get(aspect)
                    if asp_sent:
                        aspect_sentiment_counts[aspect][asp_sent.sentiment] += 1
                    else:
                        # Fallback to comment-level
                        aspect_sentiment_counts[aspect][m.comment_sentiment.sentiment] += 1
                    aspect_comments[aspect].append(m.comment_text)

        overall_sentiment = {s.lower(): (c / total) * 100 for s, c in overall_counts.items()} if total else {}

        # Extract keywords per aspect using cross-aspect TF-IDF
        aspect_keywords = self._extract_keywords_tfidf(aspect_comments, aspects, top_n=10)

        return AggregatedStats(
            aspect_sentiment_counts=dict(aspect_sentiment_counts),
            confidence_distribution=dict(confidence_dist),
            unmapped_count=unmapped_count,
            unmapped_percentage=unmapped_count / total if total else 0,
            total_comments=total,
            aspect_keywords=aspect_keywords,
            overall_sentiment=overall_sentiment,
        )

    @staticmethod
    def _extract_keywords_tfidf(
        aspect_comments: Dict[str, List[str]],
        aspects: List[str],
        top_n: int = 10,
    ) -> Dict[str, List[str]]:
        """Extract distinctive keywords per aspect using cross-aspect TF-IDF.

        Each aspect's comments are joined into one "document". TF-IDF across
        all aspect documents downranks words that appear everywhere and surfaces
        terms that are distinctive to each aspect. Unigrams + bigrams are used
        so phrases like "slow loading" or "poor navigation" can emerge.
        """
        ordered_aspects = [a for a in aspects if aspect_comments.get(a)]
        if not ordered_aspects:
            return {a: [] for a in aspects}

        # Build one document per aspect (joined comment texts)
        corpus = [" ".join(aspect_comments[a]) for a in ordered_aspects]

        # Single-aspect edge case: TF-IDF needs ≥2 docs for IDF to matter.
        # Add a background doc from ALL texts so single-aspect still works.
        if len(corpus) == 1:
            corpus.append(corpus[0])  # duplicate so vectorizer doesn't fail

        try:
            vectorizer = TfidfVectorizer(
                stop_words=list(_STOPWORDS),
                token_pattern=r'[a-zA-Z]{3,}',
                ngram_range=(1, 2),
                max_features=500,
                min_df=1,
                sublinear_tf=True,
            )
            tfidf_matrix = vectorizer.fit_transform(corpus)
            feature_names = vectorizer.get_feature_names_out()
        except ValueError:
            # Empty vocabulary after stopword removal
            return {a: [] for a in aspects}

        # Build a lookup from term -> index for bigram quality checks
        term_to_idx = {term: i for i, term in enumerate(feature_names)}

        # Compute a per-aspect score threshold: median of non-zero scores.
        # Bigrams whose best component is above this threshold are considered
        # meaningful (at least one word is distinctive to this aspect).
        result: Dict[str, List[str]] = {}
        max_bigrams_per_aspect = max(2, top_n // 3)  # up to ~30% bigrams

        for idx, aspect in enumerate(ordered_aspects):
            row = tfidf_matrix[idx].toarray().flatten()
            # Get indices sorted by TF-IDF score descending
            top_indices = row.argsort()[::-1]

            # Filter: skip terms that contain aspect name tokens (avoid "pricing" in pricing aspect)
            aspect_tokens = set(aspect.lower().replace("_", " ").split())
            keywords = []
            bigram_count = 0
            seen_unigrams = set()  # track unigrams already covered by accepted bigrams
            for i in top_indices:
                if row[i] <= 0:
                    break
                term = feature_names[i]
                term_tokens = set(term.split())
                # Skip if the term is just the aspect name or a token from it
                if term_tokens <= aspect_tokens:
                    continue

                # Bigram quality gate: accept a bigram if at least one of its
                # component words has a non-trivial score in this aspect AND
                # neither component is a stopword. This filters genuine noise
                # like "the quick" while keeping "slow loading", "poor quality".
                if " " in term:
                    if bigram_count >= max_bigrams_per_aspect:
                        continue  # enough bigrams already
                    parts = term.split()
                    # Both parts must be non-stopwords (already handled by vectorizer,
                    # but double-check for short generic words)
                    if any(len(p) <= 3 for p in parts):
                        continue
                    # At least one part must score > 0 in this aspect
                    best_part_score = max(
                        (row[term_to_idx[p]] for p in parts if p in term_to_idx),
                        default=0,
                    )
                    if best_part_score <= 0:
                        continue
                    bigram_count += 1

                # Skip unigrams that are already part of an accepted bigram
                if " " not in term and term in seen_unigrams:
                    continue

                keywords.append(term)
                # Track component unigrams of accepted bigrams
                if " " in term:
                    for p in term.split():
                        seen_unigrams.add(p)
                if len(keywords) >= top_n:
                    break
            result[aspect] = keywords

        # Fill aspects with no comments
        for aspect in aspects:
            if aspect not in result:
                result[aspect] = []

        return result

    # ------------------------------------------------------------------
    # Unified narration (single GPT entrypoint)
    # ------------------------------------------------------------------

    def _narrate_with_service(self, matches, aggregated_stats, aspects, company_name, user_id: Optional[str] = None):
        """Call unified NarrationService with lean payload.

        Generates work item candidates deterministically BEFORE the GPT call
        so that insights, feature descriptions, and work item narratives are
        all produced in a single narration request.
        """
        from work_items.services.work_item_candidate_service import get_work_item_candidate_service

        narration_service = get_narration_service()

        features = []
        aspect_key_map = {self._normalize_aspect_key(a): a for a in aspects if a}
        for aspect, sentiment_counts in aggregated_stats.aspect_sentiment_counts.items():
            if aspect == "UNMAPPED":
                continue
            total = sum(sentiment_counts.values())
            if total == 0:
                continue
            aspect_key = self._normalize_aspect_key(aspect)
            neg_pct = sentiment_counts.get("NEGATIVE", 0) / total
            pos_pct = sentiment_counts.get("POSITIVE", 0) / total
            features.append({
                "aspect_key": aspect_key,
                "metrics": {
                    "comment_count": total,
                    "neg_pct": neg_pct,
                    "pos_pct": pos_pct,
                },
                "keywords": aggregated_stats.aspect_keywords.get(aspect, [])[:5],
            })

        # Build feature comment buckets and sample comments for narration
        feature_comment_buckets = self._build_feature_comment_buckets(matches)
        comment_samples = self._sample_comments_for_narration(feature_comment_buckets)

        # Generate work item candidates deterministically so GPT can narrate them
        # in the same call that produces insights and feature descriptions.
        analysis_for_candidates = {
            "features": [
                {
                    "name": aspect_key_map.get(f["aspect_key"], f["aspect_key"]),
                    "feature": aspect_key_map.get(f["aspect_key"], f["aspect_key"]),
                    "key": f["aspect_key"],
                    "sentiment": {
                        "negative": f["metrics"]["neg_pct"] * 100,
                        "positive": f["metrics"].get("pos_pct", 0) * 100,
                        "neutral": max(0, 100 - f["metrics"]["neg_pct"] * 100 - f["metrics"].get("pos_pct", 0) * 100),
                    },
                    "comment_count": f["metrics"]["comment_count"],
                    "keywords": f.get("keywords", []),
                }
                for f in features
            ],
            "overall": aggregated_stats.overall_sentiment,
            "counts": {"total": aggregated_stats.total_comments},
            "pipeline_metadata": {
                "unmapped_percentage": aggregated_stats.unmapped_percentage,
            },
        }
        candidate_service = get_work_item_candidate_service()
        candidates = candidate_service.generate_candidates(
            analysis_for_candidates, previous_analysis=None
        )
        logger.info(f"Generated {len(candidates)} work item candidates for narration")

        narration_input = {
            "project_id": None,
            "analysis_id": None,
            "taxonomy_id": None,
            "taxonomy_version": None,
            "overall": aggregated_stats.overall_sentiment,
            "features": features,
            "evidence": self._build_evidence(matches),
            "work_item_candidates": candidates,
            "comment_samples": comment_samples,
        }

        narratives = narration_service.generate_narratives(narration_input, user_id=user_id)

        narrative_map = {f.get("aspect_key"): f.get("description") for f in narratives.get("features", [])}
        sentiment_counts_map = aggregated_stats.aspect_sentiment_counts
        features_out = []
        for feature in features:
            aspect_key = feature.get("aspect_key")
            total_comments = feature["metrics"]["comment_count"]
            aspect_name = aspect_key_map.get(aspect_key, aspect_key)
            aspect_label = aspect_name.replace("_", " ").title() if aspect_name else "General"
            raw_counts = sentiment_counts_map.get(aspect_name, {})
            total = sum(raw_counts.values()) or 1
            pos_pct = (raw_counts.get("POSITIVE", 0) / total) * 100
            neg_pct = (raw_counts.get("NEGATIVE", 0) / total) * 100
            neu_pct = (raw_counts.get("NEUTRAL", 0) / total) * 100
            features_out.append({
                "feature": aspect_label,
                "description": narrative_map.get(aspect_key, f"Customer feedback about {aspect_key}."),
                "sentiment": {
                    "positive": pos_pct,
                    "negative": neg_pct,
                    "neutral": neu_pct,
                },
                "keywords": feature.get("keywords", []),
                "comment_count": total_comments,
                "sample_comments": feature_comment_buckets.get(aspect_key, {
                    "positive": [],
                    "negative": [],
                    "neutral": []
                }),
            })

        return narratives.get("insights", []), features_out, narratives.get("work_items", [])

    @staticmethod
    def _sample_comments_for_narration(
        feature_comment_buckets: Dict[str, Dict[str, list]],
        max_per_candidate: int = 5,
    ) -> Dict[str, list]:
        """Build comment_samples dict keyed by aspect_key for the narration prompt."""
        samples = {}
        for aspect_key, buckets in feature_comment_buckets.items():
            aspect_samples = []
            # Prioritize negative, then positive, then neutral
            for sentiment in ("negative", "positive", "neutral"):
                for comment in buckets.get(sentiment, []):
                    if len(aspect_samples) >= max_per_candidate:
                        break
                    aspect_samples.append(comment)
            if aspect_samples:
                samples[aspect_key] = aspect_samples
        return samples

    # ------------------------------------------------------------------
    # Evidence sampling (per-aspect, capped)
    # ------------------------------------------------------------------

    def _select_aspect_evidence_samples(self, matches: List[AspectMatch], aspects: List[str]) -> List[str]:
        """
        Select 3-5 representative comments PER ASPECT, capped at MAX_EVIDENCE_SAMPLES total.

        Prioritizes comments with clear sentiment signal (HIGH confidence first).
        """
        aspect_buckets: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
        # (comment_text, sentiment, confidence)

        for m in matches:
            for aspect in m.matched_aspects:
                if aspect == "UNMAPPED":
                    continue
                asp_sent = m.aspect_sentiments.get(aspect)
                if asp_sent:
                    aspect_buckets[aspect].append(
                        (asp_sent.source_sentence, asp_sent.sentiment, asp_sent.confidence)
                    )
                else:
                    aspect_buckets[aspect].append(
                        (m.comment_text, m.comment_sentiment.sentiment, m.comment_sentiment.confidence)
                    )

        samples: List[str] = []
        budget = self.MAX_EVIDENCE_SAMPLES
        per_aspect = self.SAMPLES_PER_ASPECT

        for aspect in aspects:
            if budget <= 0:
                break
            bucket = aspect_buckets.get(aspect, [])
            if not bucket:
                continue

            # Sort: HIGH confidence first, then diversify by sentiment
            confidence_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
            bucket.sort(key=lambda x: confidence_order.get(x[2], 3))

            # Take up to per_aspect, ensuring sentiment diversity
            seen_sentiments = set()
            selected = []
            for text, sentiment, confidence in bucket:
                if len(selected) >= per_aspect:
                    break
                # Prioritize unseen sentiments
                if sentiment not in seen_sentiments:
                    selected.append(f"[{aspect} | {sentiment}] {text[:200]}")
                    seen_sentiments.add(sentiment)
                elif len(selected) < per_aspect:
                    selected.append(f"[{aspect} | {sentiment}] {text[:200]}")

            take = min(len(selected), budget)
            samples.extend(selected[:take])
            budget -= take

        logger.info(f"Evidence samples: {len(samples)} total across {len(aspect_buckets)} aspects")
        return samples

    def _build_evidence(self, matches: List[AspectMatch]) -> List[Dict[str, Any]]:
        """Build evidence list with confidence for narration trimming."""
        evidence = []
        for m in matches:
            for aspect in m.matched_aspects:
                if aspect == "UNMAPPED":
                    continue
                asp_sent = m.aspect_sentiments.get(aspect)
                if asp_sent:
                    confidence = self._confidence_to_score(asp_sent.confidence)
                    text = asp_sent.source_sentence
                    sentiment = asp_sent.sentiment
                else:
                    confidence = self._confidence_to_score(m.comment_sentiment.confidence)
                    text = m.comment_text
                    sentiment = m.comment_sentiment.sentiment
                evidence.append({
                    "aspect_key": self._normalize_aspect_key(aspect),
                    "sentiment": sentiment,
                    "text": text,
                    "confidence": confidence,
                })
        return evidence

    @staticmethod
    def _normalize_aspect_key(label: str) -> str:
        return str(label).strip().lower().replace(" ", "_")

    def _build_feature_comment_buckets(self, matches: List[AspectMatch], limit: int = 10) -> Dict[str, Dict[str, List[str]]]:
        """Build per-feature comment buckets split by sentiment (positive/negative/neutral)."""
        buckets: Dict[str, Dict[str, List[str]]] = {}

        def _add(bucket: Dict[str, List[str]], sentiment: str, text: str) -> None:
            if not text:
                return
            key = sentiment.upper()
            if key == "POSITIVE":
                k = "positive"
            elif key == "NEGATIVE":
                k = "negative"
            else:
                k = "neutral"
            existing = bucket[k]
            if text in existing or len(existing) >= limit:
                return
            existing.append(text)

        for m in matches:
            for aspect in m.matched_aspects:
                if aspect == "UNMAPPED":
                    continue
                aspect_key = self._normalize_aspect_key(aspect)
                if aspect_key not in buckets:
                    buckets[aspect_key] = {"positive": [], "negative": [], "neutral": []}
                asp_sent = m.aspect_sentiments.get(aspect)
                sentiment = asp_sent.sentiment if asp_sent else m.comment_sentiment.sentiment
                _add(buckets[aspect_key], sentiment, m.comment_text)

        return buckets

    @staticmethod
    def _confidence_to_score(confidence: str) -> float:
        conf = str(confidence or "").upper()
        return {"HIGH": 0.9, "MEDIUM": 0.6, "LOW": 0.3}.get(conf, 0.0)


# ------------------------------------------------------------------
# Singleton accessor
# ------------------------------------------------------------------
_local_processing_service = None


def get_local_processing_service() -> LocalProcessingService:
    global _local_processing_service
    if _local_processing_service is None:
        _local_processing_service = LocalProcessingService()
    return _local_processing_service
