"""
Ensemble Aspect Classification Service

Multi-signal approach combining:
1. NLI (Cross-Encoder) - Deep semantic entailment
2. Embedding Similarity (Bi-Encoder) - Fast semantic similarity
3. Keyword Matching - Explicit lexical overlap

Ensemble voting: Accept aspect if 2/3 methods agree (weighted by confidence).
Provides higher coverage, better quality, and more trustworthy results.
"""

import logging
import re
from typing import List, Dict, Any, Optional, Set
from collections import Counter
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)


class EnsembleAspectService:
    """
    Hybrid aspect classifier combining multiple signals for robust predictions.

    Methods:
    1. NLI: Zero-shot entailment (high precision, context-aware)
    2. Embedding: Semantic similarity (fast, good recall)
    3. Keywords: Lexical overlap (explicit mentions, high precision)

    Voting: Weighted ensemble - accepts if 2/3 agree or single method with very high confidence.
    """

    def __init__(
        self,
        nli_service,
        embedding_service,
        nli_weight: float = 0.45,
        embedding_weight: float = 0.35,
        keyword_weight: float = 0.20,
        ensemble_threshold: float = 0.50,
        max_aspects_per_comment: int = 3,
    ):
        """
        Initialize ensemble classifier.

        Args:
            nli_service: Zero-shot NLI service (ZeroShotAspectService)
            embedding_service: Embedding service (sentence-transformers)
            nli_weight: Weight for NLI scores (0-1)
            embedding_weight: Weight for embedding similarity scores (0-1)
            keyword_weight: Weight for keyword matching scores (0-1)
            ensemble_threshold: Minimum ensemble score to accept aspect (0-1)
            max_aspects_per_comment: Max aspects per comment
        """
        self.nli_service = nli_service
        self.embedding_service = embedding_service

        # Normalize weights to sum to 1.0
        total = nli_weight + embedding_weight + keyword_weight
        self.nli_weight = nli_weight / total
        self.embedding_weight = embedding_weight / total
        self.keyword_weight = keyword_weight / total

        self.ensemble_threshold = ensemble_threshold
        self.max_aspects = max_aspects_per_comment

        logger.info(
            f"EnsembleAspectService initialized: "
            f"weights=(nli:{self.nli_weight:.2f}, emb:{self.embedding_weight:.2f}, kw:{self.keyword_weight:.2f}), "
            f"threshold={ensemble_threshold}, max_aspects={max_aspects_per_comment}"
        )

    def classify_aspects(
        self,
        comments: List[str],
        aspects: List[str],
        company_name: Optional[str] = None,
        run_id: Optional[str] = None,
        task: Optional[Any] = None,
        on_progress: Optional[Any] = None,
        is_cancelled: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """
        Classify comments using ensemble of NLI + Embedding + Keywords.

        Returns same format as ZeroShotAspectService for drop-in compatibility.
        """
        if not comments or not aspects:
            return []

        logger.info(
            f"Ensemble classification: {len(comments)} comments × {len(aspects)} aspects "
            f"(run: {run_id or 'default'})"
        )

        # Step 1: Get NLI scores (most expensive, most accurate)
        nli_results = self.nli_service.classify_aspects(
            comments=comments,
            aspects=aspects,
            company_name=company_name,
            run_id=run_id,
            task=task,
            on_progress=on_progress,
            is_cancelled=is_cancelled,
        )

        # Step 2: Get embedding similarities
        comment_embeddings = self.embedding_service.get_embeddings(comments)
        aspect_embeddings = self.embedding_service.get_embeddings(aspects)
        embedding_scores = cosine_similarity(comment_embeddings, aspect_embeddings)  # shape: (n_comments, n_aspects)

        # Step 3: Get keyword matches
        keyword_scores = self._compute_keyword_scores(comments, aspects)

        # Step 4: Ensemble voting
        ensemble_results = []
        for i, nli_result in enumerate(nli_results):
            comment = comments[i]

            # Collect scores from all methods
            method_scores = {
                'nli': {},
                'embedding': {},
                'keyword': {},
            }

            # NLI scores (already filtered by NLI threshold)
            for aspect, score in nli_result.get('aspect_scores', {}).items():
                method_scores['nli'][aspect] = score

            # Embedding scores (all aspects)
            for j, aspect in enumerate(aspects):
                method_scores['embedding'][aspect] = float(embedding_scores[i, j])

            # Keyword scores
            method_scores['keyword'] = keyword_scores[i]

            # Compute ensemble scores
            ensemble_scores = self._compute_ensemble_scores(
                method_scores, aspects
            )

            # Select top aspects based on ensemble threshold
            matched_aspects, final_scores = self._select_top_aspects(
                ensemble_scores, self.ensemble_threshold, self.max_aspects
            )

            ensemble_results.append({
                'comment_id': nli_result.get('comment_id', i),
                'comment_text': comment,
                'matched_aspects': matched_aspects,
                'aspect_scores': final_scores,
                'method_breakdown': method_scores,  # For debugging/transparency
            })

        # Log ensemble statistics
        mapped_count = sum(1 for r in ensemble_results if r['matched_aspects'])
        logger.info(
            f"Ensemble complete: {mapped_count}/{len(comments)} comments mapped "
            f"({mapped_count/len(comments)*100:.1f}%)"
        )

        return ensemble_results

    def _compute_keyword_scores(
        self, comments: List[str], aspects: List[str]
    ) -> List[Dict[str, float]]:
        """
        Compute keyword-based matching scores.

        Strategy:
        - Extract meaningful keywords from aspect names
        - Check for exact/fuzzy matches in comments
        - Score based on match quality and frequency

        Returns:
            List of dicts (one per comment): {aspect: score, ...}
        """
        # Extract keywords from aspect names
        aspect_keywords = {}
        for aspect in aspects:
            keywords = self._extract_aspect_keywords(aspect)
            aspect_keywords[aspect] = keywords

        # Score each comment
        results = []
        for comment in comments:
            comment_lower = comment.lower()
            comment_words = set(re.findall(r'\b\w+\b', comment_lower))

            scores = {}
            for aspect, keywords in aspect_keywords.items():
                # Count keyword matches
                matches = sum(1 for kw in keywords if kw in comment_lower)

                # Bonus for word boundary matches (more precise)
                word_matches = sum(1 for kw in keywords if kw in comment_words)

                # Normalize by number of keywords in aspect
                if keywords:
                    base_score = matches / len(keywords)
                    precision_bonus = word_matches / len(keywords) * 0.2
                    scores[aspect] = min(1.0, base_score + precision_bonus)
                else:
                    scores[aspect] = 0.0

            results.append(scores)

        return results

    def _extract_aspect_keywords(self, aspect: str) -> List[str]:
        """
        Extract meaningful keywords from aspect name.

        Examples:
        - "UI/UX & Navigation" → ["ui", "ux", "navigation"]
        - "Data Quality & Timeliness" → ["data", "quality", "timeliness"]
        - "Customer Support" → ["customer", "support"]
        """
        # Split on common separators
        aspect_lower = aspect.lower()
        tokens = re.split(r'[&/\-,\s]+', aspect_lower)

        # Filter out stopwords and short words
        stopwords = {'and', 'or', 'the', 'a', 'an', 'of', 'in', 'to', 'for'}
        keywords = [
            token.strip()
            for token in tokens
            if token.strip() and len(token.strip()) > 2 and token.strip() not in stopwords
        ]

        return keywords

    def _compute_ensemble_scores(
        self, method_scores: Dict[str, Dict[str, float]], aspects: List[str]
    ) -> Dict[str, float]:
        """
        Compute weighted ensemble scores for each aspect.

        Args:
            method_scores: {
                'nli': {aspect: score, ...},
                'embedding': {aspect: score, ...},
                'keyword': {aspect: score, ...}
            }
            aspects: List of aspect names

        Returns:
            {aspect: ensemble_score, ...}
        """
        ensemble = {}

        for aspect in aspects:
            nli_score = method_scores['nli'].get(aspect, 0.0)
            emb_score = method_scores['embedding'].get(aspect, 0.0)
            kw_score = method_scores['keyword'].get(aspect, 0.0)

            # Weighted average
            weighted_score = (
                nli_score * self.nli_weight +
                emb_score * self.embedding_weight +
                kw_score * self.keyword_weight
            )

            # Bonus: If 2+ methods strongly agree (all > 0.3), boost score
            strong_votes = sum([
                1 if nli_score > 0.3 else 0,
                1 if emb_score > 0.3 else 0,
                1 if kw_score > 0.3 else 0,
            ])

            if strong_votes >= 2:
                weighted_score *= 1.15  # 15% boost for consensus

            ensemble[aspect] = min(1.0, weighted_score)  # Cap at 1.0

        return ensemble

    def _select_top_aspects(
        self, ensemble_scores: Dict[str, float], threshold: float, max_aspects: int
    ) -> tuple[List[str], Dict[str, float]]:
        """
        Select top aspects based on ensemble threshold and max limit.

        Returns:
            (matched_aspects, final_scores)
        """
        # Filter by threshold
        candidates = {
            aspect: score
            for aspect, score in ensemble_scores.items()
            if score >= threshold
        }

        if not candidates:
            return ([], {})

        # Sort by score and take top N
        sorted_aspects = sorted(candidates.items(), key=lambda x: x[1], reverse=True)
        top_aspects = sorted_aspects[:max_aspects]

        matched_aspects = [aspect for aspect, _ in top_aspects]
        final_scores = {aspect: score for aspect, score in top_aspects}

        return (matched_aspects, final_scores)
