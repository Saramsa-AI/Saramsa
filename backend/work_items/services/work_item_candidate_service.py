"""
Rule-first work item candidate generation (Phase-2).

Deterministic rules produce WorkItemCandidate objects from analysis metrics.
LLMs may only phrase candidates and must not decide existence, type, or priority.
"""

from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone
import uuid
import logging

logger = logging.getLogger(__name__)


class WorkItemCandidateService:
    """Deterministic work item candidate generator."""

    # Rule Group A thresholds (feature-level negative sentiment)
    NEGATIVE_CREATE_THRESHOLD = 0.30
    NEGATIVE_PRIORITY_P0 = 0.55
    NEGATIVE_PRIORITY_P1 = 0.40
    NEGATIVE_MIN_COMMENTS = 3

    # Rule Group D thresholds
    TAXONOMY_GAP_UNMAPPED_THRESHOLD = 0.15

    # Rule Group B thresholds (volume-based sub-theme splitting)
    VOLUME_SPLIT_MIN_COMMENTS = 50       # Only split features with ≥50 comments
    VOLUME_SPLIT_COMMENTS_PER_ITEM = 100 # 1 extra candidate per 100 comments beyond base
    VOLUME_SPLIT_MAX_EXTRA = 4           # Max extra candidates per feature from volume
    VOLUME_SPLIT_MIN_KEYWORDS = 2        # Need at least 2 keywords to split into sub-themes

    # Rule Group E thresholds (overall-level negative sentiment)
    OVERALL_NEGATIVE_THRESHOLD = 0.25
    OVERALL_MIN_COMMENTS = 10

    # Known bug aspect keys — aspects that indicate a defect rather than an improvement
    KNOWN_BUG_ASPECT_KEYS = {
        "bugs", "bug", "crashes", "crash", "errors", "error",
        "broken", "defect", "defects", "glitch", "glitches",
        "reliability", "stability", "downtime", "outage",
        "data_loss", "security", "authentication", "login_issues",
        "performance", "latency", "slow", "timeout",
    }

    PRIORITY_ORDER = ["P3", "P2", "P1", "P0"]

    def generate_candidates(self, analysis: Dict[str, Any], previous_analysis: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Generate deterministic candidates from analysis metrics only.

        Thresholds are adaptive: for small datasets (<100 total comments),
        the minimum comment requirement per feature is relaxed so that
        meaningful signals are not silently dropped.
        """
        features = self._extract_features(analysis)

        # Adaptive thresholds based on dataset size
        total_comments = self._total_comment_count(analysis)
        if total_comments < 50:
            effective_min_comments = 1
        elif total_comments < 100:
            effective_min_comments = 2
        else:
            effective_min_comments = self.NEGATIVE_MIN_COMMENTS

        project_id = self._get_analysis_field(analysis, "project_id") or self._get_analysis_field(analysis, "projectId")
        analysis_id = self._get_analysis_field(analysis, "analysis_id") or self._get_analysis_field(analysis, "id")
        taxonomy_id = self._get_analysis_field(analysis, "taxonomy_id")
        taxonomy_version = self._get_analysis_field(analysis, "taxonomy_version")

        previous_map = self._build_previous_neg_map(previous_analysis)

        candidates: List[Dict[str, Any]] = []

        # Rule Group A — Feature-level negative sentiment
        for feature in features:
            aspect_key, metrics = self._feature_metrics(feature)
            if not aspect_key:
                continue

            neg_pct = metrics.get("neg_pct", 0.0)
            comment_count = metrics.get("comment_count", 0)
            confidence_p95 = metrics.get("confidence_p95")

            if neg_pct < self.NEGATIVE_CREATE_THRESHOLD or comment_count < effective_min_comments:
                reason_parts = []
                if neg_pct < self.NEGATIVE_CREATE_THRESHOLD:
                    reason_parts.append(f"neg_pct {neg_pct:.1%} < threshold {self.NEGATIVE_CREATE_THRESHOLD:.0%}")
                if comment_count < effective_min_comments:
                    reason_parts.append(f"comment_count {comment_count} < min {effective_min_comments}")
                logger.debug(
                    "Skipping candidate: below thresholds",
                    extra={"aspect_key": aspect_key, "reasons": "; ".join(reason_parts)},
                )
                continue

            if neg_pct >= self.NEGATIVE_CREATE_THRESHOLD and comment_count >= effective_min_comments:
                priority = self._priority_from_negative(neg_pct)
                candidate_type = "bug" if aspect_key in self.KNOWN_BUG_ASPECT_KEYS else "improvement"
                candidates.append(self._build_candidate(
                    project_id=project_id,
                    analysis_id=analysis_id,
                    taxonomy_id=taxonomy_id,
                    taxonomy_version=taxonomy_version,
                    aspect_key=aspect_key,
                    candidate_type=candidate_type,
                    priority=priority,
                    reason={
                        "neg_pct": neg_pct,
                        "comment_count": comment_count,
                        "trend": self._trend_direction(neg_pct, previous_map.get(aspect_key)),
                        "confidence_p95": confidence_p95,
                    },
                    evidence=self._extract_evidence(feature),
                ))

        # Rule Group B — Volume-based sub-theme splitting
        # High-volume features generate additional candidates from their keywords/sub-themes
        volume_candidates: List[Dict[str, Any]] = []
        for feature in features:
            aspect_key, metrics = self._feature_metrics(feature)
            if not aspect_key:
                continue
            neg_pct = metrics.get("neg_pct", 0.0)
            comment_count = metrics.get("comment_count", 0)
            # Only split features that already qualify as candidates
            if neg_pct < self.NEGATIVE_CREATE_THRESHOLD or comment_count < max(self.VOLUME_SPLIT_MIN_COMMENTS, effective_min_comments):
                continue
            # Check if this feature already has a candidate
            if not any(c.get("aspect_key") == aspect_key for c in candidates):
                continue
            keywords = self._extract_keywords(feature)
            if len(keywords) < self.VOLUME_SPLIT_MIN_KEYWORDS:
                continue
            # Calculate how many extra items based on volume
            extra_count = min(
                (comment_count - self.VOLUME_SPLIT_MIN_COMMENTS) // self.VOLUME_SPLIT_COMMENTS_PER_ITEM + 1,
                self.VOLUME_SPLIT_MAX_EXTRA,
                len(keywords),  # Can't create more sub-themes than keywords
            )
            if extra_count <= 0:
                continue
            base_priority = self._priority_from_negative(neg_pct)
            for i, keyword in enumerate(keywords[:extra_count]):
                sub_key = f"{aspect_key}:{keyword}"
                candidate_type = "bug" if aspect_key in self.KNOWN_BUG_ASPECT_KEYS else "improvement"
                volume_candidates.append(self._build_candidate(
                    project_id=project_id,
                    analysis_id=analysis_id,
                    taxonomy_id=taxonomy_id,
                    taxonomy_version=taxonomy_version,
                    aspect_key=sub_key,
                    candidate_type=candidate_type,
                    priority=base_priority,
                    reason={
                        "neg_pct": neg_pct,
                        "comment_count": comment_count,
                        "trend": "flat",
                        "confidence_p95": None,
                        "sub_theme": keyword,
                        "parent_aspect": aspect_key,
                    },
                    evidence=[f"Keyword: {keyword}"],
                ))
            if volume_candidates:
                logger.debug(
                    "Volume split produced sub-theme candidates",
                    extra={
                        "aspect_key": aspect_key,
                        "comment_count": comment_count,
                        "extra_count": extra_count,
                    },
                )
        candidates.extend(volume_candidates)

        # Rule Group D — Taxonomy gaps
        unmapped_rate = self._extract_unmapped_rate(analysis)
        if unmapped_rate is not None and unmapped_rate >= self.TAXONOMY_GAP_UNMAPPED_THRESHOLD:
            candidates.append(self._build_candidate(
                project_id=project_id,
                analysis_id=analysis_id,
                taxonomy_id=taxonomy_id,
                taxonomy_version=taxonomy_version,
                aspect_key="__taxonomy__",
                candidate_type="taxonomy_gap",
                priority="P2",
                reason={
                    "unmapped_rate": unmapped_rate,
                    "comment_count": self._total_comment_count(analysis),
                    "trend": "flat",
                    "confidence_p95": None,
                },
                evidence=[],
            ))

        # Rule Group E — Overall-level negative sentiment
        # When overall negative is high but few/no feature candidates, create an overall work item
        overall_neg = self._extract_overall_negative(analysis)
        total_comments = self._total_comment_count(analysis)
        feature_candidate_count = sum(1 for c in candidates if c.get("type") not in ("taxonomy_gap",))
        if not (overall_neg >= self.OVERALL_NEGATIVE_THRESHOLD
                and total_comments >= self.OVERALL_MIN_COMMENTS
                and feature_candidate_count == 0):
            if feature_candidate_count > 0:
                logger.debug("Skipping overall candidate: feature candidates already exist", extra={"feature_candidate_count": feature_candidate_count})
            elif overall_neg < self.OVERALL_NEGATIVE_THRESHOLD:
                logger.debug("Skipping overall candidate: overall negative below threshold", extra={"overall_neg": overall_neg, "threshold": self.OVERALL_NEGATIVE_THRESHOLD})
            elif total_comments < self.OVERALL_MIN_COMMENTS:
                logger.debug("Skipping overall candidate: comment count below minimum", extra={"total_comments": total_comments, "min_comments": self.OVERALL_MIN_COMMENTS})

        if (overall_neg >= self.OVERALL_NEGATIVE_THRESHOLD
                and total_comments >= self.OVERALL_MIN_COMMENTS
                and feature_candidate_count == 0):
            priority = self._priority_from_negative(overall_neg)
            candidates.append(self._build_candidate(
                project_id=project_id,
                analysis_id=analysis_id,
                taxonomy_id=taxonomy_id,
                taxonomy_version=taxonomy_version,
                aspect_key="__overall__",
                candidate_type="improvement",
                priority=priority,
                reason={
                    "neg_pct": overall_neg,
                    "comment_count": total_comments,
                    "trend": "flat",
                    "confidence_p95": None,
                },
                evidence=[],
            ))

        # Rule Group F — Positive/strength features
        # Surfaces features with strong positive sentiment so PMs know what to
        # protect, double-down on, or market.
        for feature in features:
            aspect_key, metrics = self._feature_metrics(feature)
            if not aspect_key:
                continue
            comment_count = metrics.get("comment_count", 0)
            if comment_count < effective_min_comments:
                continue
            # Compute positive ratio from the feature's sentiment
            sentiment = feature.get("sentiment") or {}
            pos_pct = self._to_ratio(sentiment.get("positive"))
            if pos_pct >= 0.60:
                # Don't duplicate if already a negative candidate
                if any(c.get("aspect_key") == aspect_key for c in candidates):
                    continue
                candidates.append(self._build_candidate(
                    project_id=project_id,
                    analysis_id=analysis_id,
                    taxonomy_id=taxonomy_id,
                    taxonomy_version=taxonomy_version,
                    aspect_key=aspect_key,
                    candidate_type="strength",
                    priority="P3",
                    reason={
                        "pos_pct": pos_pct,
                        "comment_count": comment_count,
                        "trend": "flat",
                        "confidence_p95": None,
                    },
                    evidence=self._extract_evidence(feature),
                ))

        # Rule Group C — Trend escalation
        candidates = [self._apply_trend_escalation(c, previous_map) for c in candidates]

        # Deduplicate similar candidates (e.g. "pricing" and "price")
        candidates = self._deduplicate_candidates(candidates)

        logger.info(
            "Candidate generation completed",
            extra={"candidate_count": len(candidates), "feature_count": len(features), "project_id": project_id},
        )
        return candidates

    def _deduplicate_candidates(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge candidates whose aspect keys are near-duplicates (e.g. 'price' vs 'pricing').

        Keeps the candidate with the higher priority (lower P-number).
        Only deduplicates top-level feature candidates — sub-themes (containing ':')
        and special keys like __taxonomy__ are kept as-is.
        """
        special_keys = {"__taxonomy__", "__overall__"}
        # Sub-theme candidates (aspect_key contains ':') are intentional splits, not duplicates
        sub_theme_candidates = [c for c in candidates if ":" in str(c.get("aspect_key", ""))]
        top_level_candidates = [c for c in candidates
                                if c.get("aspect_key") not in special_keys
                                and ":" not in str(c.get("aspect_key", ""))]
        other_candidates = [c for c in candidates if c.get("aspect_key") in special_keys]

        if len(top_level_candidates) <= 1:
            return candidates

        # Group by stem (first 4 chars of aspect_key) as a simple similarity heuristic
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for c in top_level_candidates:
            key = c.get("aspect_key", "")
            stem = key[:4] if len(key) >= 4 else key
            # Also check if one key starts with another (e.g. 'price' vs 'pricing')
            merged = False
            for existing_stem, group in groups.items():
                existing_key = group[0].get("aspect_key", "")
                if key.startswith(existing_key) or existing_key.startswith(key):
                    group.append(c)
                    merged = True
                    break
            if not merged:
                if stem in groups:
                    groups[stem].append(c)
                else:
                    groups[stem] = [c]

        deduplicated = []
        for stem, group in groups.items():
            if len(group) == 1:
                deduplicated.append(group[0])
            else:
                # Keep the one with highest priority (lowest P-number)
                group.sort(key=lambda c: self.PRIORITY_ORDER.index(c.get("priority", "P3"))
                           if c.get("priority") in self.PRIORITY_ORDER else -1, reverse=True)
                winner = group[0]
                merged_keys = [c.get("aspect_key") for c in group[1:]]
                logger.debug(
                    "Deduplicated candidates",
                    extra={"kept_aspect_key": winner.get("aspect_key"), "merged_count": len(merged_keys)},
                )
                deduplicated.append(winner)

        return deduplicated + sub_theme_candidates + other_candidates

    def _apply_trend_escalation(self, candidate: Dict[str, Any], previous_map: Dict[str, float]) -> Dict[str, Any]:
        """Escalate priority if negative sentiment rose >=10% vs previous analysis."""
        aspect_key = candidate.get("aspect_key")
        prev_neg = previous_map.get(aspect_key)
        curr_neg = candidate.get("reason", {}).get("neg_pct")
        if prev_neg is None or curr_neg is None:
            return candidate
        if (curr_neg - prev_neg) >= 0.10:
            candidate = candidate.copy()
            candidate["priority"] = self._escalate_priority(candidate.get("priority"))
        return candidate

    def _build_candidate(
        self,
        project_id: Optional[str],
        analysis_id: Optional[str],
        taxonomy_id: Optional[str],
        taxonomy_version: Optional[int],
        aspect_key: str,
        candidate_type: str,
        priority: str,
        reason: Dict[str, Any],
        evidence: List[str],
    ) -> Dict[str, Any]:
        return {
            "candidate_id": str(uuid.uuid4()),
            "project_id": project_id,
            "analysis_id": analysis_id,
            "taxonomy_id": taxonomy_id,
            "taxonomy_version": taxonomy_version,
            "aspect_key": aspect_key,
            "type": candidate_type,
            "priority": priority,
            "reason": reason,
            "evidence": evidence,
            "created_by": "rules_engine",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def _priority_from_negative(self, neg_pct: float) -> str:
        if neg_pct >= self.NEGATIVE_PRIORITY_P0:
            return "P0"
        if neg_pct >= self.NEGATIVE_PRIORITY_P1:
            return "P1"
        return "P2"

    def _escalate_priority(self, priority: str) -> str:
        if priority not in self.PRIORITY_ORDER:
            return priority
        idx = self.PRIORITY_ORDER.index(priority)
        return self.PRIORITY_ORDER[min(idx + 1, len(self.PRIORITY_ORDER) - 1)]

    def _trend_direction(self, current: Optional[float], previous: Optional[float]) -> str:
        if current is None or previous is None:
            return "flat"
        if (current - previous) >= 0.10:
            return "up"
        if (previous - current) >= 0.10:
            return "down"
        return "flat"

    def _extract_features(self, analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(analysis, dict):
            return []
        for source in (analysis, analysis.get("analysisData"), analysis.get("result")):
            if isinstance(source, dict):
                feats = source.get("features") or source.get("feature_asba") or source.get("featureasba")
                if isinstance(feats, list):
                    return feats
        return []

    def _feature_metrics(self, feature: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
        if not isinstance(feature, dict):
            return None, {}
        name = feature.get("key") or feature.get("name") or feature.get("feature")
        aspect_key = self._normalize_aspect_key(name) if name else None

        sentiment = feature.get("sentiment") or {}
        comment_count = feature.get("comment_count") or feature.get("commentcount") or feature.get("total_mentions") or 0
        try:
            comment_count = int(comment_count)
        except Exception:
            comment_count = 0

        # Calculate negative percentage from sentiment counts
        # sentiment can be either counts (e.g., {"positive": 12, "negative": 3, "neutral": 5})
        # or ratios (e.g., {"positive": 0.6, "negative": 0.15, "neutral": 0.25})
        neg_value = sentiment.get("negative", 0)
        pos_value = sentiment.get("positive", 0)
        neu_value = sentiment.get("neutral", 0)

        # If values are counts (sum >> 1), calculate ratio
        total_sentiment = neg_value + pos_value + neu_value
        if total_sentiment > 1.5:  # Likely counts, not ratios
            neg_pct = neg_value / total_sentiment if total_sentiment > 0 else 0.0
        else:
            # Already a ratio
            neg_pct = self._to_ratio(neg_value)

        return aspect_key, {
            "neg_pct": neg_pct,
            "comment_count": comment_count,
            "confidence_p95": None,
        }

    def _extract_overall_negative(self, analysis: Dict[str, Any]) -> float:
        """Extract the overall negative sentiment percentage (as 0-1 ratio)."""
        if not isinstance(analysis, dict):
            return 0.0
        for source in (analysis, analysis.get("analysisData"), analysis.get("result")):
            if not isinstance(source, dict):
                continue
            overall = source.get("overall")
            if isinstance(overall, dict):
                val = overall.get("negative")
                if val is not None:
                    return self._to_ratio(val)
        return 0.0

    def _extract_unmapped_rate(self, analysis: Dict[str, Any]) -> Optional[float]:
        if not isinstance(analysis, dict):
            return None
        for source in (analysis, analysis.get("analysisData"), analysis.get("result")):
            if not isinstance(source, dict):
                continue
            # Check top-level unmapped_percentage first
            val = source.get("unmapped_percentage")
            if val is not None:
                return float(val)
            # Then check inside pipeline_metadata
            metadata = source.get("pipeline_metadata") or source.get("pipelineMetadata")
            if isinstance(metadata, dict):
                val = metadata.get("unmapped_percentage")
                if val is not None:
                    return float(val)
        return None

    def _total_comment_count(self, analysis: Dict[str, Any]) -> int:
        if not isinstance(analysis, dict):
            return 0
        for source in (analysis, analysis.get("analysisData"), analysis.get("result")):
            if isinstance(source, dict):
                counts = source.get("counts")
                if isinstance(counts, dict) and counts.get("total") is not None:
                    try:
                        return int(counts.get("total"))
                    except Exception:
                        return 0
        return 0

    def _extract_evidence(self, feature: Dict[str, Any]) -> List[str]:
        # Deterministic, text-only evidence (no LLM). Uses keywords if available.
        keywords = feature.get("keywords") if isinstance(feature, dict) else None
        if isinstance(keywords, list) and keywords:
            return [f"Keyword: {k}" for k in keywords[:2]]
        return []

    def _extract_keywords(self, feature: Dict[str, Any]) -> List[str]:
        """Extract keyword strings from a feature for sub-theme splitting."""
        if not isinstance(feature, dict):
            return []
        # Try negative_keywords first (more specific), fall back to keywords
        # Use truthiness check to handle empty lists properly
        raw_keywords = feature.get("negative_keywords")
        if not raw_keywords:  # None or empty list
            raw_keywords = feature.get("keywords")
        if not raw_keywords:  # Still None or empty
            return []

        if isinstance(raw_keywords, list):
            result = []
            for kw in raw_keywords:
                if isinstance(kw, str):
                    result.append(kw.strip().lower())
                elif isinstance(kw, dict):
                    word = kw.get("word") or kw.get("keyword") or kw.get("text") or ""
                    if word:
                        result.append(str(word).strip().lower())
            logger.debug(
                "Extracted keywords from feature",
                extra={
                    "keyword_count": len(result),
                    "source": "negative_keywords" if feature.get("negative_keywords") else "keywords",
                },
            )
            return result
        return []

    def _build_previous_neg_map(self, previous_analysis: Optional[Dict[str, Any]]) -> Dict[str, float]:
        if not isinstance(previous_analysis, dict):
            return {}
        features = self._extract_features(previous_analysis)
        prev_map = {}
        for feature in features:
            aspect_key, metrics = self._feature_metrics(feature)
            if aspect_key:
                prev_map[aspect_key] = metrics.get("neg_pct", 0.0)
        return prev_map

    def _get_analysis_field(self, analysis: Dict[str, Any], field: str) -> Any:
        """Look up a field in the analysis dict or its nested analysisData."""
        if not isinstance(analysis, dict):
            return None
        for source in (analysis, analysis.get("analysisData"), analysis.get("result")):
            if isinstance(source, dict) and source.get(field) is not None:
                return source[field]
        return None

    @staticmethod
    def _normalize_aspect_key(label: str) -> str:
        return str(label).strip().lower().replace(" ", "_")

    @staticmethod
    def _to_ratio(value: Any) -> float:
        if value is None:
            return 0.0
        try:
            v = float(value)
        except Exception:
            return 0.0
        # Accept either 0-1 or 0-100 inputs
        return v / 100.0 if v > 1.0 else v


_candidate_service = None


def get_work_item_candidate_service() -> WorkItemCandidateService:
    """Get the global WorkItemCandidateService instance."""
    global _candidate_service
    if _candidate_service is None:
        _candidate_service = WorkItemCandidateService()
    return _candidate_service
