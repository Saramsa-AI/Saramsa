"""
Processing Service

Orchestrates the feedback pipeline:
  1. LLM aspect classification
  2. Aspect-relative sentiment (per matched aspect, produced in the aspect call)
  3. Aggregation + keyword extraction
  4. Lean GPT-5-mini synthesis (aggregates + evidence samples only)
"""

import os
import logging
import time
import re
from typing import List, Dict, Any, Tuple, Optional, Callable
from dataclasses import dataclass, field
from collections import defaultdict
from sklearn.feature_extraction.text import TfidfVectorizer

from aiCore.services.aspect_service_factory import get_aspect_service
from aiCore.services.sentiment_types import SentimentResult
from apis.infrastructure.phase_logger import phase, reset_pipeline_summary, emit_pipeline_summary
from .narration_service import get_narration_service

logger = logging.getLogger(__name__)

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
    # --- sentiment/filler adjectives & verbs that muddy phrases
    # ("free tier good", "alerts useful", "tickertape makes") ---
    'good', 'bad', 'nice', 'great', 'useful', 'helpful', 'better', 'best',
    'makes', 'making', 'gives', 'giving', 'feels', 'feel', 'looks',
    'reading', 'reads', 'shows', 'showing', 'shown',
})

_WORD_RE = re.compile(r'[a-zA-Z]{3,}')

# Generic words that are fine inside a phrase ("locked behind paywall") but
# carry no meaning as a standalone keyword. Only applied to unigram backfill.
_UNIGRAM_NOISE = frozenset({
    'built', 'building', 'showing', 'shown', 'shows', 'behind', 'days', 'day',
    'consistent', 'consistently', 'company', 'companies', 'various', 'overall',
    'recent', 'recently', 'current', 'currently', 'multiple', 'several', 'across',
    'within', 'around', 'along', 'without', 'basic', 'simple', 'general',
    'certain', 'particular', 'specific', 'given', 'able', 'making', 'getting',
    'coming', 'looking', 'trying', 'having', 'doing',
})

# A phrase must not START or END on one of these "boundary" words — that is what
# distinguishes "locked behind paywall" (fine) from "buy hold" / "screener gives"
# (junk). Stopwords are allowed INSIDE a phrase, just not at its edges.
_BAD_BOUNDARY = _STOPWORDS | _UNIGRAM_NOISE


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
    rationale: str = ""                          # Short LLM explanation of the aspect/sentiment choice


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
    # Persisted so the downstream user-story-creation endpoint can reuse this
    # GPT output instead of making a second redundant LLM call. Candidates are
    # kept alongside so candidate_id mapping in _apply_llm_phrasing still matches.
    narration: Optional[Dict[str, Any]] = None
    work_item_candidates: Optional[List[Dict[str, Any]]] = None
    # Comments that failed classification after retries (kept out of the stats);
    # surfaced so a partial run is visible rather than silently dropped.
    failed_comments: List[Dict[str, Any]] = field(default_factory=list)


