"""
Comment sampler for work item generation.

Selects the most relevant customer comments for each candidate
so the LLM can generate specific, actionable work items.
"""

from typing import Any, Dict, List
import logging

logger = logging.getLogger(__name__)


def sample_comments_for_candidates(
    comments: List[str],
    candidates: List[Dict[str, Any]],
    max_per_candidate: int = 8,
    max_total: int = 80,
    max_comment_length: int = 300,
) -> Dict[str, List[str]]:
    """Return {candidate_id: [comment_text, ...]} with the most relevant comments per candidate.

    Relevance is determined by simple keyword matching against each candidate's
    aspect_key, evidence keywords, and sub_theme.
    """
    if not comments or not candidates:
        return {}

    # Build keyword sets per candidate
    candidate_keywords: Dict[str, List[str]] = {}
    for c in candidates:
        cid = c.get("candidate_id")
        if not cid:
            continue
        keywords = set()
        # Aspect key (e.g. "pricing", "performance")
        aspect = c.get("aspect_key", "")
        if aspect and not aspect.startswith("__"):
            # Handle sub-theme keys like "pricing:expensive"
            for part in str(aspect).split(":"):
                for word in part.replace("_", " ").split():
                    if len(word) >= 3:
                        keywords.add(word.lower())
        # Evidence keywords
        for ev in c.get("evidence", []):
            if isinstance(ev, str) and ev.startswith("Keyword: "):
                kw = ev.replace("Keyword: ", "").strip().lower()
                if kw:
                    keywords.add(kw)
        # Sub-theme from reason
        sub = (c.get("reason") or {}).get("sub_theme", "")
        if sub:
            for word in str(sub).replace("_", " ").split():
                if len(word) >= 3:
                    keywords.add(word.lower())
        candidate_keywords[cid] = list(keywords)

    if not candidate_keywords:
        return {}

    # Score each comment against each candidate
    scored: Dict[str, List[tuple]] = {cid: [] for cid in candidate_keywords}
    for comment in comments:
        if not isinstance(comment, str) or not comment.strip():
            continue
        comment_lower = comment.lower()
        for cid, keywords in candidate_keywords.items():
            if not keywords:
                continue
            score = sum(1 for kw in keywords if kw in comment_lower)
            if score > 0:
                scored[cid].append((score, comment))

    # Sort by relevance and take top N per candidate
    result: Dict[str, List[str]] = {}
    total_sampled = 0
    for cid in candidate_keywords:
        matches = scored.get(cid, [])
        matches.sort(key=lambda x: x[0], reverse=True)
        selected = [text for _, text in matches[:max_per_candidate]]
        if selected:
            result[cid] = selected
            total_sampled += len(selected)

    # If over global cap, proportionally reduce
    if total_sampled > max_total and result:
        ratio = max_total / total_sampled
        for cid in list(result.keys()):
            allowed = max(1, int(len(result[cid]) * ratio))
            result[cid] = result[cid][:allowed]

    # Truncate individual comments
    for cid in result:
        result[cid] = [
            c[:max_comment_length] + "..." if len(c) > max_comment_length else c
            for c in result[cid]
        ]

    total = sum(len(v) for v in result.values())
    logger.debug(
        "Sampled comments across candidates",
        extra={"sampled_count": total, "candidate_count": len(result), "total_comments": len(comments)},
    )
    return result