class LocalProcessingService:
    """
    Orchestrates the feedback analysis pipeline.

    Pipeline:
      1. LLM aspect classification
      2. Aspect-relative sentiment (per matched aspect)
      3. Aggregate statistics + extract keywords
      4. Lean GPT-5-mini synthesis (aggregates + evidence only)
    """

    UNMAPPED_WARNING_THRESHOLD = 0.12
    AUTO_REGENERATE_THRESHOLD = 0.70  # If >70% unmapped (i.e. <30% mapped), auto-regenerate taxonomy
    MAX_EVIDENCE_SAMPLES = 30
    SAMPLES_PER_ASPECT = 5

    def __init__(self):
        self.aspect_service = get_aspect_service()
        logger.debug("LocalProcessingService initialized (LLM aspect + sentiment, no local models)")

    def process_comments(self, comments: List[str], aspects: List[str],
                         company_name: str = "Company", run_id: str = None,
                         is_cancelled: Optional[Callable[[], bool]] = None,
                         user_id: Optional[str] = None,
                         regenerate_callback: Optional[Callable[[List[str]], List[str]]] = None,
                         project_id: Optional[str] = None,
                         analysis_id: Optional[str] = None) -> ProcessingResult:
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

        logger.debug(
            "Processing comments",
            extra={"comment_count": len(comments), "aspect_count": len(aspects), "run_id": run_id},
        )
        reset_pipeline_summary()

        # Strip bracket metadata before classification (20-30% token reduction).
        # The enriched metadata is only useful for LLM narration.
        stripped_comments = [self._strip_bracket_metadata(c) for c in comments]

        # Step 1: Aspect classification (via factory)
        with phase("aspect_classify_pass1", n_items=len(stripped_comments), n_aspects=len(aspects)):
            similarity_results = self.aspect_service.classify_aspects(
                stripped_comments, aspects, run_id, is_cancelled=is_cancelled,
                project_id=project_id, user_id=user_id, analysis_id=analysis_id,
            )

        # Step 1b: Check mapping rate and adaptive taxonomy update
        # Locked taxonomies skip regen; partial matches use additive growth
        if regenerate_callback is not None:
            # Exclude errored comments — they carry no taxonomy-fit signal either way.
            scored = [r for r in similarity_results if not r.get("errored")]
            unmapped_count = sum(1 for r in scored if not r.get("matched_aspects") or r.get("matched_aspects") == ["UNMAPPED"])
            unmapped_rate = unmapped_count / max(len(scored), 1)
            mapping_rate = 1 - unmapped_rate

            if unmapped_rate > self.AUTO_REGENERATE_THRESHOLD:
                logger.warning(
                    "Low aspect mapping rate; attempting adaptive taxonomy update",
                    extra={
                        "mapping_rate": mapping_rate,
                        "mapped_count": len(similarity_results) - unmapped_count,
                        "total_count": len(similarity_results),
                    },
                )
                try:
                    with phase("taxonomy_regen", mapped_pct=f"{mapping_rate:.1%}"):
                        new_aspects = regenerate_callback(comments, mapping_rate)
                    if new_aspects is None:
                        # Locked taxonomy - don't re-run, just continue
                        logger.info("Taxonomy locked; keeping original aspects")
                    elif len(new_aspects) > 0:
                        logger.info("Received aspects from regenerate callback", extra={"aspect_count": len(new_aspects)})
                        aspects = new_aspects

                        # Free pass1 results before pass2 — they're replaced anyway
                        # and can be large for big datasets.
                        del similarity_results
                        logger.debug("Cleaned up pass1 results before pass2")

                        # Re-run aspect classification with updated aspects
                        with phase("aspect_classify_pass2", n_items=len(stripped_comments), n_aspects=len(aspects)):
                            similarity_results = self.aspect_service.classify_aspects(
                                stripped_comments, aspects, f"{run_id}_regen", is_cancelled=is_cancelled,
                                project_id=project_id, user_id=user_id, analysis_id=analysis_id,
                            )
                        scored_after = [r for r in similarity_results if not r.get("errored")]
                        new_unmapped = sum(1 for r in scored_after if not r.get("matched_aspects") or r.get("matched_aspects") == ["UNMAPPED"])
                        new_rate = new_unmapped / max(len(scored_after), 1)
                        logger.info(
                            "Taxonomy update applied",
                            extra={"mapping_rate": 1 - new_rate, "previous_mapping_rate": mapping_rate},
                        )
                    else:
                        logger.warning("Regenerate callback returned no aspects; keeping original taxonomy")
                except Exception:
                    logger.exception("Adaptive taxonomy update failed; continuing with original taxonomy")

        # Restore original comment text (with brackets) in similarity results for display + LLM narration
        for i, result in enumerate(similarity_results):
            result["comment_text"] = comments[i]

        # Comments that failed classification after retries — kept out of the
        # stats below, surfaced so a partial run is visible (not silently dropped).
        failed_comments = [
            {"index": i, "comment": comments[i], "error": r.get("error", "")}
            for i, r in enumerate(similarity_results)
            if r.get("errored")
        ]
        if failed_comments:
            logger.warning("Partial analysis: %d comment(s) failed classification", len(failed_comments))

        # Step 2: sentiment — produced by the LLM in the same aspect call (overall +
        # per-aspect, with "NONE" for no opinion).
        with phase("sentiment_llm", n_items=len(similarity_results)):
            combined_matches = self._apply_llm_sentiment(similarity_results)

        # Step 3: aggregate + keywords (uses per-aspect sentiment)
        with phase("aggregate_stats", n_aspects=len(aspects)):
            aggregated_stats = self._aggregate_results(combined_matches, aspects)

        if aggregated_stats.unmapped_percentage > self.UNMAPPED_WARNING_THRESHOLD:
            logger.warning(
                "High unmapped rate; consider updating the aspect taxonomy",
                extra={
                    "unmapped_percentage": aggregated_stats.unmapped_percentage,
                    "unmapped_count": aggregated_stats.unmapped_count,
                    "total_comments": aggregated_stats.total_comments,
                },
            )

        # Step 4: Unified narration (single GPT entrypoint)
        with phase("narrate", n_aspects=len(aspects)):
            insights, features, work_items, narratives, candidates = self._narrate_with_service(
                combined_matches, aggregated_stats, aspects, company_name, user_id=user_id,
                project_id=project_id, analysis_id=analysis_id,
            )

        processing_time = time.time() - start_time
        logger.info("Pipeline completed", extra={"duration_s": round(processing_time, 2)})
        emit_pipeline_summary(label=f"run={run_id} n_comments={len(comments)}")

        return ProcessingResult(
            matches=combined_matches,
            aggregated_stats=aggregated_stats,
            processing_time=processing_time,
            model_info={
                "aspect_model": getattr(self.aspect_service, 'MODEL_NAME', 'llm'),
                "sentiment_model": getattr(self.aspect_service, 'MODEL_NAME', 'llm'),
                "processing_method": "llm_aspect_sentiment_pipeline",
            },
            insights=insights,
            features=features,
            work_items=work_items,
            narration=narratives,
            work_item_candidates=candidates,
            failed_comments=failed_comments,
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
        context) but adds noise for aspect classification, which works better on
        raw user text.
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

    def _apply_llm_sentiment(self, similarity_results: List[Dict[str, Any]]) -> List[AspectMatch]:
        """Build combined matches from sentiment the LLM produced in the aspect call.

        The LLM judges sentiment per aspect + overall. "NONE" (no opinion /
        operational text) maps to NEUTRAL for the 3-class downstream counts —
        honest, vs a forced positive/negative.
        """
        def to_result(label: str) -> SentimentResult:
            # NONE (no opinion) -> NEUTRAL for the 3-class downstream. confidence is a
            # STRING ("HIGH"/"MEDIUM"/"LOW") per the contract — downstream calls .upper()
            # on the confidence-distribution key, so a float would crash insights views.
            s = label if label in ("POSITIVE", "NEGATIVE", "NEUTRAL") else "NEUTRAL"
            return SentimentResult(sentiment=s, confidence="HIGH", raw_scores={}, processing_time=0.0)

        combined: List[AspectMatch] = []
        for r in similarity_results:
            if r.get("errored"):
                continue  # failed comment — excluded from stats/narration, surfaced separately
            matched = r["matched_aspects"]
            overall_label = r.get("overall_sentiment", "NEUTRAL")
            asp_sent = r.get("aspect_sentiments", {}) or {}
            aspect_sentiments: Dict[str, AspectSentiment] = {}
            for a in matched:
                if a == "UNMAPPED":
                    continue
                res = to_result(asp_sent.get(a, overall_label))
                aspect_sentiments[a] = AspectSentiment(
                    aspect=a, sentiment=res.sentiment, confidence=res.confidence,
                    source_sentence=r["comment_text"], raw_scores=res.raw_scores,
                )
            combined.append(AspectMatch(
                comment_id=r["comment_id"], comment_text=r["comment_text"],
                matched_aspects=matched, aspect_scores=r["aspect_scores"],
                comment_sentiment=to_result(overall_label), aspect_sentiments=aspect_sentiments,
                rationale=str(r.get("rationale", "") or ""),
            ))
        return combined

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
                # IMPORTANT: do NOT pass stop_words here. scikit-learn removes
                # stopwords BEFORE forming n-grams, which glues together words
                # that were never adjacent ("buy and hold" -> "buy hold",
                # "in 5 minutes ... stock" -> "minutes stock"). We keep phrases
                # CONTIGUOUS and instead filter phrase boundaries below.
                token_pattern=r'[a-zA-Z]{3,}',
                # Prefer multi-word phrases (bigrams/trigrams) — single words like
                # "built" or "showing" carry no context on their own. Unigrams are
                # still extracted so we can backfill when phrases are scarce.
                ngram_range=(1, 3),
                max_features=1500,
                min_df=1,
                sublinear_tf=True,
            )
            tfidf_matrix = vectorizer.fit_transform(corpus)
            feature_names = vectorizer.get_feature_names_out()
        except ValueError:
            # Empty vocabulary (e.g. all comments empty)
            return {a: [] for a in aspects}

        result: Dict[str, List[str]] = {}

        for idx, aspect in enumerate(ordered_aspects):
            row = tfidf_matrix[idx].toarray().flatten()
            # Terms sorted by TF-IDF score descending
            top_indices = row.argsort()[::-1]

            # Skip terms that are just the aspect name (e.g. "pricing" in Pricing)
            aspect_tokens = set(aspect.lower().replace("_", " ").split())

            phrases: List[str] = []   # bigrams/trigrams, score order
            unigrams: List[str] = []  # single words, score order (backfill only)
            for i in top_indices:
                if row[i] <= 0:
                    break
                term = feature_names[i]
                toks = term.split()
                term_tokens = set(toks)
                if term_tokens <= aspect_tokens:
                    continue
                if len(toks) == 1:
                    # Single word: must be a genuine content word.
                    if term in _BAD_BOUNDARY:
                        continue
                    unigrams.append(term)
                else:
                    # Phrase: reject if it starts or ends on a stopword/filler
                    # ("screener gives", "for stocks"). Interior stopwords are OK.
                    if toks[0] in _BAD_BOUNDARY or toks[-1] in _BAD_BOUNDARY:
                        continue
                    phrases.append(term)

            # Lead with phrases (context-rich); only fall back to unigrams to fill
            # remaining slots, skipping unigrams already covered by a chosen phrase.
            keywords: List[str] = []
            covered: set = set()
            for p in phrases:
                if len(keywords) >= top_n:
                    break
                p_tokens = set(p.split())
                if p_tokens <= covered:  # fully redundant with an existing phrase
                    continue
                keywords.append(p)
                covered |= p_tokens
            for u in unigrams:
                if len(keywords) >= top_n:
                    break
                if u in covered:
                    continue
                keywords.append(u)
                covered.add(u)
            result[aspect] = keywords

        # Fill aspects with no comments
        for aspect in aspects:
            if aspect not in result:
                result[aspect] = []

        return result

    # ------------------------------------------------------------------
    # Unified narration (single GPT entrypoint)
    # ------------------------------------------------------------------

    def _narrate_with_service(self, matches, aggregated_stats, aspects, company_name, user_id: Optional[str] = None,
                              project_id: Optional[str] = None, analysis_id: Optional[str] = None):
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
        logger.debug("Generated work item candidates for narration", extra={"candidate_count": len(candidates)})

        narration_input = {
            # Real ids so per-project narration cost caps + usage metering apply.
            "project_id": project_id,
            "analysis_id": analysis_id,
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
        # Prefer the LLM's per-feature keywords (natural, context-rich themes,
        # consistent with the LLM-written description). Fall back to the
        # statistical TF-IDF keywords when the LLM returns none for an aspect.
        narrative_kw_map = {
            f.get("aspect_key"): f.get("keywords") or []
            for f in narratives.get("features", [])
        }
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
                "keywords": narrative_kw_map.get(aspect_key) or feature.get("keywords", []),
                "comment_count": total_comments,
                "sample_comments": feature_comment_buckets.get(aspect_key, {
                    "positive": [],
                    "negative": [],
                    "neutral": []
                }),
            })

        return (
            narratives.get("insights", []),
            features_out,
            narratives.get("work_items", []),
            narratives,
            candidates,
        )

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
                    # Buckets hold {text, rationale}; the narration prompt wants plain text.
                    aspect_samples.append(comment["text"] if isinstance(comment, dict) else comment)
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

        logger.debug(
            "Selected evidence samples",
            extra={"sample_count": len(samples), "aspect_count": len(aspect_buckets)},
        )
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

    def _build_feature_comment_buckets(self, matches: List[AspectMatch], limit: int = 10) -> Dict[str, Dict[str, List[Dict[str, str]]]]:
        """Per-feature evidence buckets split by sentiment. Each entry is
        ``{"text", "rationale"}`` — the example comment plus the LLM's short
        explanation for why it was classified that way (empty when none)."""
        buckets: Dict[str, Dict[str, List[Dict[str, str]]]] = {}

        def _add(bucket: Dict[str, List[Dict[str, str]]], sentiment: str, text: str, rationale: str) -> None:
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
            if any(e["text"] == text for e in existing) or len(existing) >= limit:
                return
            existing.append({"text": text})

        for m in matches:
            for aspect in m.matched_aspects:
                if aspect == "UNMAPPED":
                    continue
                aspect_key = self._normalize_aspect_key(aspect)
                if aspect_key not in buckets:
                    buckets[aspect_key] = {"positive": [], "negative": [], "neutral": []}
                asp_sent = m.aspect_sentiments.get(aspect)
                sentiment = asp_sent.sentiment if asp_sent else m.comment_sentiment.sentiment
                _add(buckets[aspect_key], sentiment, m.comment_text, m.rationale)

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
